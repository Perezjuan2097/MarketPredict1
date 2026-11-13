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


def diebold_mariano(loss_model, loss_reference, lag=1):
    """Prueba de Diebold-Mariano: ¿el modelo tiene MENOS error que la referencia más allá del azar?

    d_t = pérdida_referencia − pérdida_modelo (positiva si el modelo es mejor). La varianza usa
    Newey-West con `lag` rezagos, necesario porque con horizonte h los errores se solapan.
    Devuelve (estadístico t, p-valor unilateral). Con t > 1.96 la mejora es significativa al ~2.5 %.
    """
    from math import erf, sqrt
    d = np.asarray(loss_reference, dtype=float) - np.asarray(loss_model, dtype=float)
    n = d.size
    if n < 30:
        return None, None
    dc = d - d.mean()
    var = float(np.dot(dc, dc) / n)
    for k in range(1, max(int(lag), 0) + 1):
        if k >= n:
            break
        w = 1 - k / (lag + 1)
        var += 2 * w * float(np.dot(dc[k:], dc[:-k]) / n)
    if var <= 0:
        return None, None
    t = float(d.mean() / sqrt(var / n))
    p = 0.5 * (1 - erf(t / sqrt(2)))
    return round(t, 3), round(p, 4)


SIGNIFICANT_T = 1.96  # exige evidencia fuerte (también compensa elegir el mejor entre varios modelos)
