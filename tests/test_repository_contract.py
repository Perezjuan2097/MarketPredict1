"""Pruebas de contrato: cualquier repositorio debe comportarse igual. Memoria siempre; SQL si hay SQLAlchemy."""
import importlib.util
import os
import tempfile
import unittest
from decimal import Decimal

from app.repositories.base import ConflictError, DuplicateError, NotFoundError
from app.repositories.memory import MemoryRepository

HAS_SA = importlib.util.find_spec("sqlalchemy") is not None


class RepoContract:
    def make_repo(self):
        raise NotImplementedError

    def setUp(self):
        self.repo = self.make_repo()
        self.u1 = self.repo.create_user("ana", "Ana@x.co", "hash1")
        self.u2 = self.repo.create_user("beto", "beto@x.co", "hash2")
        self.aid = self.repo.get_or_create_asset("AAPL", "Apple", "USD")

    def _pred(self, **kw):
        base = dict(source="user", user_id=self.u1["id"], asset_id=self.aid, direction="alcista", reference_price=100.0,
                    target_price=110.0, expected_return_pct=None, horizon_days=5, target_date="2026-01-10",
                    rationale="r", indicators=["RSI"], is_public=True)
        base.update(kw)
        return self.repo.add_prediction(base)

    def test_usuarios(self):
        self.assertEqual(self.u1["email"], "ana@x.co")
        with self.assertRaises(DuplicateError):
            self.repo.create_user("ANA", "otro@x.co", "h")
        with self.assertRaises(DuplicateError):
            self.repo.create_user("nuevo", "ANA@x.co", "h")
        self.assertEqual(self.repo.find_user("ANA@X.CO")["id"], self.u1["id"])
        self.assertEqual(self.repo.find_user("Beto")["id"], self.u2["id"])
        self.assertIsNone(self.repo.find_user("nadie"))
        self.assertEqual(self.repo.get_user(self.u1["id"])["username"], "ana")
        self.assertIsNone(self.repo.get_user(9999))
        self.repo.touch_login(self.u1["id"])

    def test_activos_idempotentes(self):
        self.assertEqual(self.repo.get_or_create_asset("AAPL"), self.aid)
        self.assertNotEqual(self.repo.get_or_create_asset("BMW.DE"), self.aid)

    def test_predicciones(self):
        p = self._pred()
        self.assertEqual(p["status"], "pendiente")
        self.assertEqual(p["symbol"], "AAPL")
        self.assertIsNone(p["evaluation"])
        self._pred(user_id=self.u2["id"])
        self.assertEqual(len(self.repo.list_predictions(self.u1["id"])), 1)
        self.repo.set_prediction_status(p["id"], "invalidada")
        self.assertEqual(self.repo.get_prediction(p["id"])["status"], "invalidada")
        with self.assertRaises(NotFoundError):
            self.repo.set_prediction_status(9999, "cumplida")

    def test_vencidas_y_evaluacion_unica(self):
        p = self._pred(target_date="2026-01-10")
        self._pred(target_date="2030-01-10")
        due = self.repo.due_predictions("2026-02-01")
        self.assertEqual([d["id"] for d in due], [p["id"]])
        ev = {"actual_price": 112.0, "actual_return_pct": 12.0, "abs_error": 2.0, "pct_error": 1.79,
              "direction_correct": True, "baseline_abs_error": 12.0}
        self.repo.save_evaluation(p["id"], ev, "cumplida")
        got = self.repo.get_prediction(p["id"])
        self.assertEqual(got["status"], "cumplida")
        self.assertAlmostEqual(got["evaluation"]["actual_price"], 112.0)
        self.assertTrue(got["evaluation"]["direction_correct"])
        self.assertEqual(self.repo.due_predictions("2026-02-01"), [])
        with self.assertRaises(ConflictError):
            self.repo.save_evaluation(p["id"], ev, "cumplida")

    def test_portafolios_y_operaciones(self):
        pf = self.repo.create_portfolio(self.u1["id"], "Principal", Decimal("10000"), Decimal("0.5"))
        with self.assertRaises(DuplicateError):
            self.repo.create_portfolio(self.u1["id"], "principal", Decimal("5"), Decimal("0"))
        self.repo.create_portfolio(self.u2["id"], "Principal", Decimal("5"), Decimal("0"))  # otro usuario: permitido
        self.assertEqual(len(self.repo.list_portfolios(self.u1["id"])), 1)
        self.assertEqual(Decimal(str(pf["cash"])), Decimal("10000.00"))
        self.assertIsNone(pf["currency"])
        t = {"symbol": "AAPL", "side": "buy", "quantity": Decimal("10"), "price": Decimal("100"), "commission": Decimal("5"), "price_source": "t"}
        self.repo.apply_trade(pf["id"], t, expected_cash=Decimal("10000.00"), new_cash=Decimal("8995.00"), currency="USD")
        now = self.repo.get_portfolio(pf["id"])
        self.assertEqual(Decimal(str(now["cash"])), Decimal("8995.00"))
        self.assertEqual(now["currency"], "USD")
        with self.assertRaises(ConflictError):  # saldo desactualizado
            self.repo.apply_trade(pf["id"], t, expected_cash=Decimal("10000.00"), new_cash=Decimal("7990.00"), currency="USD")
        self.assertEqual(Decimal(str(self.repo.get_portfolio(pf["id"])["cash"])), Decimal("8995.00"))
        trades = self.repo.list_trades(pf["id"])
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["symbol"], "AAPL")
        self.assertEqual(Decimal(str(trades[0]["quantity"])), Decimal("10"))
        with self.assertRaises(NotFoundError):
            self.repo.apply_trade(9999, t, expected_cash=Decimal("1"), new_cash=Decimal("1"), currency="USD")


class TestMemoryRepo(RepoContract, unittest.TestCase):
    def make_repo(self):
        return MemoryRepository()


@unittest.skipUnless(HAS_SA, "sqlalchemy no instalado: ejecute estas pruebas en su entorno completo")
class TestSQLRepo(RepoContract, unittest.TestCase):
    def make_repo(self):
        from sqlalchemy import create_engine
        from app.models.orm import Base
        from app.repositories.sql import SQLRepository
        self._dir = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{os.path.join(self._dir.name, 't.db')}")
        Base.metadata.create_all(engine)
        return SQLRepository(engine)

    def tearDown(self):
        self._dir.cleanup()


if __name__ == "__main__":
    unittest.main()
