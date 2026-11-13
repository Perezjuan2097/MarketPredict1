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


def _frame(close, horizon, vix=None):
    r = np.log(close).diff()
    sq = r ** 2
    eps = 1e-6
    f = pd.DataFrame({
        "lv1": np.log(r.abs() + eps),
        "lv5": np.log(np.sqrt(sq.rolling(5, min_periods=5).mean()) + eps),
        "lv22": np.log(np.sqrt(sq.rolling(22, min_periods=22).mean()) + eps),
    })
    if vix is not None and np.isfinite(vix).sum() > 100:
        f["lvix"] = np.log(pd.Series(vix, index=f.index).clip(lower=1) / 100.0 / np.sqrt(252))
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


def run(df, horizon, vix=None, n_splits=5):
    close = df["cierre"].astype(float).reset_index(drop=True)
    f, target, r = _frame(close, horizon, vix)
    if "lvix" in f and f["lvix"].iloc[-1:].isna().any():
        f = f.drop(columns="lvix")
    labeled = f.notna().all(axis=1) & target.notna()
    X, y = f[labeled].reset_index(drop=True), target[labeled].reset_index(drop=True)
    ret_h = (np.log(close).shift(-horizon) - np.log(close))[labeled].reset_index(drop=True)
    x_now = f.iloc[[-1]]
    if x_now.isna().any().any():
        raise ValueError("Historial insuficiente para el modelo de riesgo.")

    scores = {}
    for name, make in (("referencia", _Baseline), ("har", _HAR)):
        idx, pred = bt.walk_forward(make, X, y, horizon, n_splits=n_splits)
        err = pred - y.iloc[idx].to_numpy()
        band = Z90 * np.sqrt(horizon) * np.exp(pred)  # ±90 % en retorno logarítmico
        inside = np.abs(ret_h.iloc[idx].to_numpy()) <= band
        scores[name] = {"rmse_log_vol": float(np.sqrt(np.mean(err ** 2))), "coverage_90": float(inside.mean()),
                        "n_oos": int(len(idx))}
    skill = 1 - scores["har"]["rmse_log_vol"] / scores["referencia"]["rmse_log_vol"]
    use_har = skill > 0.01
    model = (_HAR if use_har else _Baseline)().fit(X, y)
    daily_vol = float(np.exp(model.predict(x_now)[0]))
    h_vol = daily_vol * np.sqrt(horizon)
    ref = float(close.iloc[-1])

    hist = np.sqrt((r ** 2).rolling(22, min_periods=22).mean()).dropna()
    pctl = float((hist < daily_vol).mean()) if len(hist) else None
    level = "Alto" if pctl is not None and pctl >= 0.75 else "Bajo" if pctl is not None and pctl <= 0.25 else "Normal"
    used = scores["har" if use_har else "referencia"]
    return {
        "model": "HAR (volatilidad de 1, 5 y 22 días" + (" + VIX" if "lvix" in X else "") + ")" if use_har else "Volatilidad del último mes",
        "skill_vs_reference": round(skill, 4), "beats_reference": use_har,
        "daily_vol_pct": round(daily_vol * 100, 3), "annual_vol_pct": round(daily_vol * np.sqrt(252) * 100, 2),
        "expected_move_pct": round((np.exp(h_vol) - 1) * 100, 3),
        "range_90": {"low_price": round(ref * np.exp(-Z90 * h_vol), 6), "high_price": round(ref * np.exp(Z90 * h_vol), 6),
                     "historical_coverage": round(used["coverage_90"], 4)},
        "risk_level": level, "vol_percentile": round(pctl, 3) if pctl is not None else None,
        "scores": {k: {kk: round(vv, 5) if isinstance(vv, float) else vv for kk, vv in v.items()} for k, v in scores.items()},
    }
