from unittest.mock import Mock, patch

import pandas as pd
import pytest

from app import create_app
from app.security import api_error
from app.services.data_service import APIFinanciera


def _daily(dates):
    return pd.DataFrame({"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 10},
                        index=pd.DatetimeIndex(dates, tz="UTC"))


def _hourly(start, hours):
    index = pd.date_range(start, periods=hours, freq="h", tz="UTC")
    return pd.DataFrame({"Open": range(1, hours + 1), "High": 100.0, "Low": 0.25,
                         "Close": [float(h) for h in range(1, hours + 1)], "Volume": 2}, index=index)


def test_missing_closed_crypto_day_is_rebuilt_from_its_24_hourly_bars():
    ticker = Mock()
    ticker.history.return_value = _hourly("2026-11-19 00:00", 24 + 24 + 5)
    data = APIFinanciera()._history_rows(_daily(["2026-11-18", "2026-11-19", "2026-11-21"]))
    filled = APIFinanciera()._fill_continuous_days(ticker, "BTC-USD", data, now="2026-11-21T05:00Z")
    assert [pd.Timestamp(r["fecha"]).date().isoformat() for r in filled] == [
        "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-21"]
    day = filled[2]
    assert (day["apertura"], day["maximo"], day["minimo"], day["cierre"], day["volumen"]) == (25, 100, 0.25, 48, 48)


@pytest.mark.parametrize("hours", [23, 0])
def test_incomplete_or_missing_hourly_data_invents_nothing(hours):
    ticker = Mock()
    ticker.history.return_value = _hourly("2026-11-20 00:00", hours) if hours else pd.DataFrame()
    data = APIFinanciera()._history_rows(_daily(["2026-11-19", "2026-11-21"]))
    assert APIFinanciera()._fill_continuous_days(ticker, "BTC-USD", data, now="2026-11-21T05:00Z") == data


def test_hourly_failure_keeps_the_daily_series():
    ticker = Mock()
    ticker.history.side_effect = RuntimeError("down")
    data = APIFinanciera()._history_rows(_daily(["2026-11-19", "2026-11-21"]))
    assert APIFinanciera()._fill_continuous_days(ticker, "BTC-USD", data, now="2026-11-21T05:00Z") == data


def test_own_validation_message_reaches_the_user_but_library_errors_stay_generic():
    app = create_app({"TESTING": True})
    with app.test_request_context("/api/analyze"):
        try:
            from ml_engine.engine import run_all
            run_all([], "X", horizon=99)
        except ValueError as exc:
            response, status = api_error(exc)
        assert status == 422
        assert "horizonte" in response.get_json()["error"].lower()
        try:
            int("no-es-numero")
        except ValueError as exc:
            response, _ = api_error(exc)
        assert response.get_json()["error"] == "No se pudo completar la solicitud con los datos proporcionados."


@pytest.mark.parametrize("name,symbol", [("ecopetrol", "EC"), ("bancolombia", "CIB")])
def test_colombian_names_resolve_to_their_listings(name, symbol):
    with patch.object(APIFinanciera, "search_yahoo", return_value=[]):
        assert APIFinanciera.resolve_ticker(name)["symbol"] == symbol
