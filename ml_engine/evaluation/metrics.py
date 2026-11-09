"""Métricas de evaluación. Todas operan sobre arreglos alineados (predicho, real)."""
import numpy as np


def mae(actual, pred):
    return float(np.mean(np.abs(np.asarray(actual) - np.asarray(pred))))


def mse(actual, pred):
    return float(np.mean(np.square(np.asarray(actual) - np.asarray(pred))))


def rmse(actual, pred):
    return float(np.sqrt(mse(actual, pred)))


def mape(actual, pred, eps=1e-8):
    """MAPE en %. Excluye observaciones con |real| < eps (denominador nulo o casi nulo).

    Devuelve None si no queda ninguna observación válida.
    """
    a, p = np.asarray(actual, dtype=float), np.asarray(pred, dtype=float)
    mask = np.abs(a) >= eps
    if not mask.any():
        return None
    return float(np.mean(np.abs((a[mask] - p[mask]) / a[mask])) * 100)


def directional_accuracy(actual, pred):
    """Fracción de aciertos en el signo del movimiento. Excluye movimientos reales exactamente nulos."""
    a, p = np.asarray(actual, dtype=float), np.asarray(pred, dtype=float)
    mask = a != 0
    if not mask.any():
        return None
    return float(np.mean(np.sign(a[mask]) == np.sign(p[mask])))


def equity_curve(period_returns):
    return np.cumprod(1.0 + np.asarray(period_returns, dtype=float))


def max_drawdown(period_returns):
    """Máxima caída (valor ≤ 0) de la curva de capital construida con retornos simples por periodo."""
    r = np.asarray(period_returns, dtype=float)
    if r.size == 0:
        return None
    curve = equity_curve(r)
    peak = np.maximum.accumulate(np.concatenate([[1.0], curve]))[1:]
    return float(np.min(curve / peak - 1.0))


def sharpe_ratio(period_returns, periods_per_year, risk_free=0.0):
    """Sharpe anualizado. Devuelve None con menos de 3 periodos o volatilidad nula."""
    r = np.asarray(period_returns, dtype=float)
    if r.size < 3:
        return None
    sd = r.std(ddof=1)
    if sd == 0:
        return None
    return float((r.mean() - risk_free / periods_per_year) / sd * np.sqrt(periods_per_year))
