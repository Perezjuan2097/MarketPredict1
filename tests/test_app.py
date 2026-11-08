"""Pruebas de la Fase 1 (unittest; también las recoge pytest)."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ["USE_DEMO"] = "true"


class BaseCase(unittest.TestCase):
    def setUp(self):
        from app import create_app
        self.tmp = Path(tempfile.mkdtemp())
        self.app = create_app({"TESTING": True, "PROJECT_ROOT": self.tmp, "ADMIN_TOKEN": "tok-prueba",
                               "RATE_LIMIT_PER_MINUTE": 1000})
        self.c = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestPublico(BaseCase):
    def test_index(self):
        self.assertEqual(self.c.get("/").status_code, 200)

    def test_analyze_demo_marca_origen_y_fechas_iso(self):
        j = self.c.post("/api/analyze", json={"symbol": "AAPL"}).get_json()
        self.assertEqual(j["data_source"], "demo")
        self.assertEqual(len(j["predictions"]), 5)
        self.assertRegex(j["data"][0]["fecha"], r"^\d{4}-\d{2}-\d{2}$")

    def test_symbol_invalido(self):
        self.assertEqual(self.c.post("/api/analyze", json={"symbol": "<script>"}).status_code, 422)
        self.assertEqual(self.c.get("/api/community/%3Cb%3E").status_code, 400)
        self.assertEqual(self.c.post("/api/analyze", data="x", content_type="text/plain").status_code, 400)

    def test_errores_no_filtran_internos(self):
        r = self.c.get("/api/seasonal/AAPL")  # sin yfinance/red en pruebas
        self.assertIn(r.status_code, (200, 422, 500))
        self.assertNotIn("Traceback", r.get_data(as_text=True))

    def test_save_valida_y_deduplica(self):
        rec = {"symbol": "AAPL", "fecha": "2026-11-08", "horizonte": "1 día(s)", "prediccion": 100.5,
               "modelo": "Regresión lineal", "version": "t"}
        self.assertEqual(self.c.post("/api/save", json=rec).get_json()["duplicate"], False)
        self.assertEqual(self.c.post("/api/save", json=rec).get_json()["duplicate"], True)
        self.assertEqual(len(self.c.get("/api/history").get_json()), 1)
        self.assertEqual(self.c.post("/api/save", json={"symbol": "X"}).status_code, 400)
        self.assertEqual(self.c.post("/api/save", json={**rec, "prediccion": -1}).status_code, 400)

    def test_cabeceras_seguridad(self):
        h = self.c.get("/").headers
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'self'", h["Content-Security-Policy"])

    def test_rate_limit(self):
        from app import create_app
        app = create_app({"TESTING": True, "PROJECT_ROOT": self.tmp, "RATE_LIMIT_PER_MINUTE": 3})
        from app.security import limiter
        limiter.hits.clear()
        c = app.test_client()
        codes = [c.get("/api/history").status_code for _ in range(5)]
        self.assertEqual(codes[:3], [200, 200, 200])
        self.assertEqual(codes[-1], 429)


class TestAdmin(BaseCase):
    def _login(self):
        self.c.get("/admin/login")
        with self.c.session_transaction() as s:
            csrf = s.get("csrf")
        return csrf

    def test_admin_requiere_login(self):
        self.assertEqual(self.c.get("/admin/").status_code, 302)
        self.assertEqual(self.c.post("/admin/config", data={}).status_code, 302)
        self.assertEqual(self.c.get("/admin/api/status").status_code, 403)

    def test_login_exige_csrf_y_token(self):
        self.assertEqual(self.c.post("/admin/login", data={"token": "tok-prueba"}).status_code, 403)
        csrf = self._login()
        r = self.c.post("/admin/login", data={"token": "mal", "csrf_token": csrf})
        self.assertEqual(r.status_code, 200)
        r = self.c.post("/admin/login", data={"token": "tok-prueba", "csrf_token": csrf})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.c.get("/admin/").status_code, 200)

    def test_admin_desactivado_sin_token(self):
        from app import create_app
        c = create_app({"TESTING": True, "PROJECT_ROOT": self.tmp, "ADMIN_TOKEN": ""}).test_client()
        self.assertIn("desactivado", c.get("/admin/login").get_data(as_text=True))

    def test_config_valida_y_no_toca_environ(self):
        csrf = self._login()
        self.c.post("/admin/login", data={"token": "tok-prueba", "csrf_token": csrf})
        self.c.get("/admin/")  # el panel genera el token CSRF de la sesión autenticada
        with self.c.session_transaction() as s:
            csrf = s["csrf"]
        before = os.environ.get("MODEL_HORIZON")
        bad = self.c.post("/admin/config", data={"window": "20", "horizon": "999", "alert_drop": "2",
                                                  "version": "v", "csrf_token": csrf}, follow_redirects=True)
        self.assertIn("horizonte", bad.get_data(as_text=True).lower())
        ok = self.c.post("/admin/config", data={"window": "30", "horizon": "7", "alert_drop": "3",
                                                 "version": "v2", "csrf_token": csrf}, follow_redirects=True)
        self.assertIn("actualizada", ok.get_data(as_text=True))
        self.assertEqual(os.environ.get("MODEL_HORIZON"), before)
        j = self.c.post("/api/analyze", json={"symbol": "AAPL"}).get_json()
        self.assertEqual(len(j["predictions"]), 7)
        self.assertEqual(j["model_version"], "v2")


class TestConfig(unittest.TestCase):
    def test_produccion_exige_secret(self):
        from app.settings import Settings
        old = {k: os.environ.get(k) for k in ("APP_ENV", "SECRET_KEY")}
        try:
            os.environ["APP_ENV"] = "production"; os.environ.pop("SECRET_KEY", None)
            with self.assertRaises(RuntimeError):
                Settings()
            os.environ["SECRET_KEY"] = "x" * 32
            s = Settings()
            self.assertFalse(s.DEBUG); self.assertTrue(s.SESSION_COOKIE_SECURE)
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


if __name__ == "__main__":
    unittest.main()
