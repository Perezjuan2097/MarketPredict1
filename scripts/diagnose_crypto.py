from datetime import datetime, timezone
import pandas as pd
from app.services.data_service import APIFinanciera
from ml_engine.calendar import closed_bars, session_close, target_session, YAHOO_CLOSE_LAG_MINUTES
from ml_engine.features import builder
now = datetime.now(timezone.utc)
print("now", now.isoformat())
for q in ["BTC-USD", "ETH-USD"]:
    r = APIFinanciera().obtener_periodo(q, "2y", "1d", forecast=True)
    print(q, "cal", r["calendar"], "raw tail", [x["fecha"] for x in r["data"][-4:]])
    bars = closed_bars(r["data"], r["calendar"], now, lag_minutes=YAHOO_CLOSE_LAG_MINUTES)
    print(" closed tail", [(b["fecha"], b["cutoff_at"]) for b in bars[-3:]])
    df = builder.to_frame(bars)
    times = pd.to_datetime(df.get("cutoff_at", df["fecha"]), format="mixed", utc=True)
    df2 = df.loc[times <= pd.Timestamp(now)].reset_index(drop=True)
    last = df2["fecha"].iloc[-1]
    print(" df last fecha", repr(last), "rows", len(df), len(df2))
    nxt = session_close(r["calendar"], target_session(r["calendar"], last, 1))
    tgt = session_close(r["calendar"], target_session(r["calendar"], last, 5))
    print(" next_available", nxt, "target_close", tgt)
