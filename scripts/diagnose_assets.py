"""Diagnóstico de solo lectura: qué activos fallan en el análisis y por qué (datos reales de Yahoo)."""
import sys
import traceback
from datetime import datetime, timezone

from app.services.data_service import APIFinanciera, ValidadorDatos
from ml_engine.calendar import YAHOO_CLOSE_LAG_MINUTES
from ml_engine.engine import run_all

ASSETS = sys.argv[1:] or [
    "AAPL", "MSFT", "TSLA", "NVDA", "AMZN", "GOOGL", "META", "NFLX", "KO", "JPM",
    "SPY", "QQQ", "GLD", "^GSPC", "^IXIC", "^DJI", "^IBEX",
    "BTC-USD", "ETH-USD", "SOL-USD",
    "EC", "CIB", "SAN.MC", "ITX.MC", "TEF.MC", "BBVA.MC", "IBE.MC",
    "apple", "tesla", "bitcoin", "ecopetrol", "bancolombia", "nvidia", "amazon", "google",
    "EURUSD=X", "GC=F", "CL=F",
]
now = datetime.now(timezone.utc)
for q in ASSETS:
    stage = "historial 3 meses"
    try:
        r = APIFinanciera().obtener_periodo(q, "3mo", "1d", forecast=True)
        ValidadorDatos().validar(r["data"])
        stage = "historial 2 años"
        r2 = APIFinanciera().obtener_periodo(q, "2y", "1d", forecast=True)
        stage = "motor de modelos"
        out = run_all(r2["data"], r2["symbol"], 5, calendar=r2["calendar"], cutoff=now,
                      lag_minutes=YAHOO_CLOSE_LAG_MINUTES)
        print(f"OK    {q:12} -> {r2['symbol']:10} cal={r2['calendar']:7} filas={len(r2['data'])} "
              f"ultimo={r2['data'][-1]['fecha']} consenso={(out.get('consensus') or {}).get('status')}")
    except Exception as exc:
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        print(f"FALLA {q:12} [{stage}] {type(exc).__name__}: {exc}  ({frame.filename.split('/')[-2:]}:{frame.lineno})")
