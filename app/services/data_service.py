import json
import math
import os
import random
import urllib.parse
import urllib.request
from datetime import date, timedelta

from app.models.domain import DatosMercado

try:
    import yfinance as yf
except ImportError:
    yf = None


class FuenteDatos:
    def obtener(self, simbolo: str, dias: int = 90):
        raise NotImplementedError


class APIFinanciera(FuenteDatos):
    PERIODOS = {
        "1D": ("1d", "5m"),
        "5D": ("5d", "15m"),
        "1M": ("1mo", "1h"),
        "6M": ("6mo", "1d"),
        "YTD": ("ytd", "1d"),
        "1A": ("1y", "1d"),
        "5A": ("5y", "1wk"),
        "10A": ("10y", "1wk"),
        "Todo": ("max", "1mo"),
    }

    FALLBACK_TICKERS = {
        "ibex": "^IBEX", "ibex 35": "^IBEX", "indice ibex": "^IBEX",
        "s&p 500": "^GSPC", "sp 500": "^GSPC", "s&p500": "^GSPC",
        "nasdaq": "^IXIC", "nasdaq composite": "^IXIC", "dow jones": "^DJI",
        "ftse 100": "^FTSE", "dax": "^GDAXI", "cac 40": "^FCHI",
        "bitcoin": "BTC-USD", "btc": "BTC-USD", "ethereum": "ETH-USD", "eth": "ETH-USD",
        "repsol": "REP.MC", "solaria": "SLR.MC", "santander": "SAN.MC",
        "banco santander": "SAN.MC", "telefonica": "TEF.MC", "iberdrola": "IBE.MC",
        "inditex": "ITX.MC", "bbva": "BBVA.MC", "caixabank": "CABK.MC",
        "apple": "AAPL", "tesla": "TSLA", "microsoft": "MSFT", "amazon": "AMZN",
        "google": "GOOGL", "alphabet": "GOOGL", "nvidia": "NVDA", "meta": "META",
    }

    INDEX_COMPONENTS = {
        "^IBEX": [
            ("IBE.MC", "Iberdrola"), ("ITX.MC", "Inditex"), ("SAN.MC", "Banco Santander"),
            ("BBVA.MC", "BBVA"), ("REP.MC", "Repsol"), ("TEF.MC", "Telefónica"),
            ("CABK.MC", "CaixaBank"), ("FER.MC", "Ferrovial"), ("AMS.MC", "Amadeus"),
            ("AENA.MC", "Aena"),
        ],
        "^GSPC": [
            ("NVDA", "NVIDIA"), ("AAPL", "Apple"), ("MSFT", "Microsoft"),
            ("AMZN", "Amazon"), ("META", "Meta Platforms"), ("GOOGL", "Alphabet"),
            ("AVGO", "Broadcom"), ("GOOG", "Alphabet Class C"), ("TSLA", "Tesla"),
            ("BRK-B", "Berkshire Hathaway"),
        ],
        "^IXIC": [
            ("NVDA", "NVIDIA"), ("AAPL", "Apple"), ("MSFT", "Microsoft"),
            ("AMZN", "Amazon"), ("META", "Meta Platforms"), ("AVGO", "Broadcom"),
            ("GOOGL", "Alphabet"), ("TSLA", "Tesla"),
        ],
    }

    def obtener(self, simbolo: str, dias: int = 90):
        if os.getenv("USE_DEMO", "false").lower() == "true":
            return DemoFuente().obtener(simbolo, dias)
        resolved = self.resolve_ticker(simbolo)
        period = "3mo" if dias <= 100 else "1y"
        return self.obtener_periodo(resolved["symbol"], period, "1d")["data"]

    @classmethod
    def resolve_ticker(cls, query):
        query = (query or "").strip()
        if not query:
            raise ValueError("Debe indicar un activo.")

        normalized = " ".join(query.lower().split())
        fallback = cls.FALLBACK_TICKERS.get(normalized)

        if query.upper().startswith("^") or ("." in query and query.upper() == query):
            return {"symbol": query.upper(), "name": query.upper(), "quote_type": "UNKNOWN", "exchange": ""}

        results = cls.search_yahoo(query)
        if results:
            selected = cls._select_search_result(results, normalized, fallback)
            if selected:
                return selected

        if fallback:
            return {"symbol": fallback, "name": query, "quote_type": "UNKNOWN", "exchange": ""}

        # Si Yahoo no responde y el usuario escribió un ticker reconocible,
        # lo dejamos pasar para que yfinance sea quien lo valide.
        return {"symbol": query.upper(), "name": query, "quote_type": "UNKNOWN", "exchange": ""}

    @staticmethod
    def search_yahoo(query):
        url = "https://query2.finance.yahoo.com/v1/finance/search?" + urllib.parse.urlencode({
            "q": query, "quotesCount": 12, "newsCount": 0, "enableFuzzyQuery": "true"
        })
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return payload.get("quotes", []) or []
        except Exception:
            return []

    @classmethod
    def _select_search_result(cls, results, normalized, fallback):
        def score(item):
            symbol = str(item.get("symbol", "")).upper()
            name = str(item.get("shortname") or item.get("longname") or "").lower()
            quote_type = str(item.get("quoteType", "")).upper()
            exchange = str(item.get("exchange", "")).upper()
            value = 0
            if fallback and symbol == fallback:
                value += 1000
            if symbol.lower() == normalized:
                value += 300
            if name == normalized:
                value += 260
            if normalized in name:
                value += 100
            if symbol.startswith("^") and any(word in normalized for word in ("ibex", "s&p", "sp 500", "nasdaq", "dow", "dax", "ftse", "cac")):
                value += 160
            if normalized in cls.FALLBACK_TICKERS and symbol == cls.FALLBACK_TICKERS[normalized]:
                value += 800
            if symbol.endswith(".MC") and any(word in normalized for word in ("repsol", "solaria", "santander", "bbva", "telefonica", "iberdrola", "inditex", "caixabank")):
                value += 180
            if exchange in {"MCE", "MAD"} and ".MC" in symbol:
                value += 50
            if quote_type in {"EQUITY", "ETF", "INDEX", "CRYPTOCURRENCY"}:
                value += 15
            return value

        best = max(results, key=score)
        symbol = best.get("symbol")
        if not symbol:
            return None
        return {
            "symbol": symbol,
            "name": best.get("shortname") or best.get("longname") or symbol,
            "quote_type": best.get("quoteType", ""),
            "exchange": best.get("exchange", ""),
        }

    def obtener_periodo(self, simbolo: str, period: str, interval: str):
        resolved = self.resolve_ticker(simbolo)
        official = resolved["symbol"]
        ticker = self._ticker(official)
        frame = ticker.history(period=period, interval=interval, auto_adjust=False, actions=False, prepost=False)
        if frame is None or frame.empty:
            raise ValueError(f"No se encontraron datos para el activo {official}.")

        data = []
        for timestamp, row in frame.iterrows():
            close = self._number(row.get("Close"))
            open_ = self._number(row.get("Open"))
            high = self._number(row.get("High"))
            low = self._number(row.get("Low"))
            volume = self._number(row.get("Volume"), 0)
            if close is None or close <= 0:
                continue
            data.append({
                "fecha": self._timestamp(timestamp),
                "apertura": round(open_ if open_ is not None else close, 6),
                "maximo": round(high if high is not None else close, 6),
                "minimo": round(low if low is not None else close, 6),
                "cierre": round(close, 6),
                "volumen": int(volume or 0),
                "variacion": 0.0,
            })

        if not data:
            raise ValueError(f"No hay observaciones válidas para {official}.")

        for i, item in enumerate(data):
            previous = data[i - 1]["cierre"] if i else item["apertura"]
            item["variacion"] = round(((item["cierre"] - previous) / previous) * 100, 4) if previous else 0.0

        info = self._safe_info(ticker)
        current_price = data[-1]["cierre"]
        try:
            fast_info = ticker.fast_info
            candidate = fast_info.get("last_price") if hasattr(fast_info, "get") else None
            if candidate is not None and math.isfinite(float(candidate)):
                current_price = float(candidate)
        except Exception:
            pass

        previous_close = self._number(info.get("previousClose"))
        if previous_close is None:
            try:
                daily = ticker.history(period="5d", interval="1d", auto_adjust=False, actions=False, prepost=False)
                closes = [self._number(v) for v in daily["Close"].tolist() if self._number(v) is not None]
                if len(closes) >= 2:
                    previous_close = closes[-2]
            except Exception:
                pass
        if previous_close is None and len(data) >= 2:
            previous_close = data[-2]["cierre"]

        change_absolute = current_price - previous_close if previous_close else 0.0
        change_percent = (change_absolute / previous_close) * 100 if previous_close else 0.0
        currency = info.get("currency") or self._currency(ticker)
        name = info.get("longName") or info.get("shortName") or resolved.get("name") or official

        return {
            "symbol": official,
            "query": simbolo,
            "name": name,
            "provider": "Yahoo Finance",
            "currency": currency,
            "quote_type": info.get("quoteType") or resolved.get("quote_type", ""),
            "exchange": info.get("exchange") or resolved.get("exchange", ""),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "interval": interval,
            "period": period,
            "current_price": round(current_price, 6),
            "previous_close": round(previous_close, 6) if previous_close is not None else None,
            "change_absolute": round(change_absolute, 6),
            "change_percent": round(change_percent, 4),
            "last_timestamp": data[-1]["fecha"],
            "data": data,
        }

    def get_news(self, simbolo, limit=10):
        resolved = self.resolve_ticker(simbolo)
        ticker = self._ticker(resolved["symbol"])
        try:
            news = ticker.news or []
        except Exception:
            news = []
        result = []
        for item in news[:limit]:
            content = item.get("content", item) if isinstance(item, dict) else {}
            title = content.get("title") or item.get("title")
            provider = content.get("provider", {}) or {}
            publisher = provider.get("displayName") if isinstance(provider, dict) else None
            publisher = publisher or item.get("publisher") or "Yahoo Finance"
            link = content.get("canonicalUrl", {}) or {}
            link = link.get("url") if isinstance(link, dict) else link
            link = link or item.get("link") or item.get("url")
            timestamp = content.get("pubDate") or item.get("providerPublishTime")
            if isinstance(timestamp, (int, float)):
                from datetime import datetime, timezone
                timestamp = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
            if title:
                result.append({"title": title, "source": publisher, "date": timestamp, "url": link})
        return {"symbol": resolved["symbol"], "news": result}

    def get_technical(self, simbolo):
        resolved = self.resolve_ticker(simbolo)
        ticker = self._ticker(resolved["symbol"])
        info = self._safe_info(ticker)
        fields = [
            ("Capitalización de mercado", "marketCap"), ("PER (P/E)", "trailingPE"),
            ("Beta", "beta"), ("Máximo 52 semanas", "fiftyTwoWeekHigh"),
            ("Mínimo 52 semanas", "fiftyTwoWeekLow"), ("Volumen promedio", "averageVolume"),
            ("Dividendo", "dividendYield"), ("Tasa de dividendo", "dividendRate"),
        ]
        return {"symbol": resolved["symbol"], "currency": info.get("currency") or self._currency(ticker), "metrics": [{"label": label, "key": key, "value": info.get(key)} for label, key in fields]}

    def get_seasonal(self, simbolo):
        resolved = self.resolve_ticker(simbolo)
        ticker = self._ticker(resolved["symbol"])
        frame = ticker.history(period="10y", interval="1mo", auto_adjust=False, actions=False, prepost=False)
        if frame is None or frame.empty:
            raise ValueError("No hay suficiente histórico para calcular estacionalidad.")
        closes = frame["Close"].dropna()
        returns = closes.pct_change() * 100
        grouped = {}
        for timestamp, value in returns.dropna().items():
            month = int(timestamp.month)
            grouped.setdefault(month, []).append(float(value))
        names = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        rows = []
        for month in range(1, 13):
            values = grouped.get(month, [])
            rows.append({"month": month, "name": names[month - 1], "average_return": round(sum(values) / len(values), 3) if values else None, "observations": len(values)})
        return {"symbol": resolved["symbol"], "years": 10, "rows": rows}

    def get_components(self, simbolo):
        resolved = self.resolve_ticker(simbolo)
        symbol = resolved["symbol"]
        ticker = self._ticker(symbol)
        info = self._safe_info(ticker)
        quote_type = str(info.get("quoteType") or resolved.get("quote_type") or "").upper()
        if quote_type == "INDEX" or symbol.startswith("^"):
            base = self.INDEX_COMPONENTS.get(symbol, [])
            rows = self._quote_variations(base)
            return {"symbol": symbol, "mode": "index", "source": "Lista de referencia; los pesos no están disponibles directamente en yfinance.", "components": rows}

        sector = info.get("sector") or ""
        industry = info.get("industry") or ""
        peers = self._competitors(symbol, sector, industry)
        return {"symbol": symbol, "mode": "sector", "sector": sector, "industry": industry, "components": self._quote_variations(peers)}

    def _competitors(self, symbol, sector, industry):
        common = {
            "Technology": [("AAPL", "Apple"), ("MSFT", "Microsoft"), ("NVDA", "NVIDIA"), ("GOOGL", "Alphabet")],
            "Consumer Cyclical": [("AMZN", "Amazon"), ("TSLA", "Tesla"), ("HD", "Home Depot"), ("NKE", "Nike")],
            "Financial Services": [("JPM", "JPMorgan"), ("BAC", "Bank of America"), ("GS", "Goldman Sachs"), ("MS", "Morgan Stanley")],
            "Healthcare": [("JNJ", "Johnson & Johnson"), ("PFE", "Pfizer"), ("MRK", "Merck"), ("LLY", "Eli Lilly")],
            "Energy": [("XOM", "Exxon Mobil"), ("CVX", "Chevron"), ("SHEL", "Shell"), ("COP", "ConocoPhillips")],
        }
        return [item for item in common.get(sector, []) if item[0] != symbol][:6]

    def _quote_variations(self, items):
        rows = []
        for symbol, name in items[:10]:
            try:
                frame = self._ticker(symbol).history(period="5d", interval="1d", auto_adjust=False, actions=False, prepost=False)
                closes = frame["Close"].dropna().tolist()
                change = ((closes[-1] - closes[-2]) / closes[-2] * 100) if len(closes) >= 2 else None
                rows.append({"symbol": symbol, "name": name, "weight": None, "change_percent": round(change, 3) if change is not None else None})
            except Exception:
                rows.append({"symbol": symbol, "name": name, "weight": None, "change_percent": None})
        return rows

    @staticmethod
    def _ticker(symbol):
        if yf is None:
            raise RuntimeError("yfinance no está instalado. Ejecute: pip install yfinance==1.7.0")
        return yf.Ticker(symbol)

    @staticmethod
    def _safe_info(ticker):
        try:
            return ticker.info or {}
        except Exception:
            return {}

    @staticmethod
    def _number(value, default=None):
        try:
            number = float(value)
            return number if math.isfinite(number) else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _timestamp(value):
        try:
            return value.isoformat()
        except AttributeError:
            return str(value)

    @staticmethod
    def _currency(ticker):
        try:
            value = ticker.fast_info.get("currency")
            return value or "USD"
        except Exception:
            return "USD"


