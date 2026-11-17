"""Motor central: ejecuta todos los modelos, los valida con walk-forward y calcula el consenso."""
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import logging

import numpy as np
import pandas as pd

from ml_engine import explain, probability, risk
from ml_engine.calendar import (
    CONTRACT_VERSION, CONTINUOUS, calendar_timezone, closed_bars, session_close, target_session, utc_instant,
)
from ml_engine.ensemble import consensus
from ml_engine.evaluation import backtest as bt
from ml_engine.features import builder
from ml_engine.features import technical as ta
from ml_engine.model_registry import registry
from ml_engine.models import monte_carlo
from ml_engine.models.base import standard_result

log = logging.getLogger("marketpredict")
MIN_ROWS = 250
BASELINES = ["baseline_last_price", "baseline_mean_return"]


def run_all(data, symbol, horizon=5, seed=0, n_splits=5, context=None, *, calendar=None, cutoff=None, lag_minutes=0):
    if not 1 <= int(horizon) <= 30:
        raise ValueError("El horizonte debe estar entre 1 y 30 sesiones.")
    horizon = int(horizon)
    trading_days = 365 if calendar == CONTINUOUS else 252
    if calendar is not None:
        cutoff = utc_instant(cutoff)
        data = closed_bars(data, calendar, cutoff, lag_minutes=lag_minutes)
    df = builder.to_frame(data)
    if cutoff is None:
        cutoff = pd.Timestamp(df.get("cutoff_at", df["fecha"]).iloc[-1])
    else:
        cutoff = utc_instant(cutoff)
        times = pd.to_datetime(df.get("cutoff_at", df["fecha"]), format="mixed", utc=True)
        df = df.loc[times <= cutoff].reset_index(drop=True)
    if len(df) < MIN_ROWS:
        raise ValueError(f"Datos insuficientes: se requieren al menos {MIN_ROWS} sesiones diarias (hay {len(df)}).")

    X_all, y_all = builder.build(df, horizon, context)
    # The live cutoff can include publications since the last asset close.
    live = df.iloc[[-1]].copy()
    live["cutoff_at"] = cutoff.isoformat()
    ctx_now = builder.context_features(live.reset_index(drop=True), context)
    labeled = X_all.notna().all(axis=1) & y_all.notna()
    X, y = X_all[labeled].reset_index(drop=True), y_all[labeled].reset_index(drop=True)
    close_lab = df["cierre"][labeled].reset_index(drop=True)
    x_now = X_all.iloc[[-1]].copy()
    for column in ctx_now:
        x_now.loc[:, column] = ctx_now[column].fillna(0).iloc[0]
    for prefix in builder.CONTEXT_LABELS:
        x_now.loc[:, f"{prefix}_known"] = float(ctx_now.filter(regex=f"^{prefix}_").notna().any(axis=1).iloc[0])
    if x_now.isna().any().any():
        raise ValueError("No hay suficiente historial para calcular los indicadores más recientes.")
    reference = float(df["cierre"].iloc[-1])
    last_date = df["fecha"].iloc[-1]
    target = target_session(calendar, last_date, horizon) if calendar else None
    next_available = (session_close(calendar, target_session(calendar, last_date, 1))
                      + pd.Timedelta(minutes=lag_minutes)) if calendar else None
    target_close = session_close(calendar, target) if target else None
    if calendar and (next_available <= cutoff or target_close <= cutoff):
        raise ValueError("El cierre de referencia está desactualizado o el objetivo ya pasó.")

    # Entradas por tipo de modelo: indicadores (tabulares) o posición en la serie (ARIMA/SARIMA)
    positions = np.flatnonzero(labeled.to_numpy())
    X_ts = pd.DataFrame({"pos": positions})
    x_now_ts = pd.DataFrame({"pos": [len(df) - 1]})
    log_returns = np.diff(np.log(df["cierre"].to_numpy()))  # log_returns[k] = retorno de la fila k+1
    ts_ids = ("arima", "sarima")

    def make(mid):
        if mid == "random_forest" or mid == "xgboost":
            return registry.create(mid, seed=seed)
        if mid in ts_ids:
            return registry.create(mid, returns=log_returns, horizon=horizon)
        return registry.create(mid)

    def inputs(mid):
        return (X_ts, x_now_ts) if mid in ts_ids else (X, x_now)

    active = registry.active_model_ids()

    # 1) Validación walk-forward de todos los modelos (incluidas referencias). Un modelo opcional que
    #    falle se informa con estado "error" y no tumba al resto.
    holdout_start = len(X) * 4 // 5
    calibration_start = len(X) * 2 // 3
    holdout_fit_end = holdout_start - horizon + 1
    development_end = calibration_start - horizon + 1
    try:
        bt.split_blocks(development_end, horizon, n_splits)
        if holdout_fit_end - calibration_start < 10:
            raise ValueError("Datos insuficientes para calibración independiente.")
        calibration_indices = np.arange(calibration_start, holdout_fit_end)
        holdout_indices = np.arange(holdout_start, len(X))
    except ValueError:
        development_end = len(X)
        calibration_indices = holdout_indices = np.array([], dtype=int)
    scores, errors, oos, holdout_predictions, model_parameters = {}, {}, {}, {}, {}
    for mid in active + BASELINES:
        try:
            Xm, _ = inputs(mid)
            idx, pred = bt.walk_forward(lambda m=mid: make(m), Xm.iloc[:development_end], y.iloc[:development_end],
                                        horizon, n_splits=n_splits)
            scores[mid] = bt.score(idx, pred, y, close_lab, horizon, trading_days)
            oos[mid] = (pred - y.iloc[idx].to_numpy()) ** 2
        except Exception as exc:
            if mid in BASELINES or mid in ("linear_regression", "random_forest"):
                raise
            log.warning("Modelo %s falló en la validación: %s", mid, exc)
            errors[mid] = str(exc)
    base_rmse = [scores[b]["rmse_return"] for b in BASELINES]
    best_base = min(BASELINES, key=lambda b: scores[b]["rmse_return"])

    # 2) Ajuste final con todas las filas etiquetadas y predicción para hoy
    results = []
    frozen_selection = []
    for mid in active + BASELINES:
        model = make(mid) if mid not in errors else None
        if mid in errors:
            stub = registry.CORE.get(mid) or type("Stub", (), {"model_id": mid, "name": mid, "version": "-", "is_baseline": False})
            results.append(standard_result(stub, symbol, reference, horizon, None, {}, builder.INDICATORS_USED,
                                           status="error", message=errors[mid]))
            continue
        metrics = dict(scores[mid])
        if mid in active:
            sk = bt.skill_vs_baseline(metrics["rmse_return"], base_rmse)
            metrics["skill_vs_baseline"] = sk
            t_stat, p_value = bt.M.diebold_mariano(oos[mid], oos[best_base], lag=horizon)
            metrics["dm_t"], metrics["p_value"] = t_stat, p_value
            metrics["significant"] = bool(t_stat is not None and t_stat > bt.M.SIGNIFICANT_T)
            metrics["adds_value_indication"] = bool(sk is not None and sk > 0.01 and metrics["significant"]
                                                    and (metrics["directional_accuracy"] or 0) > 0.5)
        try:
            Xm, xn = inputs(mid)
            interval_rmse = metrics["rmse_return"]
            if len(holdout_indices):
                try:
                    calibration_model = make(mid).fit(Xm.iloc[:development_end], y.iloc[:development_end])
                    calibration_pred = calibration_model.predict(Xm.iloc[calibration_indices])
                    interval_rmse = bt.M.rmse(y.iloc[calibration_indices], calibration_pred)
                    metrics["calibration_rmse_return"] = interval_rmse
                    frozen_selection.append({"model_id": mid, "status": "ok", "is_baseline": model.is_baseline,
                                             "historical_metrics": metrics, "predicted_log_return": 0.0})
                    candidate = make(mid).fit(Xm.iloc[:holdout_fit_end], y.iloc[:holdout_fit_end])
                    holdout_predictions[mid] = np.asarray(candidate.predict(Xm.iloc[holdout_indices]), dtype=float)
                except Exception as exc:
                    log.warning("Validación final no disponible para %s: %s", mid, exc)
            model.fit(Xm, y)
            pred = float(model.predict(xn)[0])
            estimator = getattr(model, "pipe", getattr(model, "model", None))
            if estimator is not None and hasattr(estimator, "get_params"):
                model_parameters[mid] = {key: str(value) if isinstance(value, float) and not np.isfinite(value) else value
                                         for key, value in estimator.get_params().items()
                                         if isinstance(value, (str, int, float, bool, type(None)))}
            if hasattr(model, "order"):
                model_parameters[mid] = {"order": list(model.order), "seasonal_order": list(model.seasonal_order)}
            extra = {"feature_importance": model.feature_importance()} if hasattr(model, "feature_importance") else None
            results.append(standard_result(model, symbol, reference, horizon, pred, metrics,
                                           builder.INDICATORS_USED if mid not in ts_ids else ["Retornos diarios (serie temporal)"],
                                           oos_rmse=interval_rmse, extra=extra))
            if mid in holdout_predictions:
                results[-1]["holdout_metrics"] = bt.score(
                    holdout_indices, holdout_predictions[mid], y, close_lab, horizon, trading_days)
        except Exception as exc:
            results.append(standard_result(model, symbol, reference, horizon, None, metrics, builder.INDICATORS_USED,
                                           status="error", message=str(exc)))

    # 3) Monte Carlo (escenarios, no pronóstico) con su calibración fuera de muestra
    closes = df["cierre"].to_numpy()
    paths = monte_carlo.simulate(closes, horizon, seed=seed)
    mc = monte_carlo.summarize(paths, reference)
    mc["method"] = "bootstrap de retornos diarios (últimos 252)"
    try:
        mc["calibration"] = monte_carlo.calibration(closes, horizon, seed=seed)
    except ValueError as exc:
        mc["calibration"] = {"message": str(exc)}
    mc["assumptions"] = ["Retornos futuros i.i.d. remuestreados del pasado reciente", "Sin cambios de régimen ni saltos",
                         "Es una distribución de escenarios, no una predicción"]

    cons = consensus.compute(results)
    if cons.get("weighted_log_return") is not None:
        z, r = 1.645, cons["weighted_rmse"]
        c = cons["weighted_log_return"]
        cons["estimated_price"] = round(reference * float(np.exp(c)), 6)
        cons["interval"] = {"level": 0.90, "low_price": round(reference * float(np.exp(c - z * r)), 6),
                            "high_price": round(reference * float(np.exp(c + z * r)), 6)}

    # 4) Probabilidad de subida y riesgo (volatilidad).
    try:
        prob = probability.run(X, y, x_now, horizon, seed=seed, n_splits=n_splits,
                               holdout_start=holdout_start if len(holdout_indices) else None)
    except Exception as exc:
        log.warning("Modelo de probabilidad falló: %s", exc)
        prob = {"error": str(exc)}
    try:
        rsk = risk.run(df, horizon, n_splits=n_splits, trading_days=trading_days)
    except Exception as exc:
        log.warning("Modelo de riesgo falló: %s", exc)
        rsk = {"error": str(exc)}
    context_used = [label for name, label in builder.CONTEXT_LABELS.items() if x_now[f"{name}_known"].iloc[0]]
    target_date = target.isoformat() if target else None
    final = {"status": "independent" if len(holdout_indices) else "insufficient_history",
             "n_origins": len(holdout_indices), "calibration_origins": len(calibration_indices),
             "first_origin": df["fecha"].iloc[positions[holdout_start]].date().isoformat() if len(holdout_indices) else None}
    weights = consensus.compute(frozen_selection).get("weights", {})
    weighted_models = [mid for mid, weight in weights.items() if weight > 0]
    if weighted_models and all(mid in holdout_predictions for mid in weighted_models):
        weighted = sum(weights[mid] * holdout_predictions[mid] for mid in weighted_models)
        final["consensus_metrics"] = bt.score(holdout_indices, weighted / sum(weights[mid] for mid in weighted_models),
                                              y, close_lab, horizon, trading_days)
    digest = hashlib.sha256()
    columns = ["fecha", "apertura", "maximo", "minimo", "cierre", "volumen"]
    if "cutoff_at" in df:
        columns.append("cutoff_at")
    for frame in (df[columns], X_all, x_now):
        payload = {"columns": list(frame.columns), "rows": frame.to_numpy().tolist()}
        digest.update(json.dumps(payload, default=str, separators=(",", ":")).encode())
    dependencies = {}
    for name in ("numpy", "pandas", "scikit-learn", "exchange-calendars", "tzdata", "statsmodels", "xgboost", "xgboost-cpu"):
        try:
            dependencies[name] = version(name)
        except PackageNotFoundError:
            continue
    for row in results:
        row["run_date"] = cutoff.isoformat()
    provenance = {"contract_version": CONTRACT_VERSION, "dataset_sha256": digest.hexdigest(),
                  "cutoff_at": cutoff.isoformat(), "origin_at": str(df.get("event_at", df["fecha"]).iloc[-1]),
                  "origin_session": last_date.date().isoformat(),
                  "seed": seed, "horizon": horizon, "n_splits": n_splits, "lag_minutes": lag_minutes,
                  "calendar": calendar, "timezone": calendar_timezone(calendar) if calendar else None,
                  "target_close_at": target_close.isoformat() if target else None,
                  "expires_at": min(next_available, target_close).isoformat() if target else None,
                  "dependencies": dependencies, "model_parameters": model_parameters, "active_models": active,
                  "model_versions": {m["model_id"]: m["version"] for m in results}}
    out = {
        "asset": symbol, "as_of": last_date.date().isoformat(), "target_date": target_date,
        "target_date_approx": target_date, "provenance": provenance, "final_evaluation": final,
        "horizon_days": horizon, "horizon_unit": "días UTC" if calendar == CONTINUOUS else "sesiones",
        "reference_price": round(reference, 6),
        "data_points": int(len(df)), "labeled_points": int(len(X)), "oos_points": scores["linear_regression"]["n_oos"],
        "models": [r for r in results if not r["is_baseline"]], "models_active": active, "baselines": [r for r in results if r["is_baseline"]],
        "monte_carlo": mc, "consensus": cons,
        "probability": prob if "error" not in prob else None, "risk": rsk if "error" not in rsk else None,
        "context_used": context_used,
        "fibonacci_reference": ta.fibonacci_levels(df),
        "notes": [
            "Las métricas históricas de selección y el tramo final se informan por separado.",
            "No hay evaluación final independiente con este historial." if not len(holdout_indices)
            else "El tramo final no interviene en la selección del consenso.",
            "Validación walk-forward cronológica con purga del horizonte: ningún modelo se entrena con información posterior a la fecha de predicción.",
            "Un modelo solo recibe peso si supera a las referencias simples (último precio y retorno medio).",
            "Los rangos individuales usan una aproximación normal basada en su propio error. El rango del consenso usa el promedio ponderado de los errores individuales y su cobertura es desconocida.",
            "Modelos opcionales: " + ", ".join(f"{k} ({'activo' if v['enabled'] else 'inactivo: ' + v['requires']})" for k, v in registry.optional_status().items()) + ". Pendientes: " + ", ".join(registry.PENDING) + ".",
        ],
    }
    out["explanation"] = explain.build(out)
    out["glossary"] = explain.GLOSSARY
    return out
