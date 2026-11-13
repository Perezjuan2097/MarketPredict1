import os
from flask import Blueprint, render_template, request, jsonify, current_app, send_file

from app.services.data_service import APIFinanciera, ValidadorDatos, ProcesadorDatos
from app.services.analysis_service import AnalizadorMercado
from app.services.prediction_service import ModeloML
from app.services.alert_service import ClasificadorAlertas
from app.services.report_service import InformeEmpresarial
from app.services.storage_service import Storage
from app.services.community_service import CommunityStorage
from app.models.config import ModelConfig
import time
from ml_engine.engine import run_all
from app.services.data_service import DemoFuente
from app.security import api_error, clean_symbol

main_bp = Blueprint("main", __name__)


def pipeline(symbol):
    cfg = ModelConfig.from_env(current_app.extensions.get("runtime_config")).validate()
    symbol = clean_symbol(symbol)
    source = APIFinanciera()
    resolved = source.resolve_ticker(symbol)
    raw = source.obtener(resolved["symbol"], 90)
    data = ProcesadorDatos().procesar(ValidadorDatos().validar(raw))
    analyzer = AnalizadorMercado()
    inds = [x.__dict__ for x in analyzer.indicadores(data)]
    preds = ModeloML().predecir(data, cfg.horizon, cfg.model_version)
    alert = ClasificadorAlertas().clasificar(data, preds, cfg.alert_drop)
    return cfg, resolved, data, inds, preds, alert, analyzer.tendencia(data)


@main_bp.get("/")
def index():
    return render_template("index.html")


@main_bp.get("/api/search")
def search_assets():
    query = request.args.get("q", "").strip()[:60]
    if not query:
        return jsonify(results=[])
    results = APIFinanciera.search_yahoo(query)
    fallback = APIFinanciera.FALLBACK_TICKERS.get(query.lower())
    if fallback and not any(str(x.get("symbol", "")).upper() == fallback for x in results):
        results.insert(0, {"symbol": fallback, "shortname": query, "quoteType": "UNKNOWN", "exchange": ""})
    clean = []
    for item in results[:10]:
        clean.append({
            "symbol": item.get("symbol"),
            "name": item.get("shortname") or item.get("longname") or item.get("symbol"),
            "type": item.get("quoteType", ""),
            "exchange": item.get("exchange", ""),
        })
    return jsonify(results=clean)


@main_bp.get("/api/resolve")
def resolve_asset():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify(error="Debe indicar un activo."), 400
    try:
        return jsonify(APIFinanciera().resolve_ticker(query))
    except Exception as e:
        return api_error(e)


@main_bp.get("/api/market-data/<path:ticker>")
def market_data(ticker):
    ticker = clean_symbol(ticker)
    period_key = request.args.get("period", "1D")
    interval = request.args.get("interval")
    try:
        source = APIFinanciera()
        if period_key in source.PERIODOS:
            period, default_interval = source.PERIODOS[period_key]
            interval = interval or default_interval
        else:
            period = period_key.lower()
            interval = interval or "1d"
        result = source.obtener_periodo(ticker, period, interval)
        result["period_label"] = period_key
        return jsonify(result)
    except Exception as e:
        return api_error(e)


@main_bp.get("/api/news/<path:ticker>")
def news(ticker):
    ticker = clean_symbol(ticker)
    try:
        return jsonify(APIFinanciera().get_news(ticker))
    except Exception as e:
        return api_error(e)


@main_bp.get("/api/technical/<path:ticker>")
def technical(ticker):
    ticker = clean_symbol(ticker)
    try:
        return jsonify(APIFinanciera().get_technical(ticker))
    except Exception as e:
        return api_error(e)


@main_bp.get("/api/seasonal/<path:ticker>")
def seasonal(ticker):
    ticker = clean_symbol(ticker)
    try:
        return jsonify(APIFinanciera().get_seasonal(ticker))
    except Exception as e:
        return api_error(e)


@main_bp.get("/api/components/<path:ticker>")
def components(ticker):
    ticker = clean_symbol(ticker)
    try:
        return jsonify(APIFinanciera().get_components(ticker))
    except Exception as e:
        return api_error(e)


@main_bp.get("/api/community/<path:ticker>")
def community(ticker):
    ticker = clean_symbol(ticker)
    try:
        return jsonify(CommunityStorage(current_app.config["PROJECT_ROOT"]).get(ticker))
    except Exception as e:
        return api_error(e)


