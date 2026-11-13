"""Finnhub (opcional): noticias de empresa. Requiere FINNHUB_API_KEY (plan gratuito: finnhub.io/register).

Limitaciones del plan gratuito: noticias de empresa solo para valores de EE. UU. y ~60 llamadas/min.
Sin clave configurada, el proveedor informa "configuración pendiente" y no inventa datos.
"""
import logging
import os
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger("marketpredict")
BASE = "https://finnhub.io/api/v1"


class FinnhubProvider:
    name = "Finnhub"

    def __init__(self, api_key=None):
        self.api_key = api_key if api_key is not None else os.getenv("FINNHUB_API_KEY", "").strip()

    @property
    def configured(self):
        return bool(self.api_key)

    def company_news(self, symbol, days=7, limit=10):
        """Lista normalizada de noticias; lanza RuntimeError si no hay clave o falla la solicitud."""
        if not self.configured:
            raise RuntimeError("Finnhub: configuración pendiente (defina FINNHUB_API_KEY).")
        today = datetime.now(timezone.utc).date()
        try:
            resp = requests.get(f"{BASE}/company-news", timeout=8, headers={"X-Finnhub-Token": self.api_key},
                                params={"symbol": symbol, "from": (today - timedelta(days=days)).isoformat(), "to": today.isoformat()})
        except requests.RequestException as exc:
            log.warning("Finnhub no respondió: %s", type(exc).__name__)
            raise RuntimeError("Finnhub no respondió.") from exc
        if resp.status_code == 429:
            raise RuntimeError("Finnhub: límite de solicitudes alcanzado.")
        if resp.status_code in (401, 403):
            raise RuntimeError("Finnhub: clave no válida o sin permiso para este activo.")
        if resp.status_code != 200:
            raise RuntimeError(f"Finnhub respondió con error {resp.status_code}.")
        out = []
        for item in (resp.json() or [])[:limit]:
            if not item.get("headline"):
                continue
            ts = item.get("datetime")
            out.append({"title": item["headline"], "source": item.get("source") or "Finnhub",
                        "date": datetime.fromtimestamp(ts, timezone.utc).isoformat() if isinstance(ts, (int, float)) else None,
                        "url": item.get("url"), "summary": (item.get("summary") or "")[:400] or None})
        return out
