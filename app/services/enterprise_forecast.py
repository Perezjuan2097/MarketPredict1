"""Deterministic enterprise forecasts on complete, authorized daily series."""
from datetime import date, timedelta
from hashlib import sha256
from importlib.metadata import version as package_version
import json
import math

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge

from app.security import InvalidInput

ENGINE_VERSION = "enterprise-daily-v1"
MAX_DAYS = 730
MAX_HORIZON = 14
MIN_TRAIN = 28
ORIGINS = 16
SELECTION = 4
CALIBRATION = 8
INTERVAL_LEVEL = 0.80
SEED = 0


def parse_day(value):
    try:
        day = date.fromisoformat(value)
        if day.isoformat() == value:
            return day
    except (TypeError, ValueError):
        pass
    raise InvalidInput("Usa fechas AAAA-MM-DD.")


def prepare(rows, start, through, zero_dates):
    """Rows are scoped and summed by day in SQL. Missing dates are never inferred as zero."""
    if through < start or (through - start).days >= MAX_DAYS or through > date.max - timedelta(days=MAX_HORIZON):
        raise InvalidInput(f"La ventana debe contener entre 1 y {MAX_DAYS} días y permitir el horizonte.")
    source = {day: (int(value), int(count), int(negative)) for day, value, count, negative in rows}
    if not source:
        raise InvalidInput("La selección no contiene observaciones.")
    dates = [start + timedelta(days=i) for i in range((through - start).days + 1)]
    zero = set(zero_dates)
    if not zero <= set(dates) or zero.intersection(source):
        raise InvalidInput("Declara como cero sólo fechas sin registros dentro de la ventana.")
    missing = [day.isoformat() for day in dates if day not in source and day not in zero]
    if missing:
        raise InvalidInput(f"Hay {len(missing)} días sin información y posible cambio de granularidad. "
                           f"Primeras fechas: {', '.join(missing[:5])}. Corrige el rango o declara los ceros conocidos.")
    values = [source.get(day, (0, 0, 0))[0] for day in dates]
    data = np.asarray(values, dtype=float) / 10000
    # Diagnostics only. They never change the data, candidate selection, or validation folds.
    med = float(np.median(data))
    deviations = np.abs(data - med)
    mad = float(np.median(deviations))
    outliers = deviations > (6 * 1.4826 * mad) if mad > 0 else deviations > 0
    digest = sha256(json.dumps([(day.isoformat(), value) for day, value in zip(dates, values)],
                               separators=(",", ":")).encode()).hexdigest()
    return dates, data, {
        "series_sha256": digest, "observations": len(dates), "zero_days_declared": len(zero),
        "missing_days": 0, "negative_source_rows": sum(row[2] for row in source.values()),
        "outlier_days": int(np.count_nonzero(outliers)), "outlier_rule": "MAD x 6; deviations from median if MAD=0",
        "frequency": "calendar_day", "transformations": [
            "sum_scoped_rows_by_day", "retain_negative_values", "explicit_zero_fill", "exclude_after_complete_through",
        ],
    }


def _features(history, position):
    return [position / 365, math.sin(2 * math.pi * position / 7), math.cos(2 * math.pi * position / 7),
            history[-1], history[-7], float(np.mean(history[-7:]))]


def _predict(model, train, h):
    if model == "naive":
        return np.repeat(train[-1], h)
    if model == "seasonal_naive":
        return np.resize(train[-7:], h)
    if model == "moving_average":
        return np.repeat(np.mean(train[-7:]), h)
    if model == "exponential":
        level = train[0]
        for value in train[1:]:
            level = 0.25 * value + 0.75 * level
        return np.repeat(level, h)
    if model == "arima":
        from statsmodels.tsa.arima.model import ARIMA
        fitted = ARIMA(train, order=(1, 0, 0)).fit(method_kwargs={"maxiter": 35})
        if not fitted.mle_retvals.get("converged"):
            raise ValueError("ARIMA convergence")
        return np.asarray(fitted.forecast(h))
    if model == "ridge":
        estimator = Ridge(alpha=10)
    else:
        estimator = RandomForestRegressor(n_estimators=32, max_depth=4, min_samples_leaf=4, n_jobs=1,
                                           random_state=SEED)
    # Standardize lag scales using this training prefix only.
    x = np.asarray([_features(train[:i], i) for i in range(7, len(train))])
    center = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale == 0] = 1
    estimator.fit((x - center) / scale, train[7:])
    history = list(train)
    for _ in range(h):
        features = np.asarray(_features(history, len(history)))
        history.append(float(estimator.predict([(features - center) / scale])[0]))
    return np.asarray(history[-h:])


def _metrics(actual, forecast):
    error = forecast - actual
    absolute = np.abs(error)
    total = float(np.abs(actual).sum())
    return {"mae": float(absolute.mean()), "rmse": float(np.sqrt(np.mean(error**2))),
            "wape": float(absolute.sum() / total) if total else None, "bias": float(error.mean()),
            "observations": len(actual)}


def _checked_forecast(model, train, horizon):
    result = _predict(model, train, horizon)
    if result.shape != (horizon,) or not np.isfinite(result).all():
        raise ValueError("invalid forecast")
    return result


