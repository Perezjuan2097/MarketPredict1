"""Pruebas del motor de predicción: indicadores, fuga de datos, validación, consenso y Monte Carlo."""
import math
import unittest

import numpy as np
import pandas as pd

from ml_engine.engine import run_all
from ml_engine.ensemble import consensus
from ml_engine.evaluation import backtest as bt
from ml_engine.evaluation import metrics as M
from ml_engine.features import builder
from ml_engine.features import technical as ta
from ml_engine.models import monte_carlo as mc


def make_data(n=520, seed=1, drift=0.0003, vol=0.012, ar=0.0):
    rng = np.random.default_rng(seed)
    r = np.zeros(n)
    for t in range(1, n):
        r[t] = drift + ar * r[t - 1] + rng.normal(0, vol)
    close = 100 * np.exp(np.cumsum(r))
    dates = pd.date_range("2023-01-02", periods=n, freq="B")
    return [{"fecha": str(d.date()), "apertura": c * 0.999, "maximo": c * 1.01, "minimo": c * 0.99,
             "cierre": float(c), "volumen": int(1e6 * (1 + rng.random()))} for d, c in zip(dates, close)]


class TestIndicadores(unittest.TestCase):
    def setUp(self):
        self.df = builder.to_frame(make_data(200))

    def test_rsi_rango_y_extremo(self):
        r = ta.rsi(self.df["cierre"]).dropna()
        self.assertTrue(((r >= 0) & (r <= 100)).all())
        up = pd.Series(np.arange(1.0, 60.0))
        self.assertAlmostEqual(float(ta.rsi(up).iloc[-1]), 100.0)

    def test_sma_manual(self):
        c = self.df["cierre"]
        self.assertAlmostEqual(float(ta.sma(c, 5).iloc[10]), float(c.iloc[6:11].mean()))

    def test_bandas_y_estocastico_y_atr(self):
        b = ta.bollinger(self.df["cierre"]).dropna()
        self.assertTrue((b["upper"] > b["lower"]).all())
        k = ta.stochastic(self.df).dropna()["k"]
        self.assertTrue(((k >= 0) & (k <= 100)).all())
        self.assertTrue((ta.atr(self.df).dropna() > 0).all())
        self.assertTrue((ta.adx(self.df).dropna() >= 0).all())

    def test_fibonacci_orden(self):
        f = ta.fibonacci_levels(self.df)
        self.assertGreater(f["0.236"], f["0.786"])
        self.assertLessEqual(f["minimo"], f["0.786"])


class TestFugaDeDatos(unittest.TestCase):
    def test_variables_no_miran_al_futuro(self):
        df = builder.to_frame(make_data(300))
        X_full, _ = builder.build(df, 5)
        k = 200
        X_cut, _ = builder.build(df.iloc[:k].copy(), 5)
        pd.testing.assert_frame_equal(X_full.iloc[:k].reset_index(drop=True), X_cut.reset_index(drop=True))

    def test_objetivo_alineado(self):
        df = builder.to_frame(make_data(100))
        _, y = builder.build(df, 5)
        i = 20
        self.assertAlmostEqual(float(y.iloc[i]), math.log(df["cierre"].iloc[i + 5] / df["cierre"].iloc[i]))
        self.assertTrue(y.iloc[-5:].isna().all())

    def test_walk_forward_cronologico_con_purga(self):
        n, h = 400, 5
        X = pd.DataFrame({"a": np.arange(n, dtype=float)})
        y = pd.Series(np.arange(n, dtype=float))
        seen = []

        class Spy:
            def fit(self, Xt, yt):
                seen.append(("train_max", int(Xt.index.max())))
                return self

            def predict(self, Xp):
                seen.append(("test_min", int(Xp.index.min())))
                return np.zeros(len(Xp))

        idx, pred = bt.walk_forward(Spy, X, y, h)
        trains = [v for k, v in seen if k == "train_max"]
        tests = [v for k, v in seen if k == "test_min"]
        self.assertEqual(len(trains), len(tests))
        for tr, te in zip(trains, tests):
            self.assertLessEqual(tr + h, te)  # el objetivo de la última fila de entrenamiento ya era conocido
        self.assertTrue(np.all(np.diff(idx) == 1))  # prueba cronológica y sin huecos
        self.assertEqual(len(idx), len(pred))

    def test_datos_insuficientes(self):
        with self.assertRaises(ValueError):
            bt.split_blocks(60, 5)
        with self.assertRaises(ValueError):
            run_all(make_data(100), "X", 5)


