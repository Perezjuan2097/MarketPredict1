"""Registro de modelos. Los opcionales se importan de forma perezosa y se omiten si falta su librería."""
import importlib.util
import os

from ml_engine.models.baselines import LastPriceBaseline, MeanReturnBaseline
from ml_engine.models.linear_regression import LinearRegressionModel
from ml_engine.models.random_forest import RandomForestModel

CORE = {cls.model_id: cls for cls in (LinearRegressionModel, RandomForestModel, LastPriceBaseline, MeanReturnBaseline)}

# model_id -> (módulo requerido, descripción del requisito)
OPTIONAL = {
    "xgboost": ("xgboost", "xgboost o xgboost-cpu"),
    "arima": ("statsmodels", "statsmodels"),
    "sarima": ("statsmodels", "statsmodels"),
}
PENDING = {"lstm": "requiere TensorFlow o PyTorch (no incluido)", "prophet": "opcional; no incluido"}


def lib_available(module):
    return importlib.util.find_spec(module) is not None


def ts_models_enabled():
    """ARIMA/SARIMA son más lentos; se activan con ENABLE_TS_MODELS=true (tras verificar en su entorno)."""
    return os.getenv("ENABLE_TS_MODELS", "false").strip().lower() in {"1", "true", "yes", "on"}


def optional_status():
    out = {}
    for mid, (module, req) in OPTIONAL.items():
        installed = lib_available(module)
        enabled = installed and (ts_models_enabled() if mid in ("arima", "sarima") else True)
        out[mid] = {"installed": installed, "enabled": enabled, "requires": req}
    return out


def active_model_ids():
    ids = ["linear_regression", "random_forest"]
    ids += [m for m, st in optional_status().items() if st["enabled"]]
    return ids


def create(model_id, **kwargs):
    if model_id in CORE:
        return CORE[model_id](**kwargs)
    if model_id == "xgboost":
        from ml_engine.models.xgboost_model import XGBoostModel
        return XGBoostModel(**kwargs)
    if model_id in ("arima", "sarima"):
        from ml_engine.models import arima
        return (arima.ARIMAModel if model_id == "arima" else arima.SARIMAModel)(**kwargs)
    raise KeyError(f"Modelo no registrado: {model_id}")