@main_bp.post("/api/community/<path:ticker>/post")
def community_post(ticker):
    ticker = clean_symbol(ticker)
    payload = request.get_json(silent=True) or {}
    try:
        post = CommunityStorage(current_app.config["PROJECT_ROOT"]).add_post(ticker, payload.get("author"), payload.get("text"))
        return jsonify(post=post), 201
    except Exception as e:
        return jsonify(error=str(e)), 400


@main_bp.post("/api/community/<path:ticker>/vote")
def community_vote(ticker):
    ticker = clean_symbol(ticker)
    payload = request.get_json(silent=True) or {}
    try:
        sentiment = CommunityStorage(current_app.config["PROJECT_ROOT"]).vote(ticker, payload.get("direction"))
        return jsonify(sentiment=sentiment)
    except Exception as e:
        return jsonify(error=str(e)), 400


@main_bp.post("/api/analyze")
def analyze():
    body = request.get_json(silent=True) if request.is_json else request.form
    symbol = str((body or {}).get("symbol", "")).strip()
    if not symbol:
        return jsonify(error="Debe ingresar un identificador de activo."), 400
    try:
        cfg, resolved, data, inds, preds, alert, tend = pipeline(symbol)
        return jsonify(
            symbol=resolved["symbol"], query=symbol, name=resolved.get("name"), data=data,
            indicators=inds, predictions=preds, alert=alert, tendency=tend,
            model_version=cfg.model_version,
            data_source="demo" if os.getenv("USE_DEMO", "false").lower() == "true" else "Yahoo Finance",
        )
    except Exception as e:
        return api_error(e)


@main_bp.post("/api/report")
def report():
    payload = request.get_json(silent=True) or {}
    symbol = payload.get("symbol", "")
    try:
        cfg, resolved, data, inds, preds, alert, tend = pipeline(symbol)
        path = InformeEmpresarial().generar(current_app.config["PROJECT_ROOT"], resolved["symbol"], data, inds, preds, alert, tend)
        return send_file(path, as_attachment=True, download_name=path.name)
    except Exception as e:
        return api_error(e)


@main_bp.post("/api/save")
def save():
    payload = request.get_json(silent=True)
    try:
        created = Storage(current_app.config["PROJECT_ROOT"]).add(payload)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, duplicate=not created)


@main_bp.get("/api/history")
def history():
    return jsonify(Storage(current_app.config["PROJECT_ROOT"]).all())


_CACHE = {}


def _cached(key, ttl, fn):
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = fn()
    _CACHE[key] = (time.monotonic(), value)
    return value


@main_bp.get("/api/markets")
def markets():
    try:
        hit = _CACHE.get("markets")
        if hit and time.monotonic() - hit[0] < 60:
            return jsonify(hit[1])
        data = APIFinanciera().get_markets()
        if any(m["price"] is not None for m in data["markets"]):  # no se cachean fallos totales
            _CACHE["markets"] = (time.monotonic(), data)
        return jsonify(data)
    except Exception as e:
        return api_error(e)


def model_result(ticker, horizon):
    """Resultado del motor de modelos (con caché de 15 min). Lo usan /api/models y el guardado de predicciones."""
    ticker = clean_symbol(ticker)
    demo = os.getenv("USE_DEMO", "false").lower() == "true"

    def compute():
        if demo:
            data, name = DemoFuente().obtener(ticker, 600), ticker.upper()
        else:
            res = APIFinanciera().obtener_periodo(ticker, "2y", "1d")
            data, name = res["data"], res["symbol"]
        out = run_all(data, name, horizon)
        out["data_source"] = "demo" if demo else "Yahoo Finance"
        return out

    return _cached(("models", ticker.upper(), int(horizon), demo), 900, compute)


@main_bp.get("/api/models/<path:ticker>")
def models(ticker):
    ticker = clean_symbol(ticker)
    try:
        horizon = int(request.args.get("horizon", "5"))
    except ValueError:
        return jsonify(error="Horizonte no válido."), 400
    try:
        return jsonify(model_result(ticker, horizon))
    except Exception as e:
        return api_error(e)


@main_bp.get("/api/health")
def health():
    """Estado del despliegue: base de datos y librerías opcionales (para verificar la instalación)."""
    from app.db import check_db
    from ml_engine.model_registry import registry
    return jsonify(status="ok", db=check_db(), optional_models=registry.optional_status(),
                   demo_mode=os.getenv("USE_DEMO", "false").lower() == "true")
