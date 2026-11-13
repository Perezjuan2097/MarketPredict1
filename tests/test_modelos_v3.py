"""Pruebas de los módulos nuevos: probabilidad, riesgo, contexto, explicación y pulso de noticias."""
import unittest

import numpy as np
import pandas as pd

from app.services.data_service import DemoFuente
from app.services.news_pulse import pulse, score_text
from ml_engine import explain, probability, risk
from ml_engine.engine import run_all
from ml_engine.features import builder


def demo(n=600, sym="AAPL"):
    return DemoFuente().obtener(sym, n)


def random_walk(n=600, seed=3):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, n)))
    dates = pd.bdate_range("2024-01-01", periods=n)
    return [{"fecha": d.isoformat(), "apertura": c, "maximo": c * 1.01, "minimo": c * 0.99, "cierre": c, "volumen": 1000}
            for d, c in zip(dates, close)]


class TestSinVolumen(unittest.TestCase):
    def test_indices_y_divisas_sin_volumen_tienen_prediccion(self):
        d = demo()
        for r in d:
            r["volumen"] = 0
            r["maximo"] = r["minimo"] = None
        out = run_all(d, "^IBEX", 5)
        self.assertTrue(any(m["status"] == "ok" for m in out["models"]))


class TestContexto(unittest.TestCase):
    def test_no_usa_datos_futuros(self):
        d = demo(400)
        df = builder.to_frame(d)
        # serie de contexto que solo existe hasta la mitad: después debe repetirse el último valor, no inventar
        half = [{"fecha": r["fecha"], "cierre": 10 + i} for i, r in enumerate(d[:200])]
        X, _ = builder.build(df, 5, {"^VIX": half})
        self.assertAlmostEqual(X["vix_nivel"].iloc[-1], X["vix_nivel"].iloc[199])
        # cambiar un valor futuro del contexto no altera las filas anteriores
        alt = [dict(x) for x in half]
        alt[150]["cierre"] = 999
        X2, _ = builder.build(df, 5, {"^VIX": alt})
        pd.testing.assert_series_equal(X["vix_nivel"].iloc[:150], X2["vix_nivel"].iloc[:150])


class TestProbabilidad(unittest.TestCase):
    def test_paseo_aleatorio_no_inventa_ventaja(self):
        df = builder.to_frame(random_walk())
        X_all, y_all = builder.build(df, 5)
        lab = X_all.notna().all(axis=1) & y_all.notna()
        X, y = X_all[lab].reset_index(drop=True), y_all[lab].reset_index(drop=True)
        out = probability.run(X, y, X_all.iloc[[-1]], 5)
        self.assertFalse(out["has_edge"])  # sobre ruido puro no debe declarar ventaja
        self.assertEqual(out["prob_up"], out["base_rate"])
        self.assertTrue(0 <= out["prob_up"] <= 1)


class TestRiesgo(unittest.TestCase):
    def test_volatilidad_agrupada_se_predice(self):
        rng = np.random.default_rng(0)
        n, vol, rets = 700, 0.01, []
        for i in range(n):  # GARCH(1,1) simple: la volatilidad sí es predecible
            r = rng.normal(0, vol)
            rets.append(r)
            vol = np.sqrt(0.000002 + 0.1 * r ** 2 + 0.88 * vol ** 2)
        close = 100 * np.exp(np.cumsum(rets))
        df = pd.DataFrame({"cierre": close})
        out = risk.run(df, 5)
        self.assertGreater(out["skill_vs_reference"], 0)
        self.assertLess(out["range_90"]["low_price"], close[-1])
        self.assertGreater(out["range_90"]["high_price"], close[-1])
        self.assertGreater(out["range_90"]["historical_coverage"], 0.75)


    def test_colas_gruesas_rango_calibrado(self):
        rng = np.random.default_rng(5)
        rets = rng.standard_t(3, 900) * 0.01  # colas gruesas, como los mercados reales
        out = risk.run(pd.DataFrame({"cierre": 100 * np.exp(np.cumsum(rets))}), 5)
        rg = out["range_90"]
        self.assertGreaterEqual(rg["multiplier"], 1.645)
        self.assertGreater(rg["historical_coverage"], 0.8)  # medido en datos que no participaron en el ajuste


class TestExplicacion(unittest.TestCase):
    def test_nunca_promete_certeza(self):
        out = run_all(demo(), "AAPL", 5)
        ex = out["explanation"]
        text = " ".join(ex["points"] + [ex["headline"], ex["what_to_do"]]).lower()
        for palabra in ("seguro que", "garantiza que", "certeza de", "100 % seguro"):
            self.assertNotIn(palabra, text)
        self.assertIn("ningún modelo garantiza", text)
        self.assertIn("glossary", out)


class TestPulso(unittest.TestCase):
    def test_tono(self):
        self.assertEqual(score_text("Apple stock hits record high after upgrade"), (3, 0))
        self.assertEqual(score_text("Nvidia shares plunge amid probe"), (0, 2))
        self.assertEqual(score_text("Company does not cut dividend"), (2, 0))  # negación invierte "cut"
        p = pulse([{"title": "Stocks rally to record"}, {"title": "Shares fall on weak guidance"}, {"title": "CEO speaks"}])
        self.assertEqual(p["counts"], {"positivo": 1, "neutral": 1, "negativo": 1})
        self.assertFalse(p["validated"])
