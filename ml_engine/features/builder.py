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
CONTEXT_LABELS = {"vix": "VIX (miedo del mercado)", "spx": "S&P 500", "tnx": "Tasa a 10 años", "dxy": "Índice del dólar"}
INDICATORS_USED = ["Retornos pasados (1,5,10,20)", "Volatilidad (10,20)", "RSI(14)", "MACD(12,26,9)",
                   "Bandas de Bollinger(20,2)", "ATR(14)", "Estocástico(14,3)", "SMA(20,50)", "Volumen relativo"]


def to_frame(data):
    df = pd.DataFrame(data).copy()
    if df.empty:
        raise ValueError("Sin datos para construir el modelo.")
    df["fecha"] = pd.to_datetime(df["fecha"], utc=True, errors="coerce").dt.tz_localize(None)
    df = df.dropna(subset=["fecha", "cierre"]).sort_values("fecha").drop_duplicates("fecha")
    for col in ("apertura", "maximo", "minimo", "cierre", "volumen"):
        df[col] = pd.to_numeric(df[col], errors="coerce") if col in df else np.nan
    df = df[df["cierre"] > 0].reset_index(drop=True)
    # Series sin máximo/mínimo/apertura (algunas divisas e índices): se usa el cierre.
    for col in ("apertura", "maximo", "minimo"):
        df[col] = df[col].where(df[col] > 0, df["cierre"])
    df["volumen"] = df["volumen"].fillna(0)
    return df


def build(df, horizon, context=None):
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
    # Índices y divisas no publican volumen (0 o vacío): el volumen relativo queda neutro (1.0)
    # en lugar de NaN, que antes dejaba el activo sin predicción.
    vol = pd.to_numeric(df["volumen"], errors="coerce").astype(float)
    avg = vol.rolling(20, min_periods=20).mean()
    out["vol_ratio"] = (vol / avg.where(avg > 0)).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    # Si faltan máximo/mínimo (algunas series), se aproximan con el cierre para ATR y estocástico.
    out = out.replace([np.inf, -np.inf], np.nan)
    cols = list(FEATURES)
    if context:
        ctx = context_features(df, context)
        for c in ctx.columns:
            out[c] = ctx[c].to_numpy()
        cols += list(ctx.columns)
    target = logp.shift(-horizon) - logp
    return out[cols], target


# --- Contexto de mercado: lo que pasa "en el mundo" además del propio activo -------------------
CONTEXT_SERIES = {
    "^VIX": "Miedo del mercado (VIX)",
    "^GSPC": "Mercado de EE. UU. (S&P 500)",
    "^TNX": "Tasa del bono a 10 años de EE. UU.",
    "DX-Y.NYB": "Fortaleza del dólar (DXY)",
}


def context_features(df, context):
    """Variables de contexto alineadas a la fecha de cada fila usando SOLO el último dato conocido
    (merge_asof hacia atrás: nunca se usa un dato posterior a la fecha del activo)."""
    base = pd.DataFrame({"fecha": df["fecha"].values}).sort_values("fecha")
    out = pd.DataFrame(index=range(len(df)))
    for sym, rows in (context or {}).items():
        if not rows:
            continue
        c = pd.DataFrame(rows)
        if c.empty or "cierre" not in c:
            continue
        c["fecha"] = pd.to_datetime(c["fecha"], utc=True, errors="coerce").dt.tz_localize(None).dt.normalize()
        c = c.dropna(subset=["fecha", "cierre"]).sort_values("fecha").drop_duplicates("fecha")
        c = c[c["cierre"] > 0]
        if len(c) < 60:
            continue
        lv = np.log(c["cierre"].astype(float))
        name = {"^VIX": "vix", "^GSPC": "spx", "^TNX": "tnx", "DX-Y.NYB": "dxy"}.get(sym, sym.lower())
        feats = pd.DataFrame({"fecha": c["fecha"]})
        if name in ("vix", "tnx"):  # niveles con significado propio
            feats[f"{name}_nivel"] = c["cierre"].astype(float) / (100.0 if name == "vix" else 10.0)
            feats[f"{name}_cambio_5"] = lv.diff(5)
        else:
            feats[f"{name}_ret_5"] = lv.diff(5)
            feats[f"{name}_ret_20"] = lv.diff(20)
        merged = pd.merge_asof(base.assign(fecha=base["fecha"].dt.normalize()), feats, on="fecha", direction="backward")
        for col in feats.columns:
            if col != "fecha":
                out[col] = merged[col].to_numpy()
    return out

