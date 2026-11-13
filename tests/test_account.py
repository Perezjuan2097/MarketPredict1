"""Cuentas, predicciones y portafolio a través de la API HTTP (repositorio en memoria + precios simulados)."""
import os
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

os.environ["USE_DEMO"] = "true"

from app.repositories.memory import MemoryRepository  # noqa: E402
from app.services.evaluation import evaluate  # noqa: E402
from app.services.portfolio_logic import compute_positions  # noqa: E402


class FakeProvider:
    source = "fake"

    def __init__(self):
        self.prices = {"AAPL": 100.0, "BMW.DE": 60.0}
        self.currency = {"AAPL": "USD", "BMW.DE": "EUR"}
        self.closes = {}

    def latest(self, symbol):
        s = symbol.upper()
        if s not in self.prices:
            raise ValueError(f"No se encontraron datos para el activo {s}.")
        return {"symbol": s, "name": s, "price": self.prices[s], "currency": self.currency[s], "source": "fake"}

    def close_on_or_before(self, symbol, target):
        return self.closes.get((symbol.upper(), target.isoformat()))


class Base(unittest.TestCase):
    def setUp(self):
        from app import create_app
        from app.security import limiter
        limiter.hits.clear()
        self.tmp = Path(tempfile.mkdtemp())
        self.repo, self.provider = MemoryRepository(), FakeProvider()
        self.app = create_app({"TESTING": True, "PROJECT_ROOT": self.tmp, "RATE_LIMIT_PER_MINUTE": 10000,
                               "REPOSITORY": self.repo, "PRICE_PROVIDER": self.provider})
        self.c = self.app.test_client()
        self.csrf = self.c.get("/api/auth/me").get_json()["csrf"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, url, body=None, client=None, token=None):
        c = client or self.c
        return c.post(url, json=body or {}, headers={"X-CSRF-Token": token or self.csrf})

    def register(self, name="ana", email="ana@x.co", pw="Clave1234", client=None):
        c = client or self.c
        tok = c.get("/api/auth/me").get_json()["csrf"]
        r = c.post("/api/auth/register", json={"username": name, "email": email, "password": pw}, headers={"X-CSRF-Token": tok})
        if r.status_code == 201:
            self.csrf = r.get_json()["csrf"] if c is self.c else self.csrf
        return r

    def second_client(self, name="beto", email="beto@x.co"):
        c2 = self.app.test_client()
        r = self.register(name, email, client=c2)
        self.assertEqual(r.status_code, 201)
        return c2, r.get_json()["csrf"]


