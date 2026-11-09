"""Construcción de variables y objetivo.

Objetivo: retorno logarítmico a `horizon` sesiones: log(close[t+h] / close[t]).
Variables: solo información disponible en t. Las últimas `horizon` filas no tienen objetivo
(futuro desconocido) y se usan únicamente para predecir.
"""
import numpy as np
import pandas as pd

from ml_engine.features import technical as ta

FEATURES = [
    "ret_1", "ret_5", "ret_10", "ret_20", "vol_10", "vol_20", "rsi_14", "macd_hist_pct",
    "bb_pct_b", "atr_pct", "stoch_k", "dist_sma20", "dist_sma50", "vol_ratio",
]
INDICATORS_USED = ["Retornos pasados (1,5,10,20)", "Volatilidad (10,20)", "RSI(14)", "MACD(12,26,9)",
                   "Bandas de Bollinger(20,2)", "ATR(14)", "Estocástico(14,3)", "SMA(20,50)", "Volumen relativo"]


def to_frame(data):
    df = pd.DataFrame(data).copy()
    if df.empty:
        raise ValueError("Sin datos para construir el modelo.")
    df["fecha"] = pd.to_datetime(df["fecha"], utc=True, errors="coerce").dt.tz_localize(None)
    df = df.dropna(subset=["fecha", "cierre"]).sort_values("fecha").drop_duplicates("fecha")
    for col in ("apertura", "maximo", "minimo", "cierre", "volumen"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[df["cierre"] > 0].reset_index(drop=True)
    return df


def build(df, horizon):
    close = df["cierre"]
    logp = np.log(close)
    r1 = logp.diff()
    out = pd.DataFrame(index=df.index)
    out["ret_1"] = r1
    for n in (5, 10, 20):
        out[f"ret_{n}"] = logp.diff(n)
    out["vol_10"] = r1.rolling(10, min_periods=10).std()
    out["vol_20"] = r1.rolling(20, min_periods=20).std()
    out["rsi_14"] = ta.rsi(close) / 100.0
    out["macd_hist_pct"] = ta.macd(close)["hist"] / close
    out["bb_pct_b"] = ta.bollinger(close)["pct_b"]
    out["atr_pct"] = ta.atr(df) / close
    out["stoch_k"] = ta.stochastic(df)["k"] / 100.0
    out["dist_sma20"] = close / ta.sma(close, 20) - 1
    out["dist_sma50"] = close / ta.sma(close, 50) - 1
    vol = df["volumen"].astype(float)
    avg = vol.rolling(20, min_periods=20).mean()
    out["vol_ratio"] = (vol / avg.where(avg > 0))
    out = out.replace([np.inf, -np.inf], np.nan)
    target = logp.shift(-horizon) - logp
    return out[FEATURES], target
