"""Validación walk-forward (expansiva) sin fuga de información.

Reglas:
- Orden cronológico estricto; nunca se barajan los datos.
- Para predecir en el instante t (objetivo = retorno a h sesiones), el modelo solo se entrena con filas
  cuyo objetivo ya se conocía en t: filas i ≤ t − h. Esto elimina el solapamiento entre el objetivo de
  entrenamiento y el periodo de prueba (purga).
- Se reajusta el modelo al inicio de cada bloque de prueba, con datos hasta (inicio_bloque − h).
"""
import numpy as np

from ml_engine.evaluation import metrics as M


def split_blocks(n_labeled, horizon, n_splits=5, min_train=120, initial_fraction=0.5):
    """Devuelve lista de (train_end_inclusive, test_start, test_end_exclusive) sobre índices etiquetados."""
    start = max(min_train + horizon, int(n_labeled * initial_fraction))
    if n_labeled - start < n_splits * 5:
        raise ValueError(f"Datos insuficientes para validación walk-forward (filas etiquetadas: {n_labeled}).")
    edges = np.linspace(start, n_labeled, n_splits + 1).astype(int)
    blocks = []
    for a, b in zip(edges[:-1], edges[1:]):
        blocks.append((a - horizon, a, b))  # entrenamiento: filas 0..a-h (inclusive)
    return blocks


def walk_forward(model_factory, X, y, horizon, n_splits=5, min_train=120):
    """Ejecuta la validación. X, y: DataFrame/Series ya sin filas con NaN, en orden cronológico."""
    blocks = split_blocks(len(X), horizon, n_splits, min_train)
    idx, preds = [], []
    for train_end, a, b in blocks:
        if train_end + 1 < min_train:
            raise ValueError("Ventana de entrenamiento insuficiente.")
        model = model_factory()
        model.fit(X.iloc[: train_end + 1], y.iloc[: train_end + 1])
        preds.append(np.asarray(model.predict(X.iloc[a:b]), dtype=float))
        idx.extend(range(a, b))
    return np.array(idx), np.concatenate(preds)


def score(idx, pred_logret, y, close_at, horizon, trading_days=252):
    """Métricas fuera de muestra a partir de predicciones walk-forward.

    close_at: Serie de precios alineada con y (precio en t). El precio real a h sesiones es close_at * exp(y).
    """
    actual = y.iloc[idx].to_numpy()
    base_price = close_at.iloc[idx].to_numpy()
    pred_price = base_price * np.exp(pred_logret)
    real_price = base_price * np.exp(actual)
    # Estrategia simulada: posición larga/corta según el signo de la predicción, con operaciones NO
    # solapadas (cada h sesiones) para no inflar el Sharpe con observaciones superpuestas. Sin costes.
    step = max(horizon, 1)
    strat = (np.sign(pred_logret) * (np.exp(actual) - 1.0))[::step]
    return {
        "n_oos": int(len(idx)),
        "mae_return": M.mae(actual, pred_logret), "mse_return": M.mse(actual, pred_logret),
        "rmse_return": M.rmse(actual, pred_logret),
        "mae_price": M.mae(real_price, pred_price), "rmse_price": M.rmse(real_price, pred_price),
        "mape_price_pct": M.mape(real_price, pred_price),
        "directional_accuracy": M.directional_accuracy(actual, pred_logret),
        "sharpe_strategy": M.sharpe_ratio(strat, trading_days / step),
        "max_drawdown_strategy": M.max_drawdown(strat),
        "n_strategy_trades": int(len(strat)),
    }


def skill_vs_baseline(model_rmse, baseline_rmses):
    """1 − RMSE_modelo / RMSE_mejor_referencia. >0 indica error menor que la mejor referencia simple."""
    best = min(baseline_rmses)
    if best <= 0:
        return None
    return float(1.0 - model_rmse / best)