class TestAuth(Base):
    def test_estado_inicial(self):
        j = self.c.get("/api/auth/me").get_json()
        self.assertIsNone(j["user"])
        self.assertTrue(j["persistence"])

    def test_registro_valida(self):
        for body in ({"username": "a", "email": "a@x.co", "password": "Clave1234"},
                     {"username": "ana", "email": "malo", "password": "Clave1234"},
                     {"username": "ana", "email": "a@x.co", "password": "corta1"},
                     {"username": "ana", "email": "a@x.co", "password": "sinnumeros"},
                     {"username": "ana pe", "email": "a@x.co", "password": "Clave1234"}):
            r = self.post("/api/auth/register", body)
            self.assertEqual(r.status_code, 400, body)

    def test_registro_login_logout_y_hash(self):
        r = self.register()
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.c.get("/api/auth/me").get_json()["user"]["username"], "ana")
        stored = self.repo.users[1]["password_hash"]
        self.assertNotIn("Clave1234", stored)
        self.assertTrue(stored.startswith(("scrypt:", "pbkdf2:")))
        self.assertNotIn("password_hash", r.get_data(as_text=True))
        self.assertEqual(self.post("/api/auth/logout").status_code, 200)
        self.assertIsNone(self.c.get("/api/auth/me").get_json()["user"])
        tok = self.c.get("/api/auth/me").get_json()["csrf"]
        ok = self.post("/api/auth/login", {"identifier": "ANA@x.co", "password": "Clave1234"}, token=tok)
        self.assertEqual(ok.status_code, 200)

    def test_duplicado(self):
        self.register()
        c2 = self.app.test_client()
        r = self.register(client=c2)
        self.assertEqual(r.status_code, 400)
        self.assertIn("en uso", r.get_json()["error"])

    def test_login_erroneo_mismo_mensaje(self):
        self.register(); self.post("/api/auth/logout")
        tok = self.c.get("/api/auth/me").get_json()["csrf"]
        a = self.post("/api/auth/login", {"identifier": "ana", "password": "incorrecta1"}, token=tok)
        b = self.post("/api/auth/login", {"identifier": "fantasma", "password": "incorrecta1"}, token=tok)
        self.assertEqual((a.status_code, b.status_code), (401, 401))
        self.assertEqual(a.get_json(), b.get_json())

    def test_csrf_obligatorio(self):
        r = self.c.post("/api/auth/register", json={"username": "ana", "email": "a@x.co", "password": "Clave1234"})
        self.assertEqual(r.status_code, 403)
        r = self.c.post("/api/auth/register", json={"username": "ana", "email": "a@x.co", "password": "Clave1234"},
                        headers={"X-CSRF-Token": "falso"})
        self.assertEqual(r.status_code, 403)

    def test_freno_de_fuerza_bruta(self):
        self.register(); self.post("/api/auth/logout")
        tok = self.c.get("/api/auth/me").get_json()["csrf"]
        codes = [self.post("/api/auth/login", {"identifier": "ana", "password": "mala1234x"}, token=tok).status_code for _ in range(10)]
        self.assertEqual(codes[:8], [401] * 8)
        self.assertEqual(codes[-1], 429)

    def test_usuario_bloqueado_no_entra(self):
        self.register(); self.post("/api/auth/logout")
        self.repo.users[1]["is_blocked"] = True
        tok = self.c.get("/api/auth/me").get_json()["csrf"]
        self.assertEqual(self.post("/api/auth/login", {"identifier": "ana", "password": "Clave1234"}, token=tok).status_code, 401)

    def test_sin_base_de_datos_503(self):
        from app import create_app
        os.environ.pop("DATABASE_URL", None)
        c = create_app({"TESTING": True, "PROJECT_ROOT": self.tmp}).test_client()
        tok = c.get("/api/auth/me").get_json()
        self.assertFalse(tok["persistence"])
        r = c.post("/api/auth/login", json={}, headers={"X-CSRF-Token": tok["csrf"]})
        self.assertEqual(r.status_code, 503)


