"""Construcción de variables y objetivo.

Objetivo: retorno logarítmico a `horizon` sesiones: log(close[t+h] / close[t]).
Variables: solo información disponible en t. Las últimas `horizon` filas no tienen objetivo
(futuro desconocido) y se usan únicamente para predecir.
"""
import numpy as np
import pandas as pd

from ml_engine.calendar import utc_instant
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
    df["fecha"] = pd.to_datetime(df["fecha"], format="mixed", utc=True, errors="coerce")
    if df["fecha"].isna().any() or df["fecha"].duplicated().any() or not df["fecha"].is_monotonic_increasing:
        raise ValueError("Fechas inválidas, duplicadas o desordenadas en la serie.")
    for col in ("apertura", "maximo", "minimo", "cierre", "volumen"):
        df[col] = pd.to_numeric(df[col], errors="coerce") if col in df else np.nan
    if not np.isfinite(df["cierre"]).all() or (df["cierre"] <= 0).any():
        raise ValueError("La serie contiene cierres inválidos.")
    df = df.reset_index(drop=True)
    # Series sin máximo/mínimo/apertura (algunas divisas e índices): se usa el cierre.
    for col in ("apertura", "maximo", "minimo"):
        df[col] = df[col].where(np.isfinite(df[col]) & (df[col] > 0), df["cierre"])
    df["volumen"] = df["volumen"].where(np.isfinite(df["volumen"]) & (df["volumen"] >= 0), 0)
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
    # Fixed schema and causal missingness. A series that starts tomorrow cannot change today's columns.
    ctx = context_features(df, context)
    for prefix in CONTEXT_LABELS:
        columns = ctx.filter(regex=f"^{prefix}_")
        out[f"{prefix}_known"] = columns.notna().any(axis=1).astype(float)
    out = pd.concat([out, ctx.fillna(0.0)], axis=1)
    target = logp.shift(-horizon) - logp
    return out, target


# --- Contexto de mercado: lo que pasa "en el mundo" además del propio activo -------------------
CONTEXT_SERIES = {
    "^VIX": "Miedo del mercado (VIX)",
    "^GSPC": "Mercado de EE. UU. (S&P 500)",
    "^TNX": "Tasa del bono a 10 años de EE. UU.",
    "DX-Y.NYB": "Fortaleza del dólar (DXY)",
}


def context_features(df, context):
    """Join only explicitly timestamped observations known at each origin."""
    cutoffs = pd.to_datetime(df.get("cutoff_at", df["fecha"]), format="mixed", utc=True)
    base = pd.DataFrame({"known_at": cutoffs.astype("datetime64[ns, UTC]")})
    if base["known_at"].isna().any() or not base["known_at"].is_monotonic_increasing:
        raise ValueError("Cortes de predicción inválidos o desordenados.")
    out = pd.DataFrame(index=df.index)
    names = {"^VIX": "vix", "^GSPC": "spx", "^TNX": "tnx", "DX-Y.NYB": "dxy"}
    for sym, name in names.items():
        columns = ([f"{name}_nivel", f"{name}_cambio_5"] if name in ("vix", "tnx")
                   else [f"{name}_ret_5", f"{name}_ret_20"])
        out[columns] = np.nan
        observations = []
        for row in (context or {}).get(sym, ()):
            if row.get("available_at") is None:
                continue
            event = utc_instant(row.get("event_at") or row["fecha"])
            available = utc_instant(row["available_at"])
            published = utc_instant(row["published_at"]) if row.get("published_at") is not None else available
            known_at = max(event, available, published)
            if known_at > base["known_at"].iloc[-1]:
                continue
            close = float(row["cierre"])
            if not np.isfinite(close) or close <= 0:
                raise ValueError(f"Cierre de contexto inválido: {sym}.")
            observations.append((event, known_at, close))
        if not observations:
            continue
        c = pd.DataFrame(observations, columns=["event_at", "known_at", "close"]).sort_values(["known_at", "event_at"])
        # Keep the earliest usable release of each advancing event. Late revisions never rewrite features.
        previous_event = c["event_at"].cummax().shift()
        c = c.loc[previous_event.isna() | (c["event_at"] > previous_event)]
        feats = pd.DataFrame({"known_at": c["known_at"].astype("datetime64[ns, UTC]")})
        lv = np.log(c["close"])
        if name in ("vix", "tnx"):  # niveles con significado propio
            feats[columns[0]] = c["close"] / (100.0 if name == "vix" else 10.0)
            feats[columns[1]] = lv.diff(5)
        else:
            feats[columns[0]] = lv.diff(5)
            feats[columns[1]] = lv.diff(20)
        merged = pd.merge_asof(base, feats, on="known_at", direction="backward")
        out[columns] = merged[columns].to_numpy()
    return out

