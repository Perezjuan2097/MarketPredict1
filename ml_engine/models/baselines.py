import numpy as np

from ml_engine.models.base import BaseModel


class LastPriceBaseline(BaseModel):
    """Mantener el último precio conocido (retorno esperado = 0)."""
    model_id = "baseline_last_price"
    name = "Referencia: último precio"
    version = "1.0"
    is_baseline = True

    def fit(self, X, y):
        return self

    def predict(self, X):
        return np.zeros(len(X))


class MeanReturnBaseline(BaseModel):
    """Predecir el retorno medio histórico del periodo de entrenamiento."""
    model_id = "baseline_mean_return"
    name = "Referencia: retorno medio"
    version = "1.0"
    is_baseline = True

    def fit(self, X, y):
        self.mean_ = float(np.mean(y))
        return self

    def predict(self, X):
        return np.full(len(X), self.mean_)