class TestPredicciones(Base):
    PAYLOAD = {"symbol": "AAPL", "direction": "alcista", "horizon_days": 5, "target_price": 110, "rationale": "Ruptura"}

    def test_requiere_sesion(self):
        self.assertEqual(self.c.get("/api/me/predictions").status_code, 401)
        self.assertEqual(self.post("/api/me/predictions", self.PAYLOAD).status_code, 401)

    def test_publicar_y_listar(self):
        self.register()
        r = self.post("/api/me/predictions", {**self.PAYLOAD, "reference_price": 1})  # el cliente NO fija la referencia
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        p = r.get_json()["prediction"]
        self.assertEqual(p["reference_price"], 100.0)
        self.assertEqual(p["status"], "pendiente")
        self.assertGreater(p["target_date"], date.today().isoformat())
        self.assertEqual(len(self.c.get("/api/me/predictions").get_json()["predictions"]), 1)

    def test_validaciones(self):
        self.register()
        bad = [{**self.PAYLOAD, "direction": "arriba"}, {**self.PAYLOAD, "horizon_days": 0}, {**self.PAYLOAD, "horizon_days": 500},
               {**self.PAYLOAD, "target_price": 90}, {**self.PAYLOAD, "direction": "bajista", "target_price": 120},
               {**self.PAYLOAD, "symbol": "<x>"}, {**self.PAYLOAD, "target_price": "abc"}]
        for body in bad:
            self.assertEqual(self.post("/api/me/predictions", body).status_code, 400, body)
        self.assertEqual(self.post("/api/me/predictions", {**self.PAYLOAD, "symbol": "NOEXISTE"}).status_code, 422)

    def test_no_se_edita_y_se_puede_invalidar(self):
        self.register()
        pid = self.post("/api/me/predictions", self.PAYLOAD).get_json()["prediction"]["id"]
        self.assertIn(self.c.put(f"/api/me/predictions/{pid}", json={"target_price": 999}, headers={"X-CSRF-Token": self.csrf}).status_code, (404, 405))
        r = self.post(f"/api/me/predictions/{pid}/cancel")
        self.assertEqual(r.get_json()["prediction"]["status"], "invalidada")
        self.assertEqual(self.post(f"/api/me/predictions/{pid}/cancel").status_code, 400)
        self.assertEqual(self.repo.preds[pid]["target_price"], 110.0)  # el historial no cambió

    def test_aislamiento_entre_usuarios(self):
        self.register()
        pid = self.post("/api/me/predictions", self.PAYLOAD).get_json()["prediction"]["id"]
        c2, tok2 = self.second_client()
        self.assertEqual(c2.get("/api/me/predictions").get_json()["predictions"], [])
        self.assertEqual(self.post(f"/api/me/predictions/{pid}/cancel", client=c2, token=tok2).status_code, 404)

    def test_evaluacion_automatica_al_vencer(self):
        self.register()
        pid = self.post("/api/me/predictions", self.PAYLOAD).get_json()["prediction"]["id"]
        self.assertEqual(self.post("/api/me/predictions/evaluate").get_json(), {"evaluated": 0, "waiting_for_data": 0})  # aún no vence
        past = (date.today() - timedelta(days=3)).isoformat()
        self.repo.preds[pid]["target_date"] = past
        r = self.post("/api/me/predictions/evaluate").get_json()  # vencida, sin dato de precio todavía
        self.assertEqual(r, {"evaluated": 0, "waiting_for_data": 1})
        self.assertEqual(self.repo.preds[pid]["status"], "en_evaluacion")
        self.provider.closes[("AAPL", past)] = 108.0
        r = self.post("/api/me/predictions/evaluate").get_json()
        self.assertEqual(r["evaluated"], 1)
        p = self.c.get("/api/me/predictions").get_json()["predictions"][0]
        self.assertEqual(p["status"], "cumplida")
        self.assertAlmostEqual(p["evaluation"]["abs_error"], 2.0)
        self.assertAlmostEqual(p["evaluation"]["baseline_abs_error"], 8.0)
        self.assertEqual(self.post("/api/me/predictions/evaluate").get_json()["evaluated"], 0)  # no se reevalúa

    def test_guardar_predicciones_de_modelos(self):
        self.register()
        r = self.post("/api/me/predictions/from-models", {"symbol": "AAPL", "horizon": 5})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True)[:300])
        saved = r.get_json()["saved"]
        self.assertGreaterEqual(len(saved), 2)
        self.assertTrue(all(p["status"] == "pendiente" and p["indicators"]["model_version"] for p in saved))
        self.assertFalse(any(p["is_public"] for p in saved))


class TestEvaluacionPura(unittest.TestCase):
    def test_direcciones(self):
        base = {"reference_price": 100, "target_price": None, "expected_return_pct": None}
        self.assertEqual(evaluate({**base, "direction": "alcista"}, 101)[1], "cumplida")
        self.assertEqual(evaluate({**base, "direction": "alcista"}, 99)[1], "no_cumplida")
        self.assertEqual(evaluate({**base, "direction": "bajista"}, 99)[1], "cumplida")
        self.assertEqual(evaluate({**base, "direction": "neutral"}, 100.5)[1], "cumplida")
        self.assertEqual(evaluate({**base, "direction": "neutral"}, 103)[1], "no_cumplida")

    def test_errores_y_referencia(self):
        ev, _ = evaluate({"reference_price": 100, "direction": "alcista", "target_price": None, "expected_return_pct": 10}, 105)
        self.assertAlmostEqual(ev["abs_error"], 5.0)
        self.assertAlmostEqual(ev["pct_error"], 5 / 105 * 100, places=3)
        self.assertAlmostEqual(ev["baseline_abs_error"], 5.0)
        with self.assertRaises(ValueError):
            evaluate({"reference_price": 100, "direction": "alcista"}, 0)


