"""Proveedores de precio para el simulador y la evaluación de predicciones (interfaz mínima)."""
from datetime import timedelta
from math import isfinite

from app.services.data_service import APIFinanciera, DemoFuente
from ml_engine.calendar import CONTINUOUS, YAHOO_CLOSE_LAG_MINUTES, closed_bars


class YahooPriceProvider:
    source = "Yahoo Finance"

    def latest(self, symbol):
        res = APIFinanciera().obtener_periodo(symbol, "5d", "1d")
        last = res["data"][-1]
        return {"symbol": res["symbol"], "name": res.get("name"), "price": float(last["cierre"]),
                "currency": res.get("currency") or "USD", "as_of": last["fecha"], "source": self.source}

    def forecast_reference(self, symbol, now):
        res = APIFinanciera().obtener_periodo(symbol, "5d", "1d", forecast=True)
        calendar = res["calendar"]
        bars = closed_bars(res["data"], calendar, now, lag_minutes=YAHOO_CLOSE_LAG_MINUTES)
        if not bars:
            raise ValueError("No hay un cierre diario disponible.")
        last = bars[-1]
        return {"symbol": res["symbol"], "name": res["name"], "currency": res["currency"],
                "price": last["cierre"], "as_of": last["fecha"], "calendar": calendar,
                "lag_minutes": YAHOO_CLOSE_LAG_MINUTES, "source": self.source}

    def close_on(self, symbol, target):
        """Return the exact daily close for the requested session, or None."""
        frame = APIFinanciera._ticker(symbol).history(
            start=target.isoformat(), end=(target + timedelta(days=1)).isoformat(),
            interval="1d", auto_adjust=False, actions=False, prepost=False,
        )
        if frame is None or frame.empty:
            return None
        closes = [float(value) for ts, value in frame["Close"].items() if ts.date() == target]
        if len(closes) == 1 and isfinite(closes[0]) and closes[0] > 0:
            return closes[0]
        return None


class DemoPriceProvider:
    """Solo para desarrollo sin red (USE_DEMO=true): precios SINTÉTICOS, no de mercado."""
    source = "demo"

    def latest(self, symbol):
        data = DemoFuente().obtener(symbol, 30)
        return {"symbol": symbol.upper(), "name": symbol.upper(), "price": float(data[-1]["cierre"]), "currency": "USD",
                "as_of": data[-1]["fecha"], "source": self.source}

    def forecast_reference(self, symbol, now):
        last = DemoFuente().obtener(symbol, 1, end=now.date() - timedelta(days=1))[0]
        return {"symbol": symbol.upper(), "name": symbol.upper(), "currency": "USD",
                "price": last["cierre"], "as_of": last["fecha"], "calendar": CONTINUOUS, "source": self.source}

    def close_on(self, symbol, target):
        return float(DemoFuente().obtener(symbol, 1, end=target)[0]["cierre"])
