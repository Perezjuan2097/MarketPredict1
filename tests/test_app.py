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
        self.assertGreaterEqual(len(j["predictions"]), 1)
        self.assertTrue(all(p["horizonte"] == "5 sesiones" for p in j["predictions"]))
        self.assertIn(j["alert"]["nivel"], ("ALTA", "MEDIA", "BAJA", "SIN SEÑAL"))
        self.assertRegex(j["data"][0]["fecha"], r"^\d{4}-\d{2}-\d{2}$")

    def test_analyze_coincide_con_consenso(self):
        """La tarjeta de predicciones y el panel de consenso salen del mismo motor."""
        j = self.c.post("/api/analyze", json={"symbol": "AAPL"}).get_json()
        m = self.c.get("/api/models/AAPL?horizon=5").get_json()
        if m["consensus"]["status"] == "ok":
            self.assertEqual(j["predictions"][0]["modelo"], "Consenso multimodelo")
            self.assertAlmostEqual(j["predictions"][0]["valor_estimado"], round(m["consensus"]["estimated_price"], 2))
        else:
            self.assertEqual(j["alert"]["nivel"], "SIN SEÑAL")
            precios = {round(x["estimated_price"], 2) for x in m["models"] if x["status"] == "ok"}
            self.assertEqual({p["valor_estimado"] for p in j["predictions"]}, precios)

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
        self.assertTrue(all(p["horizonte"] == "7 sesiones" for p in j["predictions"]))


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


class TestNuevosEndpoints(BaseCase):
    def test_models_demo(self):
        r = self.c.get("/api/models/AAPL?horizon=5")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:200])
        j = r.get_json()
        self.assertEqual(j["data_source"], "demo")
        self.assertEqual(j["horizon_days"], 5)
        self.assertIn("consensus", j)

    def test_models_validaciones(self):
        self.assertEqual(self.c.get("/api/models/AAPL?horizon=abc").status_code, 400)
        self.assertEqual(self.c.get("/api/models/AAPL?horizon=99").status_code, 422)
        self.assertEqual(self.c.get("/api/models/%3Cx%3E").status_code, 400)

    def test_markets_sin_proveedor_no_revienta(self):
        r = self.c.get("/api/markets")
        self.assertIn(r.status_code, (200, 422))
        self.assertNotIn("Traceback", r.get_data(as_text=True))

    def test_index_tiene_navegacion_funcional(self):
        html = self.c.get("/").get_data(as_text=True)
        for key in ("productos", "community", "mercados", "brokers", "mas"):
            self.assertIn(f'data-nav="{key}"', html)
        for panel in ("tabProductos", "tabMercados", "tabBrokers", "tabMas", "modelsPanel"):
            self.assertIn(f'id="{panel}"', html)


class TestNoticias(unittest.TestCase):
    def _svc(self, fake):
        from unittest.mock import patch
        from app.services.data_service import APIFinanciera
        p1 = patch.object(APIFinanciera, "resolve_ticker", classmethod(lambda cls, q: {"symbol": q}))
        p2 = patch.object(APIFinanciera, "_ticker", staticmethod(lambda s: fake))
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)
        return APIFinanciera()

    def test_formato_nuevo_yfinance(self):
        class T:
            news = [{"id": "1", "content": {"title": "Titular", "provider": {"displayName": "Reuters"},
                                            "canonicalUrl": {"url": "https://x.test/a"}, "pubDate": "2026-11-06T10:00:00Z"}}]
        out = self._svc(T()).get_news("AAPL")
        self.assertEqual(out["news"][0]["title"], "Titular")
        self.assertEqual(out["news"][0]["source"], "Reuters")
        self.assertNotIn("warning", out)

    def test_fallback_get_news_y_formato_antiguo(self):
        class T:
            news = []
            def get_news(self, count=10):
                return [{"title": "Viejo", "publisher": "AP", "link": "https://x.test/b", "providerPublishTime": 1762400000}]
        out = self._svc(T()).get_news("AAPL")
        self.assertEqual(out["news"][0]["title"], "Viejo")

    def test_error_se_informa_no_se_silencia(self):
        class T:
            @property
            def news(self):
                raise ConnectionError("bloqueado")
            def get_news(self, count=10):
                raise ConnectionError("bloqueado")
        out = self._svc(T()).get_news("AAPL")
        self.assertEqual(out["news"], [])
        self.assertIn("no respondió", out["warning"])


class TestSalud(BaseCase):
    def test_health_sin_bd(self):
        old = os.environ.pop("DATABASE_URL", None)
        try:
            j = self.c.get("/api/health").get_json()
        finally:
            if old is not None:
                os.environ["DATABASE_URL"] = old
        self.assertEqual(j["status"], "ok")
        self.assertEqual(j["db"]["status"], "no_configurada")
        self.assertIn("xgboost", j["optional_models"])
        self.assertNotIn("postgresql://", json.dumps(j))


