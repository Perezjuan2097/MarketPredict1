"""Pruebas de componentes opcionales. Se omiten solas si falta la librería; ejecútelas en su entorno completo."""
import importlib.util
import os
import unittest

import numpy as np
import pandas as pd

from ml_engine.engine import run_all
from ml_engine.model_registry import registry
from tests.test_ml_engine import make_data

HAS_XGB = importlib.util.find_spec("xgboost") is not None
HAS_SM = importlib.util.find_spec("statsmodels") is not None
HAS_SA = importlib.util.find_spec("sqlalchemy") is not None


class TestDegradacion(unittest.TestCase):
    def test_modelo_opcional_ausente_no_tumba_el_motor(self):
        from unittest.mock import patch
        ids = ["linear_regression", "random_forest", "xgboost"]
        with patch.object(registry, "active_model_ids", return_value=ids):
            r = run_all(make_data(), "SIM", 5)
        xgb = next(m for m in r["models"] if m["model_id"] == "xgboost")
        self.assertEqual(xgb["status"], "ok" if HAS_XGB else "error")
        self.assertEqual(sum(1 for m in r["models"] if m["status"] == "ok"), 3 if HAS_XGB else 2)
        self.assertNotIn("xgboost", [k for k, v in r["consensus"]["weights"].items()] if not HAS_XGB else [])

    def test_estado_opcional(self):
        st = registry.optional_status()
        self.assertEqual(set(st), {"xgboost", "arima", "sarima"})
        self.assertEqual(st["xgboost"]["installed"], HAS_XGB)


@unittest.skipUnless(HAS_XGB, "xgboost no instalado")
class TestXGBoost(unittest.TestCase):
    def test_en_el_motor(self):
        r = run_all(make_data(), "SIM", 5)
        m = next(m for m in r["models"] if m["model_id"] == "xgboost")
        self.assertEqual(m["status"], "ok")
        self.assertIn("feature_importance", m)
        self.assertIn("xgboost", r["consensus"]["weights"])


@unittest.skipUnless(HAS_SM, "statsmodels no instalado")
class TestSeriesTemporales(unittest.TestCase):
    def setUp(self):
        self.prev = os.environ.get("ENABLE_TS_MODELS")
        os.environ["ENABLE_TS_MODELS"] = "true"

    def tearDown(self):
        os.environ.pop("ENABLE_TS_MODELS", None) if self.prev is None else os.environ.__setitem__("ENABLE_TS_MODELS", self.prev)

    def test_en_el_motor(self):
        r = run_all(make_data(), "SIM", 5)
        ids = {m["model_id"]: m for m in r["models"]}
        for mid in ("arima", "sarima"):
            self.assertEqual(ids[mid]["status"], "ok", ids[mid].get("message"))
            self.assertIn("interval", ids[mid])

    def test_sin_fuga_de_datos(self):
        """El pronóstico en el origen p no puede cambiar si se alteran los retornos posteriores a p."""
        rng = np.random.default_rng(0)
        rets = rng.normal(0, 0.01, 400)
        X = pd.DataFrame({"pos": np.arange(100, 250)})
        y = pd.Series(np.zeros(150))
        a = registry.create("arima", returns=rets, horizon=5).fit(X, y)
        pa = a.predict(pd.DataFrame({"pos": [260]}))
        rets2 = rets.copy()
        rets2[260:] = rng.normal(0, 0.05, len(rets2) - 260)  # futuro distinto
        b = registry.create("arima", returns=rets2, horizon=5).fit(X, y)
        np.testing.assert_allclose(pa, b.predict(pd.DataFrame({"pos": [260]})))


@unittest.skipUnless(HAS_SA, "sqlalchemy no instalado")
class TestEsquemaORM(unittest.TestCase):
    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from app.models.orm import Base
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.s = Session(self.engine)
        from app.models import orm
        self.o = orm
        self.u1 = orm.User(username="ana", email="a@x.co", password_hash="h")
        self.u2 = orm.User(username="beto", email="b@x.co", password_hash="h")
        self.asset = orm.Asset(symbol="AAPL", name="Apple")
        self.s.add_all([self.u1, self.u2, self.asset]); self.s.commit()

    def tearDown(self):
        self.s.close()

    def _fails(self, obj):
        from sqlalchemy.exc import IntegrityError
        self.s.add(obj)
        with self.assertRaises(IntegrityError):
            self.s.commit()
        self.s.rollback()

    def test_tablas_requeridas(self):
        from app.models.orm import Base
        for t in ("users", "assets", "model_runs", "predictions", "prediction_evaluations", "portfolios", "trades",
                  "watchlist_items", "alerts", "notifications", "posts", "comments", "reactions", "votes", "follows",
                  "reports", "audit_log", "data_providers"):
            self.assertIn(t, Base.metadata.tables)

    def test_voto_unico_por_usuario(self):
        post = self.o.Post(user_id=self.u1.id, body="hola"); self.s.add(post); self.s.commit()
        self.s.add(self.o.Vote(post_id=post.id, user_id=self.u2.id, direction="alcista")); self.s.commit()
        self._fails(self.o.Vote(post_id=post.id, user_id=self.u2.id, direction="bajista"))
        self._fails(self.o.Vote(post_id=post.id, user_id=self.u1.id, direction="loquesea"))

    def test_no_seguirse_a_si_mismo_ni_duplicar(self):
        self._fails(self.o.Follow(follower_id=self.u1.id, followed_id=self.u1.id))
        self.s.add(self.o.Follow(follower_id=self.u1.id, followed_id=self.u2.id)); self.s.commit()
        self._fails(self.o.Follow(follower_id=self.u1.id, followed_id=self.u2.id))

    def test_prediccion_de_usuario_exige_usuario_y_estado_valido(self):
        from datetime import date
        base = dict(asset_id=self.asset.id, direction="alcista", reference_price=100, horizon_days=5, target_date=date(2026, 12, 1))
        self._fails(self.o.Prediction(source="user", **base))                      # sin user_id
        self._fails(self.o.Prediction(source="user", user_id=self.u1.id, status="rara", **base))
        self._fails(self.o.Prediction(source="user", user_id=self.u1.id, **{**base, "reference_price": 0}))
        self.s.add(self.o.Prediction(source="user", user_id=self.u1.id, **base)); self.s.commit()

    def test_notificacion_sin_duplicados(self):
        self.s.add(self.o.Notification(user_id=self.u1.id, kind="evento", title="t", dedupe_key="AAPL:rsi:2026-11-08")); self.s.commit()
        self._fails(self.o.Notification(user_id=self.u1.id, kind="evento", title="t", dedupe_key="AAPL:rsi:2026-11-08"))

    def test_reaccion_un_solo_destino(self):
        self._fails(self.o.Reaction(user_id=self.u1.id))  # ni post ni comentario

    def test_usuario_unico_y_rol_valido(self):
        self._fails(self.o.User(username="ana", email="otro@x.co", password_hash="h"))
        self._fails(self.o.User(username="carlos", email="c@x.co", password_hash="h", role="superman"))


if __name__ == "__main__":
    unittest.main()
