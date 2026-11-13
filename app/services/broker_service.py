"""Bróker (Alpaca, SOLO paper trading): consulta de la cuenta simulada, posiciones y órdenes.

- Siempre usa el endpoint de paper trading: la app nunca toca dinero real.
- Solo lectura: no envía órdenes.
- Claves en variables de entorno ALPACA_API_KEY y ALPACA_API_SECRET (nunca en el código ni en GitHub).
- BROKER_USERS: usuarios o correos autorizados a ver el panel, separados por comas.
"""
import logging
import os
import re

import requests

log = logging.getLogger("marketpredict")
PAPER_BASE = "https://paper-api.alpaca.markets"


def allowed_users():
    raw = os.getenv("BROKER_USERS", "")
    return {x.strip().lower() for x in re.split(r"[,;\s]+", raw) if x.strip()}


def user_allowed(user):
    """True si el usuario (dict con username/email) figura en BROKER_USERS."""
    if not user:
        return False
    names = allowed_users()
    return bool(names) and (str(user.get("username", "")).lower() in names or str(user.get("email", "")).lower() in names)


class AlpacaPaper:
    name = "Alpaca (paper trading)"

    def __init__(self, key=None, secret=None):
        self.key = (key if key is not None else os.getenv("ALPACA_API_KEY", "")).strip()
        self.secret = (secret if secret is not None else os.getenv("ALPACA_API_SECRET", "")).strip()

    @property
    def configured(self):
        return bool(self.key and self.secret)

    def _get(self, path, **params):
        try:
            resp = requests.get(f"{PAPER_BASE}{path}", params=params or None, timeout=8,
                                headers={"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret})
        except requests.RequestException as exc:
            log.warning("Alpaca no respondió: %s", type(exc).__name__)
            raise RuntimeError("Alpaca no respondió. Intente de nuevo en unos segundos.") from exc
        if resp.status_code in (401, 403):
            raise RuntimeError("Alpaca rechazó las claves. Verifique que ALPACA_API_KEY y ALPACA_API_SECRET "
                               "sean de una cuenta de PAPER trading (las claves de cuenta real no funcionan aquí).")
        if resp.status_code == 429:
            raise RuntimeError("Alpaca: límite de solicitudes alcanzado.")
        if resp.status_code != 200:
            raise RuntimeError(f"Alpaca respondió con error {resp.status_code}.")
        return resp.json()

    @staticmethod
    def _num(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def snapshot(self):
        if not self.configured:
            raise RuntimeError("Bróker sin configurar: defina ALPACA_API_KEY y ALPACA_API_SECRET en Render.")
        acc = self._get("/v2/account")
        positions = self._get("/v2/positions")
        orders = self._get("/v2/orders", status="all", limit=10, direction="desc")
        clock = self._get("/v2/clock")
        n = self._num
        equity, last_equity = n(acc.get("equity")), n(acc.get("last_equity"))
        return {
            "broker": self.name, "paper": True,
            "account": {
                "status": acc.get("status"), "currency": acc.get("currency", "USD"),
                "equity": equity, "cash": n(acc.get("cash")), "buying_power": n(acc.get("buying_power")),
                "portfolio_value": n(acc.get("portfolio_value")),
                "day_change": round(equity - last_equity, 2) if equity is not None and last_equity else None,
                "day_change_pct": round((equity / last_equity - 1) * 100, 3) if equity and last_equity else None,
            },
            "market": {"is_open": clock.get("is_open"), "next_open": clock.get("next_open"),
                       "next_close": clock.get("next_close")},
            "positions": [{
                "symbol": p.get("symbol"), "qty": n(p.get("qty")), "side": p.get("side"),
                "avg_entry_price": n(p.get("avg_entry_price")), "current_price": n(p.get("current_price")),
                "market_value": n(p.get("market_value")), "unrealized_pl": n(p.get("unrealized_pl")),
                "unrealized_plpc": round(n(p.get("unrealized_plpc")) * 100, 3) if n(p.get("unrealized_plpc")) is not None else None,
            } for p in positions],
            "orders": [{
                "symbol": o.get("symbol"), "side": o.get("side"), "type": o.get("type"), "qty": n(o.get("qty")),
                "filled_qty": n(o.get("filled_qty")), "filled_avg_price": n(o.get("filled_avg_price")),
                "status": o.get("status"), "submitted_at": o.get("submitted_at"),
            } for o in orders],
        }
