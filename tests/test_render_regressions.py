from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import pandas as pd
import pytest

from app import create_app
from app.services.data_service import APIFinanciera, MarketDataError
from app.services.price_provider import YahooPriceProvider


@pytest.fixture
def ticker():
    result = Mock()
    result.history.return_value = pd.DataFrame(
        {"Close": [100.0]}, index=pd.DatetimeIndex(["2025-07-03"], tz="America/New_York"),
    )
    result.get_history_metadata.return_value = {}
    result.get_info.return_value = {}
    with patch.object(APIFinanciera, "_ticker", return_value=result), \
         patch.object(APIFinanciera, "search_yahoo", return_value=[]):
        yield result


@pytest.mark.parametrize("symbol,status", [("NVDA", 401), ("AAPL", 429)])
def test_known_listing_survives_yahoo_metadata_failure(ticker, symbol, status):
    ticker.get_history_metadata.side_effect = HTTPError("https://example.invalid", status, "Unavailable", None, None)
    result = YahooPriceProvider().forecast_reference(symbol, datetime(2025, 7, 3, 17, 20, tzinfo=timezone.utc))
    assert result["symbol"] == symbol
    assert result["calendar"] == "NASDAQ"
    assert result["as_of"] == "2025-07-03"
    ticker.get_info.assert_not_called()


@pytest.mark.parametrize("status", [401, 429])
def test_history_provider_failure_is_not_an_unknown_calendar(ticker, status, monkeypatch):
    monkeypatch.setenv("USE_DEMO", "false")
    ticker.history.side_effect = HTTPError("https://example.invalid", status, "Unavailable", None, None)
    response = create_app({"TESTING": True}).test_client().get("/api/models/NVDA")
    assert response.status_code == 503
    assert response.json["code"] == "provider_unavailable"


@pytest.mark.parametrize("metadata,code", [
    ({"symbol": "OTHER", "exchangeName": "UNSUPPORTED"}, "unsupported_calendar"),
    ({"symbol": "OTHER", "instrumentType": "EQUITY"}, "unverified_instrument"),
    ({"symbol": "DIFFERENT", "exchangeName": "NMS"}, "provider_mismatch"),
])
def test_chart_metadata_distinguishes_unsupported_unknown_and_wrong_symbol(ticker, metadata, code):
    ticker.get_history_metadata.return_value = metadata
    with pytest.raises(MarketDataError) as error:
        APIFinanciera().obtener_periodo("OTHER", "2y", "1d", forecast=True)
    assert error.value.code == code


def test_chart_exchange_takes_precedence_over_catalog(ticker):
    ticker.get_history_metadata.return_value = {"symbol": "NVDA", "exchangeName": "NYQ", "currency": "USD"}
    result = APIFinanciera().obtener_periodo("NVDA", "2y", "1d", forecast=True)
    assert result["calendar"] == "XNYS"
    assert result["currency"] == "USD"
    assert ticker.history.call_args.kwargs["raise_errors"] is True


def test_unknown_placeholder_does_not_discard_verified_listing(ticker):
    ticker.get_history_metadata.return_value = {"symbol": "AAPL", "exchangeName": "UNKNOWN"}
    assert APIFinanciera().obtener_periodo("AAPL", "2y", "1d", forecast=True)["calendar"] == "NASDAQ"


def test_fuzzy_search_cannot_replace_explicit_unknown_symbol(ticker):
    with patch.object(APIFinanciera, "search_yahoo", return_value=[{"symbol": "NVDA", "shortname": "NVIDIA", "exchange": "NMS"}]):
        with pytest.raises(MarketDataError, match="NVDAA"):
            APIFinanciera().obtener_periodo("NVDAA", "2y", "1d", forecast=True)


def test_old_schema_reports_migration_and_never_queries_missing_key(monkeypatch):
    from sqlalchemy import create_engine

    engine = create_engine("sqlite://")
    try:
        with engine.begin() as bind:
            bind.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
            bind.exec_driver_sql("CREATE TABLE portfolios (id INTEGER PRIMARY KEY)")
        monkeypatch.setenv("DATABASE_URL", "sqlite://")
        monkeypatch.setenv("USE_MEMORY_DB", "true")
        with patch("app.db.get_engine", return_value=engine), patch("app.repositories.factory.get_engine", return_value=engine):
            client = create_app({"TESTING": True}).test_client()
            health = client.get("/api/health").json["db"]
            token = client.get("/api/auth/me").json
            response = client.post("/api/auth/login", json={}, headers={"X-CSRF-Token": token["csrf"]})
        assert health["status"] == "migracion_requerida"
        assert "users.username_key" in health["missing"]
        assert token["persistence"] is False
        assert response.status_code == 503
    finally:
        engine.dispose()
