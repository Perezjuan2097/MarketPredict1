import os
from flask import Blueprint, render_template, request, jsonify, current_app, send_file

from app.services.data_service import APIFinanciera, ValidadorDatos, ProcesadorDatos
from app.services.analysis_service import AnalizadorMercado
from app.services.prediction_service import predicciones_desde_motor
from app.services.alert_service import alerta_desde_motor
from app.services.report_service import InformeEmpresarial
from app.services.storage_service import Storage
from app.services.community_service import CommunityStorage
from app.models.config import ModelConfig
import time
from datetime import datetime, timezone
from ml_engine.engine import run_all
from ml_engine.calendar import CONTINUOUS, YAHOO_CLOSE_LAG_MINUTES, utc_instant
from app.services.data_service import DemoFuente
from app.services import forecast_emissions
from app.security import InvalidInput, api_error, check_csrf_header, clean_symbol, json_object, legacy_owner_key, require_user

main_bp = Blueprint("main", __name__)


def pipeline(symbol):
    cfg = ModelConfig.from_env(current_app.extensions.get("runtime_config")).validate()
    symbol = clean_symbol(symbol)
    source = APIFinanciera()
    if os.getenv("USE_DEMO", "false").lower() == "true":
        resolved = source.resolve_ticker(symbol)
        raw = source.obtener(resolved["symbol"], 90)
    else:
        resolved = source.obtener_periodo(symbol, "3mo", "1d", forecast=True)
        raw = resolved["data"]
    data = ProcesadorDatos().procesar(ValidadorDatos().validar(raw))
    analyzer = AnalizadorMercado()
    inds = [x.__dict__ for x in analyzer.indicadores(data)]
    # Predicción y alerta salen del mismo motor validado que el panel "Consenso de modelos".
    engine = model_result(resolved["symbol"], cfg.horizon)
    preds = predicciones_desde_motor(engine)
    alert = alerta_desde_motor(engine, cfg.alert_drop)
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
        from app.services.news_pulse import pulse
        data = APIFinanciera().get_news(ticker)
        data["pulse"] = pulse(data.get("news", []))
        return jsonify(data)
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
    user = require_user()
    check_csrf_header()
    ticker = clean_symbol(ticker)
    payload = json_object()
    if not isinstance(payload.get("text"), str):
        raise InvalidInput("El comentario debe ser texto.")
    try:
        post = CommunityStorage(current_app.config["PROJECT_ROOT"]).add_post(ticker, user["username"], payload["text"])
        return jsonify(post=post), 201
    except ValueError as e:
        return jsonify(error=str(e)), 400


@main_bp.post("/api/community/<path:ticker>/vote")
def community_vote(ticker):
    user = require_user()
    check_csrf_header()
    ticker = clean_symbol(ticker)
    payload = json_object()
    if payload.get("direction") not in ("bullish", "bearish"):
        raise InvalidInput("Voto no válido.")
    try:
        sentiment = CommunityStorage(current_app.config["PROJECT_ROOT"]).vote(ticker, payload["direction"], legacy_owner_key(user))
        return jsonify(sentiment=sentiment)
    except ValueError as e:
        return jsonify(error=str(e)), 400


@main_bp.post("/api/analyze")
def analyze():
    body = json_object() if request.is_json else request.form
    symbol = str((body or {}).get("symbol", "")).strip()
    if not symbol:
        return jsonify(error="Debe ingresar un identificador de activo."), 400
    try:
        cfg, resolved, data, inds, preds, alert, tend = pipeline(symbol)
        return jsonify(
            symbol=resolved["symbol"], query=symbol, name=resolved.get("name"), data=data,
            indicators=inds, predictions=preds, alert=alert, tendency=tend,
            model_version="ml_engine (consenso validado)",
            data_source="demo" if os.getenv("USE_DEMO", "false").lower() == "true" else "Yahoo Finance",
        )
    except Exception as e:
        return api_error(e)


@main_bp.post("/api/report")
def report():
    check_csrf_header()
    payload = json_object()
    symbol = payload.get("symbol", "")
    try:
        cfg, resolved, data, inds, preds, alert, tend = pipeline(symbol)
        content, filename = InformeEmpresarial().generar(resolved["symbol"], data, inds, preds, alert, tend)
        return send_file(content, as_attachment=True, download_name=filename, mimetype="text/html", max_age=0)
    except Exception as e:
        return api_error(e)


@main_bp.post("/api/save")
def save():
    user = require_user()
    check_csrf_header()
    payload = json_object()
    try:
        created = Storage(current_app.config["PROJECT_ROOT"]).add(payload, legacy_owner_key(user))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, duplicate=not created)


@main_bp.get("/api/history")
def history():
    user = require_user()
    return jsonify(Storage(current_app.config["PROJECT_ROOT"]).all(legacy_owner_key(user)))


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
    """Share forecasts across analysis, models and saving until the next temporal boundary."""
    ticker = clean_symbol(ticker)
    demo = os.getenv("USE_DEMO", "false").lower() == "true"
    cutoff = datetime.now(timezone.utc)
    key = ("models", ticker.upper(), int(horizon), demo)
    hit = _CACHE.get(key)
    if hit and utc_instant(hit[1]["provenance"]["expires_at"]) <= cutoff:
        _CACHE.pop(key, None)

    def compute():
        if demo:
            calendar, lag = CONTINUOUS, 0
            data, name = DemoFuente().obtener(ticker, 600, end=cutoff.date()), ticker.upper()
        else:
            res = APIFinanciera().obtener_periodo(ticker, "2y", "1d", forecast=True)
            data, name = res["data"], res["symbol"]
            calendar = res["calendar"]
            lag = YAHOO_CLOSE_LAG_MINUTES
        # Yahoo daily history carries no point-in-time publication timestamps for context.
        out = run_all(data, name, horizon, calendar=calendar, cutoff=cutoff, lag_minutes=lag)
        out["data_source"] = "demo" if demo else "Yahoo Finance"
        return out

    return _cached(key, 1800, compute)


@main_bp.get("/api/models/<path:ticker>")
def models(ticker):
    ticker = clean_symbol(ticker)
    try:
        horizon = int(request.args.get("horizon", "5"))
    except ValueError:
        return jsonify(error="Horizonte no válido."), 400
    try:
        result = model_result(ticker, horizon)
        response = jsonify({**result, "emission": forecast_emissions.issue(result, current_app.secret_key)})
        response.headers["Cache-Control"] = "no-store"
        return response
    except Exception as e:
        return api_error(e)


@main_bp.get("/api/health")
def health():
    """Estado del despliegue: base de datos y librerías opcionales (para verificar la instalación)."""
    from app.db import check_db
    from ml_engine.model_registry import registry
    return jsonify(status="ok", db=check_db(), optional_models=registry.optional_status(),
                   demo_mode=os.getenv("USE_DEMO", "false").lower() == "true")
