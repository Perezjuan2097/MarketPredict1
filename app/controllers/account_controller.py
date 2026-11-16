"""Cuentas, predicciones guardadas y portafolio simulado. Todo requiere sesión salvo /api/auth/*."""
import logging
from decimal import Decimal
from functools import wraps

from flask import Blueprint, current_app, jsonify, request

from app.repositories import factory
from app.repositories.base import ConflictError, identity_key
from app.security import InvalidInput, api_error, audit, check_csrf_header, client_ip, csrf_token, json_object, limiter
from app.session_store import end_session, start_session, user_id
from app.services import account_service, portfolio_service, user_predictions

log = logging.getLogger("marketpredict")
account_bp = Blueprint("account", __name__)


def jsonable(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items() if k != "password_hash"}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def _repo():
    return factory.get_repo(current_app)


def guarded(auth=True):
    def deco(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            repo = _repo()
            if repo is None:
                return jsonify(error="El almacenamiento de cuentas no está disponible. Revise /api/health."), 503
            user = None
            if auth:
                uid = user_id()
                user = repo.get_user(uid) if uid is not None else None
                if not user or user.get("is_blocked"):
                    audit("account_access_denied", uid)
                    if uid is not None:
                        end_session()
                    return jsonify(error="Debe iniciar sesión."), 401
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                check_csrf_header()
            try:
                return view(repo, user, *args, **kwargs)
            except InvalidInput as exc:
                return jsonify(error=str(exc)), 400
            except ConflictError as exc:
                return jsonify(error=str(exc)), 409
            except Exception as exc:
                return api_error(exc)
        return wrapper
    return deco


# ------------------------------------------------------------------ autenticación
@account_bp.get("/api/auth/me")
def me():
    repo = _repo()
    uid = user_id()
    user = repo.get_user(uid) if repo and uid is not None else None
    if uid is not None and (not user or user.get("is_blocked")):
        end_session()
        user = None
    return jsonify(user=account_service.public_user(user) if user else None, csrf=csrf_token(), persistence=repo is not None)


def _start_session(user):
    start_session(user_id=user["id"])
    return jsonify(user=account_service.public_user(user), csrf=csrf_token())


@account_bp.post("/api/auth/register")
@guarded(auth=False)
def register(repo, _user):
    if not limiter.allow("register:" + client_ip(), 10):
        audit("registration_rate_limited")
        return jsonify(error="Demasiados registros desde esta dirección. Intente más tarde."), 429
    body = json_object()
    user = account_service.register(repo, body.get("username"), body.get("email"), body.get("password"))
    audit("account_registered", user["id"])
    return _start_session(user), 201


@account_bp.post("/api/auth/login")
@guarded(auth=False)
def login(repo, _user):
    from hashlib import sha256
    body = json_object()
    ident = body.get("identifier")
    ident = identity_key(ident) if isinstance(ident, str) and len(ident) <= 254 else ""
    key = sha256(ident.encode()).hexdigest()
    ip = client_ip()
    if not limiter.allow("login:" + ip, 30) or not limiter.allow(f"login:{ip}:{key}", 8):
        audit("login_rate_limited")
        return jsonify(error="Demasiados intentos. Espere un minuto."), 429
    user = account_service.authenticate(repo, ident, body.get("password"))
    if not user:
        audit("login_failed")
        return jsonify(error="Credenciales inválidas."), 401
    audit("login_success", user["id"])
    return _start_session(user)


@account_bp.post("/api/auth/logout")
def logout():
    check_csrf_header()
    uid = user_id()
    end_session()
    audit("logout", uid)
    return jsonify(ok=True, csrf=csrf_token())


# ------------------------------------------------------------------ predicciones
@account_bp.get("/api/me/predictions")
@guarded()
def list_predictions(repo, user):
    return jsonify(predictions=jsonable(repo.list_predictions(user["id"])))


@account_bp.post("/api/me/predictions")
@guarded()
def publish_prediction(repo, user):
    pred = user_predictions.publish(repo, factory.get_provider(current_app), user["id"], json_object())
    return jsonify(prediction=jsonable(pred)), 201


@account_bp.post("/api/me/predictions/from-models")
@guarded()
def save_from_models(repo, user):
    from app.controllers.main_controller import model_result
    body = json_object()
    try:
        horizon = int(body.get("horizon", 5))
    except (TypeError, ValueError):
        raise InvalidInput("Horizonte no válido.")
    result = model_result(body.get("symbol"), horizon)
    return jsonify(saved=jsonable(user_predictions.save_model_predictions(repo, user["id"], result))), 201


@account_bp.post("/api/me/predictions/<int:pred_id>/cancel")
@guarded()
def cancel_prediction(repo, user, pred_id):
    pred = user_predictions.cancel(repo, user["id"], pred_id)
    if pred is None:
        audit("prediction_access_denied", user["id"], pred_id)
    return (jsonify(prediction=jsonable(pred)) if pred else (jsonify(error="Predicción no encontrada."), 404))


@account_bp.post("/api/me/predictions/evaluate")
@guarded()
def evaluate_predictions(repo, user):
    return jsonify(user_predictions.evaluate_due(repo, factory.get_provider(current_app), user["id"]))


# ------------------------------------------------------------------ portafolios
@account_bp.get("/api/me/portfolios")
@guarded()
def list_portfolios(repo, user):
    return jsonify(portfolios=jsonable(repo.list_portfolios(user["id"])))


@account_bp.post("/api/me/portfolios")
@guarded()
def create_portfolio(repo, user):
    body = json_object()
    pf = portfolio_service.create(repo, user["id"], body.get("name"), body.get("initial_cash"), body.get("commission_pct"))
    return jsonify(portfolio=jsonable(pf)), 201


@account_bp.get("/api/me/portfolios/<int:pf_id>")
@guarded()
def portfolio_detail(repo, user, pf_id):
    pf = portfolio_service.owned(repo, user["id"], pf_id)
    if not pf:
        audit("portfolio_access_denied", user["id"], pf_id)
        return jsonify(error="Portafolio no encontrado."), 404
    return jsonify(jsonable(portfolio_service.snapshot(repo, factory.get_provider(current_app), pf)))


@account_bp.post("/api/me/portfolios/<int:pf_id>/trades")
@guarded()
def trade(repo, user, pf_id):
    pf = portfolio_service.owned(repo, user["id"], pf_id)
    if not pf:
        audit("portfolio_write_denied", user["id"], pf_id)
        return jsonify(error="Portafolio no encontrado."), 404
    body = json_object()
    t = portfolio_service.execute_trade(repo, factory.get_provider(current_app), pf, body.get("symbol"), body.get("side"), body.get("quantity"))
    return jsonify(trade=jsonable(t)), 201
