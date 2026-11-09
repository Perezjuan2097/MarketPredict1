"""ARIMA y SARIMA (opcionales; requieren `statsmodels`).

Modelan los retornos logarítmicos DIARIOS y suman el pronóstico de los próximos `horizon` días para
obtener el retorno acumulado. Se escalan ×100 para estabilidad numérica.

Encaje con la validación walk-forward: el modelo recibe como "X" un DataFrame con la columna `pos`
(posición de la fila en la serie). `fit` estima parámetros solo con retornos hasta la última fila de
entrenamiento; `predict` filtra (sin reestimar) los retornos disponibles hasta cada origen de prueba, de modo
que nunca usa información posterior al origen.
"""
import warnings

import numpy as np

from ml_engine.models.base import BaseModel

SCALE = 100.0


class ARIMAModel(BaseModel):
    model_id = "arima"
    name = "ARIMA(1,0,1)"
    version = "1.0"
    description = "Dependencia temporal lineal en los retornos diarios."
    order = (1, 0, 1)
    seasonal_order = (0, 0, 0, 0)

    def __init__(self, returns, horizon):
        from statsmodels.tsa.arima.model import ARIMA  # import perezoso
        self._ARIMA = ARIMA
        self.rets = np.asarray(returns, dtype=float) * SCALE  # rets[k] = retorno de la fila k+1
        self.horizon = int(horizon)
        self.res = None
        self._cache = {}

    def fit(self, X, y):
        last = int(X["pos"].iloc[-1])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = self._ARIMA(self.rets[:last], order=self.order, seasonal_order=self.seasonal_order, trend="c")
            self.res = model.fit()
        self._cache = {}
        return self

    def _forecast_from(self, pos):
        if pos not in self._cache:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                filtered = self.res.apply(self.rets[:pos], refit=False)
                self._cache[pos] = float(np.sum(filtered.forecast(self.horizon))) / SCALE
        return self._cache[pos]

    def predict(self, X):
        return np.array([self._forecast_from(int(p)) for p in X["pos"]])


class SARIMAModel(ARIMAModel):
    model_id = "sarima"
    name = "SARIMA(1,0,1)(1,0,1,5)"
    version = "1.0"
    description = "ARIMA con componente estacional semanal (5 sesiones)."
    seasonal_order = (1, 0, 1, 5)
