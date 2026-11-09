from sklearn.ensemble import RandomForestRegressor

from ml_engine.models.base import BaseModel


class RandomForestModel(BaseModel):
    model_id = "random_forest"
    name = "Random Forest"
    version = "1.0"
    description = "Bosque aleatorio poco profundo (regularizado) sobre indicadores técnicos."

    def __init__(self, n_estimators=100, max_depth=4, min_samples_leaf=15, seed=0):
        self.model = RandomForestRegressor(n_estimators=n_estimators, max_depth=max_depth,
                                           min_samples_leaf=min_samples_leaf, random_state=seed, n_jobs=1)
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
