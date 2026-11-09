"""Indicadores técnicos (pandas/numpy). Cada valor en t usa solo datos hasta t (sin mirar al futuro).

Se espera un DataFrame con columnas: apertura, maximo, minimo, cierre, volumen.
"""
import numpy as np
import pandas as pd


def sma(s, n):
    return s.rolling(n, min_periods=n).mean()


def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close, n=14):
    """RSI de Wilder."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0).where(gain.notna())


def macd(close, fast=12, slow=26, signal=9):
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger(close, n=20, k=2.0):
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    pct_b = (close - lower) / (upper - lower).replace(0, np.nan)
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower, "pct_b": pct_b})


def atr(df, n=14):
    prev = df["cierre"].shift(1)
    tr = pd.concat([df["maximo"] - df["minimo"], (df["maximo"] - prev).abs(), (df["minimo"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def adx(df, n=14):
    up = df["maximo"].diff()
    down = -df["minimo"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    a = atr(df, n)
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / a
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def stochastic(df, k=14, d=3):
    low = df["minimo"].rolling(k, min_periods=k).min()
    high = df["maximo"].rolling(k, min_periods=k).max()
    pct_k = 100 * (df["cierre"] - low) / (high - low).replace(0, np.nan)
    return pd.DataFrame({"k": pct_k, "d": pct_k.rolling(d, min_periods=d).mean()})


def obv(df):
    direction = np.sign(df["cierre"].diff()).fillna(0)
    return (direction * df["volumen"]).cumsum()


def fibonacci_levels(df, lookback=120):
    """Retrocesos de Fibonacci sobre el último rango. Herramienta INTERPRETATIVA: no predice precios."""
    window = df.tail(lookback)
    hi, lo = float(window["maximo"].max()), float(window["minimo"].min())
    span = hi - lo
    levels = {f"{r:g}": round(hi - span * r, 6) for r in (0.236, 0.382, 0.5, 0.618, 0.786)}
    levels.update(maximo=hi, minimo=lo)
    return levels
