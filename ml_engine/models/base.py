"""Interfaz común de modelos y respuesta estandarizada."""
from datetime import datetime, timezone

import numpy as np


class BaseModel:
    model_id = "base"
    name = "Base"
    version = "1.0"
    is_baseline = False
    description = ""

    def fit(self, X, y):
        raise NotImplementedError

    def predict(self, X):
        """Devuelve el retorno logarítmico esperado a `horizon` sesiones para cada fila de X."""
        raise NotImplementedError


def standard_result(model, symbol, reference_price, horizon, predicted_log_return, metrics, indicators,
                    oos_rmse=None, status="ok", message="", extra=None):
    """Respuesta estandarizada que entrega cada modelo (ver documentación del motor)."""
    result = {
        "model_id": model.model_id, "name": model.name, "version": model.version,
        "is_baseline": model.is_baseline, "asset": symbol,
        "run_date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reference_price": round(float(reference_price), 6), "horizon_days": int(horizon),
        "status": status, "message": message, "indicators_used": indicators,
        "historical_metrics": metrics,
    }
    if predicted_log_return is not None and np.isfinite(predicted_log_return):
        est = reference_price * float(np.exp(predicted_log_return))
        result.update(estimated_price=round(est, 6), estimated_return_pct=round((np.exp(predicted_log_return) - 1) * 100, 4),
                      predicted_log_return=float(predicted_log_return))
        if oos_rmse is not None and np.isfinite(oos_rmse):
            z = 1.645  # intervalo ~90 % bajo supuesto normal de los errores fuera de muestra
            result["interval"] = {
                "level": 0.90, "method": "predicción ± 1,645 × RMSE fuera de muestra (supone errores aprox. normales)",
                "low_price": round(reference_price * float(np.exp(predicted_log_return - z * oos_rmse)), 6),
                "high_price": round(reference_price * float(np.exp(predicted_log_return + z * oos_rmse)), 6),
            }
    if extra:
        result.update(extra)
    return result
