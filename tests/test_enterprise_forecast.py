"""Predictive contract and temporal evaluation tests for private enterprise data."""
from datetime import date, timedelta

import numpy as np
import pytest

from app.security import InvalidInput
from app.services import enterprise_forecast as forecast


def _dates(count):
    return [date(2025, 1, 1) + timedelta(days=i) for i in range(count)]


def test_preparation_keeps_returns_and_distinguishes_explicit_zero_from_unknown():
    days = _dates(10)
    rows = [(days[0], 10000, 1, 0), (days[2], -25000, 2, 1), (days[3], 5000000, 1, 0)]
    with pytest.raises(InvalidInput, match="sin información"):
        forecast.prepare(rows, days[0], days[3], [])
    with pytest.raises(InvalidInput, match="sólo fechas sin registros"):
        forecast.prepare(rows, days[0], days[3], [days[0], days[1]])
    dates, series, audit = forecast.prepare(rows, days[0], days[3], [days[1]])
    assert dates == days[:4]
    assert series.tolist() == [1.0, 0.0, -2.5, 500.0]
    assert audit["negative_source_rows"] == audit["zero_days_declared"] == audit["outlier_days"] == 1
    assert audit["series_sha256"] == forecast.prepare(rows[::-1], days[0], days[3], [days[1]])[2]["series_sha256"]
    with pytest.raises(InvalidInput, match="730"):
        forecast.prepare(rows, days[0], days[0] + timedelta(days=730), [])


def test_weekly_reference_and_reproducibility():
    days = _dates(105)
    values = np.resize(np.array([4, 5, 7, 12, 15, 8, 6], dtype=float), len(days))
    result = forecast.evaluate(days, values, 3, scenario_pct=20)
    assert result == forecast.evaluate(days, values, 3, scenario_pct=20)
    assert result["baseline"] == result["selected_model"] == "seasonal_naive"
    assert not result["beats_baseline_on_final"]
    assert result["models"]["seasonal_naive"]["final"]["mae"] == 0
    assert result["interval"]["coverage_final"] == 1
    assert result["scenario"]["kind"] == "hypothetical"
    assert result["scenario"]["points"][0]["value"] == pytest.approx(result["forecast"][0]["value"] * 1.2)
    assert "random_forest" not in result["models"]
    assert "arima" not in result["models"]
    with pytest.raises(InvalidInput, match="días completos"):
        forecast.evaluate(days[:20], values[:20], 1)


def test_late_outcomes_do_not_choose_candidate_or_change_calibration(monkeypatch):
    days = _dates(125)
    values = np.arange(len(days), dtype=float) + np.tile([1.0, -0.5, 0.1, 1.5, -1.0], 25)
    calls = []
    predict = forecast._predict

    def record(model, prefix, h):
        calls.append((model, len(prefix)))
        np.testing.assert_array_equal(prefix, modified[:len(prefix)])
        return predict(model, prefix, h)

    reference = forecast.evaluate(days, values, 3)
    modified = values.copy()
    modified[-10:] += 1000
    monkeypatch.setattr(forecast, "_predict", record)
    observed = forecast.evaluate(days, modified, 3)
    assert reference["selected_model"] == observed["selected_model"]
    assert reference["baseline"] == observed["baseline"]
    assert {m: s["selection"] for m, s in reference["models"].items()} == {
        m: s["selection"] for m, s in observed["models"].items()}
    width = lambda output: output["forecast"][0]["high"] - output["forecast"][0]["low"]
    assert width(reference) == pytest.approx(width(observed))
    assert observed["models"][observed["selected_model"]]["final"]["mae"] != (
        reference["models"][reference["selected_model"]]["final"]["mae"])
    assert min(size for _, size in calls) == len(days) - forecast.ORIGINS * 3
    assert not observed["beats_baseline_on_final"] or observed["models"][observed["selected_model"]]["final"]["mae"] < (
        observed["models"][observed["baseline"]]["final"]["mae"])


def test_random_forest_runs_only_with_sufficient_training_prefix():
    days = _dates(130)
    values = np.linspace(2, 12, len(days)) + np.sin(np.arange(len(days)) * 2 * np.pi / 7)
    result = forecast.evaluate(days, values, 1)
    assert "random_forest" in result["models"]
    assert result["models"]["random_forest"]["final"]["observations"] == 4


def test_optional_arima_fit():
    pytest.importorskip("statsmodels")
    rng = np.random.default_rng(45)
    values = np.empty(170)
    values[0] = 30
    for i in range(1, len(values)):
        values[i] = 10 + 0.7 * values[i - 1] + rng.normal()
    forecasted = forecast._checked_forecast("arima", values, 5)
    assert np.isfinite(forecasted).all()
