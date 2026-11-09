"""Simulación Monte Carlo de trayectorias de precio.

SUPUESTOS (documentados a propósito):
- bootstrap: los retornos diarios futuros se remuestrean con reposición de los últimos `lookback`
  retornos logarítmicos observados (conserva colas pesadas; supone que son independientes e idénticamente
  distribuidos; ignora agrupamiento de volatilidad).
- gbm: movimiento browniano geométrico con media y desviación de esa misma ventana (supone normalidad).
- No incorpora noticias, cambios de régimen ni saltos. Es una distribución de escenarios, no un pronóstico.
"""
import numpy as np


def simulate(close, horizon, n_paths=2000, lookback=252, method="bootstrap", seed=0):
    close = np.asarray(close, dtype=float)
    rets = np.diff(np.log(close))[-lookback:]
    if len(rets) < 30:
        raise ValueError("Datos insuficientes para la simulación Monte Carlo (mínimo 30 retornos).")
    rng = np.random.default_rng(seed)
    if method == "gbm":
        steps = rng.normal(rets.mean(), rets.std(ddof=1), size=(n_paths, horizon))
    elif method == "bootstrap":
        steps = rng.choice(rets, size=(n_paths, horizon), replace=True)
    else:
        raise ValueError("Método no soportado.")
    return close[-1] * np.exp(np.cumsum(steps, axis=1))


def summarize(paths, reference_price, percentiles=(5, 25, 50, 75, 95)):
    final = paths[:, -1]
    pct = {f"p{p}": round(float(np.percentile(final, p)), 6) for p in percentiles}
    ret = final / reference_price - 1
    return {
        "percentiles_price": pct,
        "median_return_pct": round(float(np.median(ret) * 100), 4),
        "mean_return_pct": round(float(ret.mean() * 100), 4),
        "std_return_pct": round(float(ret.std(ddof=1) * 100), 4),
        "prob_gain": round(float((final > reference_price).mean()), 4),
        "prob_loss_gt_10pct": round(float((ret < -0.10).mean()), 4),
        "n_paths": int(paths.shape[0]), "horizon_days": int(paths.shape[1]),
    }


def calibration(close, horizon, n_origins=40, n_paths=800, lookback=252, level=0.90, seed=0):
    """Cobertura fuera de muestra: ¿qué fracción de los resultados reales cae dentro del intervalo simulado?

    Para cada origen i solo se usan precios hasta i (sin datos futuros). Un modelo bien calibrado
    cubre ≈ `level`.
    """
    close = np.asarray(close, dtype=float)
    n = len(close)
    first = max(lookback // 2 + 30, 60)
    last = n - horizon - 1
    n_origins = min(n_origins, max(last - first, 0))
    if n_origins < 10:
        raise ValueError("Datos insuficientes para calibrar la simulación.")
    origins = np.linspace(first, last, n_origins).astype(int)
    lo_q, hi_q = (1 - level) / 2 * 100, (1 + level) / 2 * 100
    hits, med_err = [], []
    for k, i in enumerate(origins):
        final = simulate(close[: i + 1], horizon, n_paths=n_paths, lookback=lookback, seed=seed + k)[:, -1]
        actual = close[i + horizon]
        hits.append(np.percentile(final, lo_q) <= actual <= np.percentile(final, hi_q))
        med_err.append(np.log(np.median(final) / actual))
    return {"nominal_level": level, "empirical_coverage": round(float(np.mean(hits)), 4),
            "n_origins": int(len(origins)), "rmse_median_log": round(float(np.sqrt(np.mean(np.square(med_err)))), 6)}
