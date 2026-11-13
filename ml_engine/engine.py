"""Motor central: ejecuta todos los modelos, los valida con walk-forward y calcula el consenso."""
from datetime import timezone

import logging

import numpy as np
import pandas as pd

from ml_engine import explain, probability, risk
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


def run_all(data, symbol, horizon=5, seed=0, n_splits=5, context=None):
    if not 1 <= int(horizon) <= 30:
        raise ValueError("El horizonte debe estar entre 1 y 30 sesiones.")
    horizon = int(horizon)
    df = builder.to_frame(data)
    if len(df) < MIN_ROWS:
        raise ValueError(f"Datos insuficientes: se requieren al menos {MIN_ROWS} sesiones diarias (hay {len(df)}).")

    X_all, y_all = builder.build(df, horizon, context)
    # Una variable de contexto sin dato hoy (p. ej. serie que dejó de publicarse) se descarta entera.
    X_all = X_all.drop(columns=[c for c in X_all.columns if c not in builder.FEATURES and pd.isna(X_all[c].iloc[-1])])
    labeled = X_all.notna().all(axis=1) & y_all.notna()
    X, y = X_all[labeled].reset_index(drop=True), y_all[labeled].reset_index(drop=True)
    close_lab = df["cierre"][labeled].reset_index(drop=True)
    x_now = X_all.iloc[[-1]]
    if x_now.isna().any().any():
        raise ValueError("No hay suficiente historial para calcular los indicadores más recientes.")
    reference = float(df["cierre"].iloc[-1])
    last_date = df["fecha"].iloc[-1]

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
    scores, errors, oos = {}, {}, {}
    for mid in active + BASELINES:
        try:
            Xm, _ = inputs(mid)
            idx, pred = bt.walk_forward(lambda m=mid: make(m), Xm, y, horizon, n_splits=n_splits)
            scores[mid] = bt.score(idx, pred, y, close_lab, horizon)
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
            model.fit(Xm, y)
            pred = float(model.predict(xn)[0])
            extra = {"feature_importance": model.feature_importance()} if hasattr(model, "feature_importance") else None
            results.append(standard_result(model, symbol, reference, horizon, pred, metrics,
                                           builder.INDICATORS_USED if mid not in ts_ids else ["Retornos diarios (serie temporal)"],
                                           oos_rmse=metrics["rmse_return"], extra=extra))
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

    # 4) Probabilidad de subida (calibrada) y riesgo (volatilidad): lo más útil para decidir
    try:
        prob = probability.run(X, y, x_now, horizon, seed=seed, n_splits=n_splits)
    except Exception as exc:
        log.warning("Modelo de probabilidad falló: %s", exc)
        prob = {"error": str(exc)}
    try:
        vix = (X_all["vix_nivel"] * 100).to_numpy() if "vix_nivel" in X_all else None
        rsk = risk.run(df, horizon, vix=vix, n_splits=n_splits)
    except Exception as exc:
        log.warning("Modelo de riesgo falló: %s", exc)
        rsk = {"error": str(exc)}
    context_used = [builder.CONTEXT_LABELS.get(c.split("_")[0], c) for c in X.columns if c not in builder.FEATURES]
    context_used = list(dict.fromkeys(context_used))

    target_date = (last_date + pd.tseries.offsets.BDay(horizon)).date().isoformat()  # aproximado: no conoce festivos
    out = {
        "asset": symbol, "as_of": last_date.date().isoformat(), "target_date_approx": target_date,
        "horizon_days": horizon, "horizon_unit": "sesiones bursátiles", "reference_price": round(reference, 6),
        "data_points": int(len(df)), "labeled_points": int(len(X)), "oos_points": scores["linear_regression"]["n_oos"],
        "models": [r for r in results if not r["is_baseline"]], "models_active": active, "baselines": [r for r in results if r["is_baseline"]],
        "monte_carlo": mc, "consensus": cons,
        "probability": prob if "error" not in prob else None, "risk": rsk if "error" not in rsk else None,
        "context_used": context_used,
        "fibonacci_reference": ta.fibonacci_levels(df),
        "notes": [
            "Estimaciones estadísticas con error medido fuera de muestra; no son certezas ni recomendaciones de inversión.",
            "Validación walk-forward cronológica con purga del horizonte: ningún modelo se entrena con información posterior a la fecha de predicción.",
            "Un modelo solo recibe peso si supera a las referencias simples (último precio y retorno medio).",
            "Los intervalos suponen errores aproximadamente normales; en mercados reales las colas son más pesadas.",
            "Modelos opcionales: " + ", ".join(f"{k} ({'activo' if v['enabled'] else 'inactivo: ' + v['requires']})" for k, v in registry.optional_status().items()) + ". Pendientes: " + ", ".join(registry.PENDING) + ".",
        ],
    }
    out["explanation"] = explain.build(out)
    out["glossary"] = explain.GLOSSARY
    return out
