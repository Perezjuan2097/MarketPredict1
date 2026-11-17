"""Panel de bróker (Alpaca paper trading, solo lectura)."""
from flask import Blueprint, current_app, jsonify

from app.repositories import factory
from app.security import api_error, audit
from app.session_store import is_admin, user_id
from app.services.broker_service import AlpacaPaper, BrokerUnavailable, allowed_users, user_allowed

broker_bp = Blueprint("broker", __name__)


def broker_access_allowed(user):
    return is_admin() or user_allowed(user)


def _current_user():
    uid = user_id()
    repo = factory.get_repo(current_app) if uid is not None else None
    if repo is None:
        return None
    try:
        user = repo.get_user(uid)
        return user if user and not user.get("is_blocked") else None
    except Exception:
        return None


@broker_bp.get("/api/broker/status")
def broker_status():
    alpaca = AlpacaPaper()
    base = {"broker": alpaca.name, "configured": alpaca.configured, "paper": True}
    if not alpaca.configured:
        return jsonify({**base, "authorized": False,
                        "message": "Bróker sin configurar: faltan ALPACA_API_KEY y ALPACA_API_SECRET."})
    user = _current_user()
    if not broker_access_allowed(user):
        audit("broker_access_denied", user_id())
        msg = ("Inicie sesión con un usuario autorizado para ver la cuenta del bróker."
               if allowed_users() else "Solo el administrador puede ver la cuenta del bróker.")
        return jsonify({**base, "authorized": False, "message": msg}), (401 if not user else 403)
    try:
        return jsonify({**alpaca.snapshot(), "configured": True, "authorized": True})
    except BrokerUnavailable as exc:
        return jsonify(error=str(exc)), 502
    except Exception as exc:
        return api_error(exc, status=502)