class DemoFuente(FuenteDatos):
    def obtener(self, simbolo, dias=90):
        seed = sum(ord(c) for c in simbolo.upper())
        rng = random.Random(seed)
        base = 100 + (seed % 90)
        result, prev = [], base
        start = date.today() - timedelta(days=dias - 1)
        for i in range(dias):
            trend = 0.05 * i + 3 * math.sin(i / 9)
            close = max(1, base + trend + rng.uniform(-2.2, 2.2))
            open_ = prev + rng.uniform(-1.2, 1.2)
            high = max(open_, close) + rng.uniform(0.2, 2)
            low = min(open_, close) - rng.uniform(0.2, 2)
            vol = int(50000 + rng.random() * 450000)
            var = ((close - prev) / prev) * 100 if prev else 0
            result.append(DatosMercado((start + timedelta(days=i)).isoformat(), open_, high, low, close, vol, var).__dict__)
            prev = close
        return result


class ValidadorDatos:
    def validar(self, datos):
        if not datos:
            raise ValueError("La fuente no entregó datos.")
        required = ["fecha", "apertura", "maximo", "minimo", "cierre", "volumen"]
        clean = []
        for x in datos:
            if not all(k in x for k in required):
                continue
            if x["cierre"] <= 0 or x["minimo"] <= 0:
                continue
            clean.append(x)
        if len(clean) < 20:
            raise ValueError("Datos insuficientes: se requieren al menos 20 observaciones válidas.")
        return clean


class ProcesadorDatos:
    def procesar(self, datos):
        for i, x in enumerate(datos):
            prev = datos[i - 1]["cierre"] if i else x["apertura"]
            x["variacion"] = round(((x["cierre"] - prev) / prev) * 100, 3) if prev else 0
        return datos
