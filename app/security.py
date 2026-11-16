"""Utilidades de seguridad: validación, límite de peticiones, CSRF de sesión y errores."""
import hmac
import logging
import re
import secrets
import time
from collections import defaultdict, deque
from functools import wraps
from hashlib import sha256
from ipaddress import ip_address, ip_network
from threading import Lock

from flask import abort, current_app, jsonify, request, session
from werkzeug.exceptions import HTTPException

log = logging.getLogger("marketpredict")

class InvalidInput(ValueError):
    """Entrada de usuario inválida (respuesta 400)."""


SYMBOL_RE = re.compile(r"^[\w\s.\-^=&/]{1,40}$", re.UNICODE)


def clean_symbol(value):
    value = " ".join(str(value or "").split())
    if not value or not SYMBOL_RE.match(value):
        raise InvalidInput("Identificador de activo no válido.")
    return value


def api_error(exc, status=422):
    """Devuelve el mensaje solo si es un error de validación/negocio; el resto se registra."""
    from app.services.data_service import MarketDataError
    if isinstance(exc, MarketDataError):
        return jsonify(error=str(exc), code=exc.code), exc.status
    if isinstance(exc, InvalidInput):
        return jsonify(error=str(exc)), 400
    if isinstance(exc, ValueError):
        return jsonify(error="No se pudo completar la solicitud con los datos proporcionados."), status
    log.error("Error no controlado endpoint=%s type=%s", request.endpoint, type(exc).__name__)
    return jsonify(error="Error interno al procesar la solicitud."), 500


class RateLimiter:
    """Ventana deslizante en memoria. Suficiente para una instancia; con varias, usar Redis."""

    def __init__(self):
        self.hits = defaultdict(deque)
        self._lock = Lock()
        self._sweep_at = 0

    def allow(self, key, limit, window=60.0):
        now = time.monotonic()
        with self._lock:
            if now >= self._sweep_at:
                for old in list(self.hits):
                    if not self.hits[old] or self.hits[old][-1] <= now:
                        del self.hits[old]
                self._sweep_at = now + 30
            if key not in self.hits and len(self.hits) >= 10000:
                return False
            q = self.hits[key]
            while q and q[0] <= now:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now + window)
            return True


limiter = RateLimiter()


def client_ip():
    peer = request.remote_addr or "unknown"
    networks = current_app.extensions["trusted_proxies"]
    try:
        address = ip_address(peer)
        if not any(address in network for network in networks):
            return str(address)
        forwarded = request.headers.get("X-Forwarded-For", "").split(",")
        if len(forwarded) > 16:
            return str(address)
        chain = [ip_address(part.strip()) for part in forwarded]
        for hop in reversed(chain):
            if not any(hop in network for network in networks):
                return str(hop)
    except ValueError:
        pass
    return peer


def audit(event, user_id=None, target_id=None):
    log.info("security_event=%s endpoint=%s actor_id=%s target_id=%s",
             event, request.endpoint, user_id, target_id)


def json_object():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise InvalidInput("El cuerpo debe ser un objeto JSON.")
    return value


def require_user():
    from app.repositories.factory import get_repo
    from app.session_store import user_id
    uid = user_id()
    repo = get_repo(current_app) if uid is not None else None
    user = repo.get_user(uid) if repo else None
    if not user or user.get("is_blocked"):
        audit("account_access_denied", uid)
        abort(401, description="Debe iniciar sesión.")
    return user


def legacy_owner_key(user):
    # JSON records can outlive a development repository whose numeric IDs get reused.
    return sha256(f"{user['id']}:{user['created_at']}:{user['password_hash']}".encode()).hexdigest()


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf():
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    if not sent or not sent.isascii() or not hmac.compare_digest(sent, session.get("csrf", "")):
        abort(403, description="Token CSRF inválido o ausente.")


def check_csrf_header():
    """API de cuentas: el cliente reenvía el token (obtenido en /api/auth/me) en X-CSRF-Token."""
    sent = request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf", "")
    if not sent or not expected or not sent.isascii() or not hmac.compare_digest(sent, expected):
        abort(403, description="Token CSRF inválido o ausente.")


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        from app.session_store import is_admin
        if not is_admin():
            audit("admin_access_denied")
            if request.path.startswith("/admin/api") or request.is_json:
                return jsonify(error="Acceso restringido a administradores."), 403
            from flask import redirect, url_for
            return redirect(url_for("admin.login"))
        return view(*args, **kwargs)
    return wrapper


def register_security(app):
    from app.session_store import SessionStore
    app.extensions["session_store"] = SessionStore()
    app.extensions["trusted_proxies"] = tuple(
        ip_network(value.strip()) for value in app.config["TRUSTED_PROXY_CIDRS"].split(",") if value.strip()
    )

    @app.before_request
    def _limit():
        if request.path.startswith(("/api/", "/admin")):
            limit = current_app.config["RATE_LIMIT_PER_MINUTE"]
            if not limiter.allow(client_ip(), limit):
                return jsonify(error="Demasiadas solicitudes. Intente de nuevo en un minuto."), 429, {"Retry-After": "60"}

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path.startswith(("/api/auth/", "/api/me/", "/api/history", "/api/broker/", "/admin")):
            resp.headers["Cache-Control"] = "no-store"
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'",
        )
        if current_app.config.get("SESSION_COOKIE_SECURE"):
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp

    @app.errorhandler(InvalidInput)
    def _invalid(e):
        return jsonify(error=str(e)), 400

    @app.errorhandler(403)
    def _403(e):
        audit("request_denied")
        if request.path.startswith("/api/"):
            return jsonify(error=getattr(e, "description", "Acceso denegado.")), 403
        return e

    @app.errorhandler(401)
    def _401(e):
        return jsonify(error="Debe iniciar sesión."), 401

    @app.errorhandler(404)
    def _404(e):
        if request.path.startswith("/api/"):
            return jsonify(error="Recurso no encontrado."), 404
        return e

    @app.errorhandler(413)
    def _413(e):
        return jsonify(error="La solicitud excede el tamaño permitido."), 413

    @app.errorhandler(Exception)
    def _unexpected(e):
        if isinstance(e, HTTPException):
            return e
        log.error("Error 500 endpoint=%s type=%s", request.endpoint, type(e).__name__)
        return jsonify(error="Error interno del servidor."), 500