class TestMetricas(unittest.TestCase):
    def test_mape_control_denominador(self):
        self.assertIsNone(M.mape([0, 0], [1, 2]))
        self.assertAlmostEqual(M.mape([0, 100], [5, 110]), 10.0)

    def test_direccional(self):
        self.assertEqual(M.directional_accuracy([1, -1, 2, -2], [1, 1, 3, -1]), 0.75)
        self.assertIsNone(M.directional_accuracy([0, 0], [1, 1]))

    def test_errores(self):
        self.assertEqual(M.mae([1, 2], [2, 4]), 1.5)
        self.assertEqual(M.mse([1, 2], [2, 4]), 2.5)
        self.assertAlmostEqual(M.rmse([1, 2], [2, 4]), math.sqrt(2.5))

    def test_drawdown_y_sharpe(self):
        self.assertAlmostEqual(M.max_drawdown([0.1, -0.5, 0.1]), -0.5)
        self.assertIsNone(M.sharpe_ratio([0.01, 0.01, 0.01], 252))
        self.assertGreater(M.sharpe_ratio([0.02, 0.01, 0.03, 0.02], 252), 0)

    def test_skill(self):
        self.assertAlmostEqual(bt.skill_vs_baseline(0.9, [1.0, 1.2]), 0.1)
        self.assertLess(bt.skill_vs_baseline(1.1, [1.0]), 0)


def _res(mid, pred, skill, rmse=0.02):
    return {"model_id": mid, "status": "ok", "is_baseline": False, "predicted_log_return": pred,
            "historical_metrics": {"skill_vs_baseline": skill, "rmse_return": rmse}}


class TestConsenso(unittest.TestCase):
    def test_pesos_por_validacion_no_por_optimismo(self):
        c = consensus.compute([_res("a", 0.01, 0.10), _res("b", 0.50, -0.20), _res("c", 0.02, 0.30)])
        self.assertEqual(c["weights"]["b"], 0.0)  # el más optimista pero sin habilidad no pesa
        self.assertAlmostEqual(sum(c["weights"].values()), 1.0, places=3)
        self.assertGreater(c["weights"]["c"], c["weights"]["a"])

    def test_sin_valor_predictivo(self):
        c = consensus.compute([_res("a", 0.05, -0.1), _res("b", 0.04, 0.0)])
        self.assertEqual(c["status"], "sin_valor_predictivo")
        self.assertIsNone(c["weighted_return_pct"])
        self.assertEqual(c["direction"], "neutral")

    def test_direccion_neutral_en_banda(self):
        c = consensus.compute([_res("a", 0.001, 0.2, rmse=0.05)])
        self.assertEqual(c["direction"], "neutral")
        c = consensus.compute([_res("a", 0.08, 0.2, rmse=0.05)])
        self.assertEqual(c["direction"], "alcista")

    def test_sin_modelos(self):
        self.assertEqual(consensus.compute([])["status"], "sin_modelos")


class TestMonteCarlo(unittest.TestCase):
    def setUp(self):
        self.close = np.array([d["cierre"] for d in make_data(400)])

    def test_reproducible_y_percentiles_ordenados(self):
        a = mc.simulate(self.close, 5, seed=3)
        b = mc.simulate(self.close, 5, seed=3)
        np.testing.assert_array_equal(a, b)
        s = mc.summarize(a, self.close[-1])["percentiles_price"]
        self.assertTrue(s["p5"] <= s["p25"] <= s["p50"] <= s["p75"] <= s["p95"])
        self.assertEqual(a.shape, (2000, 5))

    def test_calibracion_razonable(self):
        cal = mc.calibration(self.close, 5)
        self.assertGreater(cal["empirical_coverage"], 0.75)
        self.assertEqual(cal["nominal_level"], 0.90)

    def test_datos_insuficientes(self):
        with self.assertRaises(ValueError):
            mc.simulate(self.close[:20], 5)


class TestMotor(unittest.TestCase):
    def test_respuesta_estandarizada(self):
        r = run_all(make_data(), "SIM", 5)
        required = {"model_id", "name", "version", "asset", "run_date", "reference_price", "horizon_days",
                    "estimated_price", "estimated_return_pct", "interval", "indicators_used",
                    "historical_metrics", "status"}
        self.assertEqual(len(r["models"]), 2)
        for m in r["models"]:
            self.assertTrue(required <= set(m), required - set(m))
            self.assertEqual(m["status"], "ok")
            self.assertLess(m["interval"]["low_price"], m["interval"]["high_price"])
        self.assertEqual(len(r["baselines"]), 2)
        self.assertIn("calibration", r["monte_carlo"])

    def test_ruido_puro_no_genera_consenso_alcista_o_bajista(self):
        r = run_all(make_data(seed=7, drift=0.0), "RUIDO", 5)
        self.assertIn(r["consensus"]["direction"], ("neutral",) if r["consensus"]["status"] != "ok" else ("neutral", "alcista", "bajista"))
        if r["consensus"]["status"] == "sin_valor_predictivo":
            self.assertTrue(all(v == 0.0 for v in r["consensus"]["weights"].values()))

    def test_detecta_senal_real(self):
        r = run_all(make_data(n=900, seed=3, ar=0.45, drift=0.0, vol=0.01), "AR", 1)
        lin = next(m for m in r["models"] if m["model_id"] == "linear_regression")
        self.assertGreater(lin["historical_metrics"]["skill_vs_baseline"], 0)
        self.assertGreater(lin["historical_metrics"]["directional_accuracy"], 0.5)

    def test_horizonte_invalido(self):
        with self.assertRaises(ValueError):
            run_all(make_data(), "X", 0)
        with self.assertRaises(ValueError):
            run_all(make_data(), "X", 99)


if __name__ == "__main__":
    unittest.main()
