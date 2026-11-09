from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ml_engine.models.base import BaseModel


class LinearRegressionModel(BaseModel):
    model_id = "linear_regression"
    name = "Regresión lineal (Ridge)"
    version = "2.0"
    description = "Regresión lineal regularizada sobre indicadores técnicos; modelo de referencia."

    def __init__(self, alpha=10.0):
        self.pipe = make_pipeline(StandardScaler(), Ridge(alpha=alpha))

    def fit(self, X, y):
        self.pipe.fit(X, y)
        return self

    def predict(self, X):
        return self.pipe.predict(X)
