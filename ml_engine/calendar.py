"""Daily session labels and UTC boundaries for supported markets."""

from datetime import timedelta
from functools import lru_cache

import exchange_calendars as xcals
import pandas as pd


CONTRACT_VERSION = "daily-v1"
CONTINUOUS = "24/7"
YAHOO_CLOSE_LAG_MINUTES = 20
# Yahoo exchange identifiers. Keep index calendars explicit where metadata describes a publisher.
EXCHANGES = {
    "NYQ": "XNYS", "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ",
    "NASDAQ": "NASDAQ", "NASDAQGS": "NASDAQ", "NASDAQGM": "NASDAQ", "NASDAQCM": "NASDAQ",
    "NYSE": "XNYS",
    "ASE": "XNYS", "PCX": "XNYS", "BTS": "XNYS", "MCE": "XMAD",
    "LSE": "XLON", "PAR": "XPAR", "GER": "XETR", "TOR": "XTSE",
    "HKG": "XHKG", "JPX": "XTKS", "ASX": "XASX",
}
INDEX_CALENDARS = {"^GSPC": "XNYS", "^DJI": "XNYS", "^IXIC": "NASDAQ", "^IBEX": "XMAD",
                   "^FTSE": "XLON", "^FCHI": "XPAR", "^GDAXI": "XETR"}


@lru_cache
def _exchange(name):
    return xcals.get_calendar(name)


def market_calendar(symbol, exchange="", quote_type=""):
    symbol = symbol.upper()
    exchange = str(exchange or "").strip().upper()
    if exchange == "CCC" or (not exchange and str(quote_type).upper() == "CRYPTOCURRENCY"):
        return CONTINUOUS
    return INDEX_CALENDARS.get(symbol) or EXCHANGES.get(exchange)


def utc_instant(value):
    instant = pd.Timestamp(value)
    if pd.isna(instant) or instant.tzinfo is None:
        raise ValueError("El instante debe incluir una zona horaria.")
    return instant.tz_convert("UTC")


def session_close(name, session):
    label = pd.Timestamp(session).date().isoformat()
    if name == CONTINUOUS:
        return pd.Timestamp(label, tz="UTC") + pd.Timedelta(days=1)
    return _exchange(name).session_close(label)


def target_session(name, origin, horizon):
    label = pd.Timestamp(origin).date()
    if name == CONTINUOUS:
        return label + timedelta(days=horizon)
    return _exchange(name).session_offset(label.isoformat(), horizon).date()


def calendar_timezone(name):
    return "UTC" if name == CONTINUOUS else str(_exchange(name).tz)


def closed_bars(data, name, cutoff, *, require_contiguous=True, lag_minutes=0):
    """Treat dates as local session labels, not timestamps of publication."""
    cutoff = utc_instant(cutoff)
    result = []
    previous = None
    for row in data:
        session = pd.Timestamp(row["fecha"]).date()
        close = session_close(name, session)
        eligible = close + pd.Timedelta(minutes=lag_minutes)
        for field in ("published_at", "available_at"):
            if row.get(field) is not None:
                eligible = max(eligible, utc_instant(row[field]))
        if eligible > cutoff:
            continue
        if previous is not None:
            if session <= previous or (require_contiguous and target_session(name, previous, 1) != session):
                raise ValueError("Serie diaria con sesiones duplicadas, desordenadas o faltantes.")
            if eligible <= pd.Timestamp(result[-1]["cutoff_at"]):
                raise ValueError("Los cierres diarios deben estar disponibles en orden cronológico.")
        result.append(dict(row, fecha=session.isoformat(), event_at=close.isoformat(), cutoff_at=eligible.isoformat()))
        previous = session
    return result
