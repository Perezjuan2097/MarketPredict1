import os
import unittest
from unittest import mock

from app import create_app
from app.services import broker_service
from app.services.data_service import APIFinanciera

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>Apple sube</title><link>https://example.com/a</link><pubDate>Fri, 13 Nov 2026 04:12:02 +0000</pubDate><description>Resumen</description></item>
<item><title>Otra noticia</title><link>https://example.com/b</link></item></channel></rss>"""


class Resp:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code, self._p, self.content = status, payload, content

    def json(self):
        return self._p


class TestNoticiasRSS(unittest.TestCase):
    def test_parsea_feed(self):
        with mock.patch("requests.get", return_value=Resp(content=RSS)):
            news = APIFinanciera.yahoo_rss_news("AAPL")
        self.assertEqual([n["title"] for n in news], ["Apple sube", "Otra noticia"])
        self.assertTrue(news[0]["date"].startswith("2026-11-13"))

    def test_fallo_devuelve_lista_vacia(self):
        with mock.patch("requests.get", side_effect=Exception("sin red")):
            self.assertEqual(APIFinanciera.yahoo_rss_news("AAPL"), [])


class TestBroker(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"ALPACA_API_KEY": "k", "ALPACA_API_SECRET": "s", "BROKER_USERS": "ana, luis@x.com"})
        self.env.start()
        self.c = create_app({"TESTING": True}).test_client()

    def tearDown(self):
        self.env.stop()

    def test_usuarios_autorizados(self):
        self.assertTrue(broker_service.user_allowed({"username": "Ana", "email": "a@x.com"}))
        self.assertTrue(broker_service.user_allowed({"username": "z", "email": "LUIS@x.com"}))
        self.assertFalse(broker_service.user_allowed({"username": "z", "email": "z@x.com"}))
        self.assertFalse(broker_service.user_allowed(None))

    def test_anonimo_no_ve_la_cuenta(self):
        r = self.c.get("/api/broker/status")
        self.assertEqual(r.status_code, 401)
        self.assertFalse(r.get_json()["authorized"])

    def test_admin_ve_snapshot_paper(self):
        payloads = {"/v2/account": {"status": "ACTIVE", "equity": "100500", "last_equity": "100000", "cash": "50000", "buying_power": "200000"},
                    "/v2/positions": [{"symbol": "AAPL", "qty": "10", "unrealized_plpc": "0.05"}],
                    "/v2/orders": [], "/v2/clock": {"is_open": False}}
        def fake_get(url, **kw):
            self.assertTrue(url.startswith("https://paper-api.alpaca.markets"))  # nunca la API real
            return Resp(payload=payloads[url.replace(broker_service.PAPER_BASE, "")])
        with self.c.session_transaction() as s:
            s["is_admin"] = True
        with mock.patch("app.services.broker_service.requests.get", side_effect=fake_get):
            j = self.c.get("/api/broker/status").get_json()
        self.assertTrue(j["authorized"] and j["paper"])
        self.assertEqual(j["account"]["day_change"], 500.0)
        self.assertEqual(j["positions"][0]["unrealized_plpc"], 5.0)

    def test_claves_rechazadas(self):
        with self.c.session_transaction() as s:
            s["is_admin"] = True
        with mock.patch("app.services.broker_service.requests.get", return_value=Resp(401)):
            r = self.c.get("/api/broker/status")
        self.assertEqual(r.status_code, 502)
        self.assertIn("PAPER", r.get_json()["error"])

    def test_sin_claves(self):
        with mock.patch.dict(os.environ, {"ALPACA_API_KEY": ""}):
            j = self.c.get("/api/broker/status").get_json()
        self.assertFalse(j["configured"])