class TestPortafolio(Base):
    def setUp(self):
        super().setUp()
        self.register()
        r = self.post("/api/me/portfolios", {"name": "Principal", "initial_cash": 10000, "commission_pct": 0.5})
        self.assertEqual(r.status_code, 201)
        self.pf = r.get_json()["portfolio"]["id"]

    def trade(self, symbol="AAPL", side="buy", qty=10, **kw):
        return self.post(f"/api/me/portfolios/{self.pf}/trades", {"symbol": symbol, "side": side, "quantity": qty}, **kw)

    def test_crear_valida(self):
        for body in ({"name": "", "initial_cash": 100}, {"name": "x", "initial_cash": -5}, {"name": "x", "initial_cash": "abc"},
                     {"name": "x", "initial_cash": 100, "commission_pct": 50}):
            self.assertEqual(self.post("/api/me/portfolios", body).status_code, 400, body)
        self.assertEqual(self.post("/api/me/portfolios", {"name": "principal", "initial_cash": 5}).status_code, 400)  # duplicado

    def test_compra_venta_y_pnl(self):
        self.assertEqual(self.trade().status_code, 201)
        snap = self.c.get(f"/api/me/portfolios/{self.pf}").get_json()
        self.assertAlmostEqual(snap["cash"], 8995.0)                      # 10000 - 1000 - 5 de comisión
        self.assertAlmostEqual(snap["positions"][0]["avg_cost"], 100.5)
        self.provider.prices["AAPL"] = 120.0
        self.assertEqual(self.trade(side="sell", qty=4).status_code, 201)
        snap = self.c.get(f"/api/me/portfolios/{self.pf}").get_json()
        self.assertAlmostEqual(snap["cash"], 9472.6)
        pos = snap["positions"][0]
        self.assertAlmostEqual(pos["quantity"], 6.0)
        self.assertAlmostEqual(pos["realized_pnl"], 75.6)
        self.assertAlmostEqual(pos["unrealized_pnl"], 117.0)
        self.assertAlmostEqual(snap["equity"], 10192.6)
        self.assertAlmostEqual(snap["return_pct"], 1.926, places=3)
        self.assertAlmostEqual(pos["weight_pct"], 720 / 10192.6 * 100, places=3)
        self.assertEqual(len(snap["trades"]), 2)

    def test_rechazos(self):
        self.assertEqual(self.trade(qty=1000).status_code, 400)             # fondos insuficientes
        self.assertEqual(self.trade(side="sell", qty=1).status_code, 400)   # venta en corto
        self.assertEqual(self.trade(qty=0).status_code, 400)
        self.assertEqual(self.trade(qty=-3).status_code, 400)
        self.assertEqual(self.trade(side="hold").status_code, 400)
        self.assertEqual(self.trade(symbol="NOEXISTE").status_code, 422)
        self.trade()
        r = self.trade(symbol="BMW.DE", qty=1)                              # otra divisa
        self.assertEqual(r.status_code, 400)
        self.assertIn("USD", r.get_json()["error"])
        self.assertAlmostEqual(self.c.get(f"/api/me/portfolios/{self.pf}").get_json()["cash"], 8995.0)  # nada cambió

    def test_aislamiento(self):
        c2, tok2 = self.second_client()
        self.assertEqual(c2.get(f"/api/me/portfolios/{self.pf}").status_code, 404)
        self.assertEqual(self.trade(client=c2, token=tok2).status_code, 404)
        self.assertEqual(c2.get("/api/me/portfolios").get_json()["portfolios"], [])


class TestLogicaPosiciones(unittest.TestCase):
    def test_costo_promedio_y_cierre_total(self):
        t = lambda i, side, q, p, f=0: {"id": i, "symbol": "X", "side": side, "quantity": q, "price": p, "commission": f}  # noqa: E731
        pos = compute_positions([t(1, "buy", 10, 100), t(2, "buy", 10, 120), t(3, "sell", 5, 130)])["X"]
        self.assertEqual(pos["quantity"], Decimal("15"))
        self.assertEqual(pos["avg_cost"], Decimal("110"))
        self.assertEqual(pos["realized_pnl"], Decimal("100"))
        closed = compute_positions([t(1, "buy", 2, 50), t(2, "sell", 2, 60)])["X"]
        self.assertEqual(closed["quantity"], Decimal("0"))
        self.assertEqual(closed["realized_pnl"], Decimal("20"))
        with self.assertRaises(ValueError):
            compute_positions([t(1, "sell", 1, 10)])


if __name__ == "__main__":
    unittest.main()
