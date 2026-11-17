"""Pronóstico de RIESGO: cuánto se puede mover el precio en `horizon` sesiones.

A diferencia de la dirección del precio, la volatilidad sí es bastante predecible (los periodos agitados
tienden a seguir agitados). Modelo HAR (Corsi, 2009): regresión del logaritmo de la volatilidad futura
sobre la volatilidad de ayer, de la última semana y del último mes (+ VIX si está disponible).

Referencia: "la volatilidad del próximo periodo será igual a la del último mes".
Validación: walk-forward con purga, igual que el resto del motor; sin datos posteriores a cada fecha.
"""
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from ml_engine.evaluation import backtest as bt

Z90 = 1.645


def _frame(close, horizon, vix=None, trading_days=252):
    r = np.log(close).diff()
    sq = r ** 2
    eps = 1e-6
    f = pd.DataFrame({
        "lv1": np.log(r.abs() + eps),
        "lv5": np.log(np.sqrt(sq.rolling(5, min_periods=5).mean()) + eps),
        "lv22": np.log(np.sqrt(sq.rolling(22, min_periods=22).mean()) + eps),
    })
    if vix is not None:
        published = pd.Series(vix, index=f.index)
        f["lvix"] = np.log(published.where(np.isfinite(published), 20).clip(lower=1) / 100.0 / np.sqrt(trading_days))
    # Objetivo: volatilidad diaria realizada en las próximas h sesiones (t+1..t+h)
    fut = sq.shift(-1).rolling(horizon, min_periods=horizon).mean().shift(-(horizon - 1))
    target = np.log(np.sqrt(fut) + eps)
    return f, target, r


class _Baseline:
    def fit(self, X, y):
        return self

    def predict(self, X):
        return X["lv22"].to_numpy()


class _HAR:
    def fit(self, X, y):
        self.m = LinearRegression().fit(X, y)
        return self

    def predict(self, X):
        return self.m.predict(X)


def run(df, horizon, vix=None, n_splits=5, *, trading_days=252):
    close = df["cierre"].astype(float).reset_index(drop=True)
    f, target, r = _frame(close, horizon, vix, trading_days)
    labeled = f.notna().all(axis=1) & target.notna()
    X, y = f[labeled].reset_index(drop=True), target[labeled].reset_index(drop=True)
    ret_h = (np.log(close).shift(-horizon) - np.log(close))[labeled].reset_index(drop=True)
    x_now = f.iloc[[-1]]
    if x_now.isna().any().any():
        raise ValueError("Historial insuficiente para el modelo de riesgo.")

    scores, zs = {}, {}
    for name, make in (("referencia", _Baseline), ("har", _HAR)):
        idx, pred = bt.walk_forward(make, X, y, horizon, n_splits=n_splits)
        err = pred - y.iloc[idx].to_numpy()
        z = np.abs(ret_h.iloc[idx].to_numpy()) / (np.sqrt(horizon) * np.exp(pred))
        first_calibration, first_final = len(idx) // 2, len(idx) * 3 // 4
        selection = err[idx + horizon <= idx[first_calibration]]
        calibration = z[(idx >= idx[first_calibration]) & (idx + horizon <= idx[first_final])]
        validation = z[first_final:]
        k = max(float(np.quantile(calibration, 0.90)), Z90) if len(calibration) >= 10 else Z90
        cov_normal = float((validation <= Z90).mean())
        cov_cal = float((validation <= k).mean())
        zs[name] = k
        scores[name] = {"rmse_log_vol": float(np.sqrt(np.mean(selection ** 2))) if len(selection) else None,
                        "coverage_90": cov_cal, "coverage_90_normal": cov_normal, "multiplier": k,
                        "n_oos": int(len(idx)), "n_calibration": len(calibration), "n_final": len(validation)}
    reference_rmse = scores["referencia"]["rmse_log_vol"]
    skill = 1 - scores["har"]["rmse_log_vol"] / reference_rmse if reference_rmse else 0.0
    use_har = skill > 0.01
    model = (_HAR if use_har else _Baseline)().fit(X, y)
    daily_vol = float(np.exp(model.predict(x_now)[0]))
    h_vol = daily_vol * np.sqrt(horizon)
    k = zs["har" if use_har else "referencia"]
    ref = float(close.iloc[-1])

    hist = np.sqrt((r ** 2).rolling(22, min_periods=22).mean()).dropna()
    pctl = float((hist < daily_vol).mean()) if len(hist) else None
    level = "Alto" if pctl is not None and pctl >= 0.75 else "Bajo" if pctl is not None and pctl <= 0.25 else "Normal"
    used = scores["har" if use_har else "referencia"]
    return {
        "model": "HAR (volatilidad de 1, 5 y 22 días" + (" + VIX" if "lvix" in X else "") + ")" if use_har else "Volatilidad del último mes",
        "skill_vs_reference": round(skill, 4), "beats_reference": use_har,
        "daily_vol_pct": round(daily_vol * 100, 3), "annual_vol_pct": round(daily_vol * np.sqrt(trading_days) * 100, 2),
        "expected_move_pct": round((np.exp(h_vol) - 1) * 100, 3),
        "range_90": {"low_price": round(ref * np.exp(-k * h_vol), 6), "high_price": round(ref * np.exp(k * h_vol), 6),
                     "historical_coverage": round(used["coverage_90"], 4),
                     "coverage_if_normal": round(used["coverage_90_normal"], 4), "multiplier": round(k, 3),
                     "method": ("Volatilidad prevista × multiplicador empírico (con mínimo normal)"
                                if used["n_calibration"] >= 10 else
                                "Volatilidad prevista × 1,645 (aproximación normal por muestra insuficiente)")},
        "risk_level": level, "vol_percentile": round(pctl, 3) if pctl is not None else None,
        "scores": {k: {kk: round(vv, 5) if isinstance(vv, float) else vv for kk, vv in v.items()} for k, v in scores.items()},
    }