def evaluate(dates, data, horizon, *, arima=False, scenario_pct=None):
    first = len(data) - ORIGINS * horizon
    if first < MIN_TRAIN:
        raise InvalidInput(f"Se requieren {MIN_TRAIN + ORIGINS * horizon} días completos para este horizonte.")
    if not np.isfinite(data).all() or not np.any(data):
        raise InvalidInput("La serie debe contener observaciones válidas y actividad neta distinta de cero.")
    choices = ["naive", "seasonal_naive", "moving_average", "exponential", "ridge"]
    if first >= 112:
        choices.append("random_forest")
    if arima and first >= 168 and np.std(data[:first]) > 0:
        choices.append("arima")
    origins = list(range(first, len(data), horizon))
    actual = np.concatenate([data[t:t + horizon] for t in origins])
    selection_end, calibration_end = SELECTION * horizon, (SELECTION + CALIBRATION) * horizon
    predictions, failed = {}, []
    recoverable = (ArithmeticError, ValueError, RuntimeError, ImportError, np.linalg.LinAlgError)

    def forecast_prefixes(model, checkpoints):
        return np.concatenate([_checked_forecast(model, data[:t], horizon) for t in checkpoints])

    for model in choices:
        try:
            predictions[model] = forecast_prefixes(model, origins[:SELECTION])
        except recoverable:
            if model in ("naive", "seasonal_naive"):
                raise
            failed.append({"model": model, "stage": "selection"})
    selection = {model: _metrics(actual[:selection_end], values) for model, values in predictions.items()}
    baseline = min(("naive", "seasonal_naive"), key=lambda model: selection[model]["mae"])
    winner = min(selection, key=lambda model: selection[model]["mae"])
    ref_mae = selection[baseline]["mae"]
    if not ref_mae or selection[winner]["mae"] >= 0.95 * ref_mae:
        winner = baseline

    # Neither validation failures nor scores from later folds can change the winner.
    final_actual = actual[calibration_end:]
    scores = {model: {"selection": selection[model], "final": None} for model in selection}
    try:
        calibration_fit = forecast_prefixes(winner, origins[SELECTION:SELECTION + CALIBRATION])
        live = _checked_forecast(winner, data, horizon)
    except recoverable:
        raise InvalidInput("El modelo elegido no pudo ajustarse o calibrarse.") from None
    coverage = None
    calibration = np.sort(np.abs(actual[selection_end:calibration_end] - calibration_fit))
    rank = min(len(calibration), math.ceil((len(calibration) + 1) * INTERVAL_LEVEL))
    band = float(calibration[rank - 1])
    for model in scores:
        try:
            final_values = forecast_prefixes(model, origins[SELECTION + CALIBRATION:])
            scores[model]["final"] = _metrics(final_actual, final_values)
            if model == winner:
                coverage = float(np.mean(np.abs(final_actual - final_values) <= band))
        except recoverable:
            failed.append({"model": model, "stage": "final"})
    beats_baseline = bool(winner != baseline and scores[winner]["final"] and
                          scores[winner]["final"]["mae"] < scores[baseline]["final"]["mae"])
    forecasts = [{"date": (dates[-1] + timedelta(days=i)).isoformat(), "value": float(point),
                  "low": float(point) - band, "high": float(point) + band}
                 for i, point in enumerate(live, 1)]
    scenario = None
    if scenario_pct is not None:
        scenario = {"kind": "hypothetical", "assumption": "multiplicative_change_pct", "pct": scenario_pct,
                    "points": [{"date": p["date"], "value": p["value"] * (1 + scenario_pct / 100)} for p in forecasts]}
    return {
        "engine_version": ENGINE_VERSION, "seed": SEED,
        "dependencies": {name: package_version(name) for name in
                         (["numpy", "scikit-learn", "statsmodels"] if "arima" in choices else ["numpy", "scikit-learn"])},
        "parameters": {"weekly_period": 7, "moving_window": 7, "exponential_alpha": 0.25,
                       "ridge_alpha": 10, "forest_estimators": 32, "forest_depth": 4, "forest_min_leaf": 4,
                       "arima_order": [1, 0, 0], "arima_maxiter": 35, "min_training_days": MIN_TRAIN,
                       "selection_min_relative_gain": 0.05, "candidates": choices}, "baseline": baseline, "selected_model": winner,
        "beats_baseline_on_final": beats_baseline, "models": scores, "failed_candidates": failed,
        "interval": {"nominal_level": INTERVAL_LEVEL, "coverage_final": coverage, "calibration_points": len(calibration),
                     "final_points": len(final_actual), "method": "pooled_absolute_residual_quantile"},
        "evaluation": {"selection_origins": [dates[t].isoformat() for t in origins[:SELECTION]],
                       "calibration_origins": [dates[t].isoformat() for t in origins[SELECTION:SELECTION + CALIBRATION]],
                       "final_origins": [dates[t].isoformat() for t in origins[SELECTION + CALIBRATION:]],
                       "origin_dates_are_first_test_days": True},
        "forecast": forecasts, "scenario": scenario,
        "history": [{"date": day.isoformat(), "value": float(value)} for day, value in zip(dates[-120:], data[-120:])],
    }
