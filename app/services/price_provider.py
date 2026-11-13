"""Proveedores de precio para el simulador y la evaluación de predicciones (interfaz mínima)."""
from datetime import date, timedelta

from app.services.data_service import APIFinanciera, DemoFuente


class YahooPriceProvider:
    source = "Yahoo Finance"

    def latest(self, symbol):
        res = APIFinanciera().obtener_periodo(symbol, "5d", "1d")
        last = res["data"][-1]
        return {"symbol": res["symbol"], "name": res.get("name"), "price": float(last["cierre"]),
                "currency": res.get("currency") or "USD", "as_of": last["fecha"], "source": self.source}

    def close_on_or_before(self, symbol, target):
        """Último cierre diario con fecha <= target (date). None si no hay datos."""
        api = APIFinanciera()
        official = api.resolve_ticker(symbol)["symbol"]
        frame = api._ticker(official).history(start=(target - timedelta(days=10)).isoformat(),
                                              end=(target + timedelta(days=1)).isoformat(), interval="1d",
                                              auto_adjust=False, actions=False)
        if frame is None or frame.empty:
            return None
        closes = [(ts.date(), float(v)) for ts, v in frame["Close"].dropna().items() if ts.date() <= target]
        return closes[-1][1] if closes else None


class DemoPriceProvider:
    """Solo para desarrollo sin red (USE_DEMO=true): precios SINTÉTICOS, no de mercado."""
    source = "demo"

    def latest(self, symbol):
        data = DemoFuente().obtener(symbol, 30)
        return {"symbol": symbol.upper(), "name": symbol.upper(), "price": float(data[-1]["cierre"]), "currency": "USD",
                "as_of": data[-1]["fecha"], "source": self.source}

    def close_on_or_before(self, symbol, target):
        data = [d for d in DemoFuente().obtener(symbol, 400) if date.fromisoformat(d["fecha"]) <= target]
        return float(data[-1]["cierre"]) if data else None
