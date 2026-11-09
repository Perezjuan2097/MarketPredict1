"""XGBoost (opcional). Requiere `xgboost` o `xgboost-cpu`; si no está instalado el motor lo omite."""
from ml_engine.models.base import BaseModel


class XGBoostModel(BaseModel):
    model_id = "xgboost"
    name = "XGBoost"
    version = "1.0"
    description = "Árboles potenciados, poco profundos y con regularización, sobre indicadores técnicos."

    def __init__(self, seed=0):
        from xgboost import XGBRegressor  # import perezoso: la app arranca sin la librería
        self.model = XGBRegressor(
            n_estimators=150, max_depth=3, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
            min_child_weight=10, reg_lambda=5.0, random_state=seed, n_jobs=1, verbosity=0,
        )
        self.feature_names = None

    def fit(self, X, y):
        self.feature_names = list(getattr(X, "columns", []))
        self.model.fit(X, y)
        return self

    def predict(self, X):
        return self.model.predict(X)

    def feature_importance(self):
        if not self.feature_names:
            return {}
        pairs = sorted(zip(self.feature_names, self.model.feature_importances_), key=lambda p: -p[1])
        return {k: round(float(v), 4) for k, v in pairs}