class TestInterfazCoherente(BaseCase):
    """Sin navegador: comprueba que el JS solo apunta a elementos que existen en el HTML."""

    def test_ids_del_js_existen_en_el_html(self):
        import re
        html = self.c.get("/").get_data(as_text=True)
        js = (Path(__file__).resolve().parent.parent / "app/static/js/script.js").read_text(encoding="utf-8")
        html_ids = set(re.findall(r'id="([^"]+)"', html))
        used = set(re.findall(r'\$\("#([A-Za-z][\w-]*)"\)', js)) | set(re.findall(r'exists\("#([A-Za-z][\w-]*)"\)', js))
        missing = sorted(used - html_ids)
        self.assertEqual(missing, [], f"El JS referencia ids inexistentes: {missing}")

    def test_elementos_de_cuenta(self):
        html = self.c.get("/").get_data(as_text=True)
        for ident in ("authButton", "authDialog", "loginForm", "registerForm", "tabCuenta", "predForm", "pfCreateForm",
                      "tradeForm", "pfSelect", "saveModels", "evaluateBtn", "logoutButton"):
            self.assertIn(f'id="{ident}"', html)

    def test_solo_un_js(self):
        base = Path(__file__).resolve().parent.parent / "app/static/js"
        self.assertTrue((base / "script.js").exists())
        self.assertFalse((base / "app.js").exists())  # la copia duplicada se eliminó


class TestFinnhub(unittest.TestCase):
    def _resp(self, status=200, payload=None):
        class R:
            status_code = status
            def json(self_inner):
                return payload if payload is not None else []
        return R()

    def test_sin_clave_no_inventa(self):
        from app.services.finnhub_provider import FinnhubProvider
        with self.assertRaises(RuntimeError) as ctx:
            FinnhubProvider(api_key="").company_news("AAPL")
        self.assertIn("configuración pendiente", str(ctx.exception))

    def test_normaliza_noticias_y_usa_cabecera(self):
        from unittest.mock import patch
        from app.services.finnhub_provider import FinnhubProvider
        payload = [{"headline": "Apple sube", "source": "Reuters", "datetime": 1762400000, "url": "https://x.test/n", "summary": "Resumen"},
                   {"headline": "", "source": "X"}]
        with patch("app.services.finnhub_provider.requests.get", return_value=self._resp(200, payload)) as g:
            out = FinnhubProvider(api_key="k").company_news("AAPL")
        self.assertEqual([n["title"] for n in out], ["Apple sube"])
        self.assertEqual(g.call_args.kwargs["headers"]["X-Finnhub-Token"], "k")
        self.assertNotIn("token", g.call_args.kwargs["params"])  # la clave no viaja en la URL

    def test_errores_http_son_explicitos(self):
        from unittest.mock import patch
        from app.services.finnhub_provider import FinnhubProvider
        for status, text in ((429, "límite"), (401, "clave"), (500, "500")):
            with patch("app.services.finnhub_provider.requests.get", return_value=self._resp(status)):
                with self.assertRaises(RuntimeError) as ctx:
                    FinnhubProvider(api_key="k").company_news("AAPL")
            self.assertIn(text, str(ctx.exception))

    def test_respaldo_en_get_news_cuando_yahoo_falla(self):
        from unittest.mock import patch
        from app.services.data_service import APIFinanciera

        class Roto:
            @property
            def news(self):
                raise ConnectionError("x")
            def get_news(self, count=10):
                raise ConnectionError("x")

        payload = [{"headline": "Titular US", "source": "AP", "datetime": 1762400000, "url": "https://x.test/a"}]
        with patch.object(APIFinanciera, "resolve_ticker", classmethod(lambda cls, q: {"symbol": q})), \
             patch.object(APIFinanciera, "_ticker", staticmethod(lambda s: Roto())), \
             patch.dict(os.environ, {"FINNHUB_API_KEY": "k"}), \
             patch("app.services.finnhub_provider.requests.get", return_value=self._resp(200, payload)):
            out = APIFinanciera().get_news("AAPL")
        self.assertEqual(out["provider"], "Finnhub")
        self.assertEqual(out["news"][0]["title"], "Titular US")
        with patch.object(APIFinanciera, "resolve_ticker", classmethod(lambda cls, q: {"symbol": q})), \
             patch.object(APIFinanciera, "_ticker", staticmethod(lambda s: Roto())), \
             patch.dict(os.environ, {"FINNHUB_API_KEY": ""}):
            out = APIFinanciera().get_news("AAPL")
        self.assertEqual(out["news"], [])
        self.assertIn("FINNHUB_API_KEY", out["warning"])
