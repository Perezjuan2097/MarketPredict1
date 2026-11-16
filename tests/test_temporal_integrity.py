from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pytest

from app.services.data_service import DemoFuente
from app.services.price_provider import YahooPriceProvider
from app.services.user_predictions import evaluate_due
from ml_engine.calendar import CONTINUOUS, closed_bars, market_calendar, session_close, target_session
from ml_engine.engine import run_all
from ml_engine.features import builder
from ml_engine.model_registry import registry


def test_context_publication_and_availability_at_intraday_cutoff():
    origins = pd.DataFrame({"fecha": pd.to_datetime(
        ["2025-01-15T07:00Z", "2025-01-15T21:00Z", "2025-01-15T21:05Z"], utc=True
    )})
    rows = [
        {"event_at": "2025-01-14T21:00Z", "available_at": "2025-01-14T21:10Z", "cierre": 20},
        {"event_at": "2025-01-15T20:55Z", "published_at": "2025-01-15T21:00Z",
         "available_at": "2025-01-15T21:05Z", "cierre": 50},
        {"fecha": "2025-01-15", "cierre": 99},
    ]
    actual = builder.context_features(origins, {"^VIX": rows})
    assert actual["vix_nivel"].tolist() == [0.2, 0.2, 0.5]
    rows[1]["available_at"] = "2025-01-15T20:58Z"
    assert builder.context_features(origins, {"^VIX": rows})["vix_nivel"].tolist() == [0.2, 0.5, 0.5]


def test_future_context_cannot_change_schema_or_past_features():
    data = DemoFuente().obtener("AAPL", 65, end=date(2025, 2, 1))
    prices = builder.to_frame(data)
    old, _ = builder.build(prices, 5, {})
    future = {"^VIX": [{"event_at": "2025-02-01T00:00:00-05:00",
                        "available_at": "2025-02-01T06:00:00Z", "cierre": 47}]}
    new, _ = builder.build(prices, 5, future)
    pd.testing.assert_frame_equal(old, new)


def test_exchange_holidays_dst_early_closes_and_crypto():
    assert market_calendar("AAPL", "NMS") == "NASDAQ"
    assert market_calendar("BTC-USD", "CCC") == CONTINUOUS
    assert market_calendar("UNKNOWN", "") is None
    assert target_session("XNYS", "2025-07-03", 1) == date(2025, 7, 7)
    assert target_session("XNYS", "2025-05-02", 1) == date(2025, 5, 5)
    assert target_session(CONTINUOUS, "2025-07-04", 1) == date(2025, 7, 5)
    assert session_close("XNYS", "2025-07-03") == pd.Timestamp("2025-07-03T17:00Z")
    assert session_close("XNYS", "2025-03-07").hour == 21
    assert session_close("XNYS", "2025-03-10").hour == 20
    assert target_session("XMAD", "2025-05-30", 1) == date(2025, 6, 2)


def test_calendar_excludes_open_bars_and_rejects_missing_sessions():
    bars = [{"fecha": d, "cierre": 100} for d in ("2025-07-02", "2025-07-03", "2025-07-07")]
    assert [bar["fecha"] for bar in closed_bars(bars, "XNYS", "2025-07-03T16:59Z")] == ["2025-07-02"]
    assert len(closed_bars(bars, "XNYS", "2025-07-03T17:20Z", lag_minutes=20)) == 2
    with pytest.raises(ValueError, match="faltantes"):
        closed_bars(bars[::2], "XNYS", "2025-07-08T00:00Z")


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf, 0])
def test_nonfinite_or_nonpositive_close_is_rejected(bad):
    with pytest.raises(ValueError, match="cierres inválidos"):
        builder.to_frame([{"fecha": "2025-01-01", "cierre": bad}])


def test_target_provider_does_not_use_previous_close():
    frame = pd.DataFrame({"Close": [100.0]}, index=pd.DatetimeIndex(["2025-07-02"], tz="America/New_York"))
    with patch("app.services.price_provider.APIFinanciera._ticker") as ticker:
        ticker.return_value.history.return_value = frame
        assert YahooPriceProvider().close_on("AAPL", date(2025, 7, 3)) is None
        assert YahooPriceProvider().close_on("AAPL", date(2025, 7, 2)) == 100.0


def test_evaluation_waits_for_session_and_valid_exact_observation(caplog):
    repo, provider = Mock(), Mock(source="Yahoo Finance")
    repo.due_predictions.return_value = [{
        "id": 7, "symbol": "AAPL", "target_date": "2025-07-03", "status": "pendiente",
        "reference_price": 100, "direction": "alcista",
        "indicators": {"data_source": "Yahoo Finance", "provenance": {
            "contract_version": "daily-v1", "target_close_at": "2025-07-03T17:00:00Z", "lag_minutes": 20}},
    }]
    assert evaluate_due(repo, provider, now="2025-07-03T17:19Z")["evaluated"] == 0
    provider.close_on.assert_not_called()
    provider.close_on.return_value = np.inf
    with caplog.at_level("INFO"):
        assert evaluate_due(repo, provider, now="2025-07-03T17:20Z")["waiting_for_data"] == 1
    assert "sin cierre exacto válido" in caplog.text
    repo.save_evaluation.assert_not_called()
    provider.close_on.return_value = 109
    assert evaluate_due(repo, provider, now="2025-07-03T17:21Z")["evaluated"] == 1
    provider.close_on.assert_called_with("AAPL", date(2025, 7, 3))
    assert repo.save_evaluation.call_args.args[1]["actual_price"] == 109

    repo.due_predictions.return_value[0]["indicators"] = None
    provider.close_on.reset_mock()
    assert evaluate_due(repo, provider, now="2025-07-04T00:00Z")["waiting_for_data"] == 1
    provider.close_on.assert_not_called()


def test_same_inputs_and_seed_reproduce_forecast_with_future_rows_excluded():
    end = date(2025, 2, 1)
    source = DemoFuente()
    data = source.obtener("AAPL", 340, end=end)
    future = source.obtener("AAPL", 2, end=date(2025, 2, 3))
    settings = {"calendar": CONTINUOUS, "cutoff": "2025-02-02T07:00Z", "seed": 9}
    with patch.object(registry, "active_model_ids", return_value=["linear_regression", "random_forest"]):
        first = run_all(data, "AAPL", 5, **settings)
        second = run_all(data + future, "AAPL", 5, **settings)
    assert first == second
    assert first["target_date"] == "2025-02-06"
    assert first["provenance"]["target_close_at"] == "2025-02-07T00:00:00+00:00"
    assert first["final_evaluation"]["status"] == "independent"
    assert first["final_evaluation"]["calibration_origins"] > 0
