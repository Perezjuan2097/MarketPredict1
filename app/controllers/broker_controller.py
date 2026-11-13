"""Panel de bróker (Alpaca paper trading, solo lectura)."""
from flask import Blueprint, current_app, jsonify, session

from app.repositories import factory
from app.security import api_error
from app.services.broker_service import AlpacaPaper, allowed_users, user_allowed

broker_bp = Blueprint("broker", __name__)


def _current_user():
    repo = factory.get_repo(current_app)
    if repo is None or not session.get("uid"):
        return None
    try:
        return repo.get_user(session["uid"])
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
    if not (session.get("is_admin") or user_allowed(user)):
        msg = ("Inicie sesión con un usuario autorizado para ver la cuenta del bróker."
               if allowed_users() else "Solo el administrador puede ver la cuenta del bróker (BROKER_USERS está vacío).")
        return jsonify({**base, "authorized": False, "message": msg}), (401 if not user else 403)
    try:
        return jsonify({**alpaca.snapshot(), "configured": True, "authorized": True})
    except Exception as exc:
        return api_error(exc, status=502)
