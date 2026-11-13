"""Utilidades de seguridad: validación, límite de peticiones, CSRF de sesión y errores."""
import hmac
import logging
import re
import secrets
import time
from collections import defaultdict, deque
from functools import wraps

from flask import abort, current_app, jsonify, request, session

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
    if isinstance(exc, (ValueError, RuntimeError)):
        return jsonify(error=str(exc)), status
    log.exception("Error no controlado en %s", request.path)
    return jsonify(error="Error interno al procesar la solicitud."), 500


class RateLimiter:
    """Ventana deslizante en memoria. Suficiente para una instancia; con varias, usar Redis."""

    def __init__(self):
        self.hits = defaultdict(deque)

    def allow(self, key, limit, window=60.0):
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


limiter = RateLimiter()


def client_ip():
    # Detrás del proxy de Netlify/Render la IP real llega en X-Forwarded-For.
    forwarded = request.headers.get("X-Forwarded-For", "")
    return (forwarded.split(",")[0].strip() or request.remote_addr or "unknown")


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf():
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
        abort(403, description="Token CSRF inválido o ausente.")


def check_csrf_header():
    """API de cuentas: el cliente reenvía el token (obtenido en /api/auth/me) en X-CSRF-Token."""
    sent = request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf", "")
    if not sent or not expected or not hmac.compare_digest(sent, expected):
        abort(403, description="Token CSRF inválido o ausente.")


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            if request.path.startswith("/admin/api") or request.is_json:
                return jsonify(error="Acceso restringido a administradores."), 403
            from flask import redirect, url_for
            return redirect(url_for("admin.login"))
        return view(*args, **kwargs)
    return wrapper


def register_security(app):
    @app.before_request
    def _limit():
        if request.path.startswith(("/api/", "/admin")):
            limit = current_app.config["RATE_LIMIT_PER_MINUTE"]
            if not limiter.allow(client_ip(), limit):
                return jsonify(error="Demasiadas solicitudes. Intente de nuevo en un minuto."), 429

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
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
        if request.path.startswith("/api/"):
            return jsonify(error=getattr(e, "description", "Acceso denegado.")), 403
        return e

    @app.errorhandler(404)
    def _404(e):
        if request.path.startswith("/api/"):
            return jsonify(error="Recurso no encontrado."), 404
        return e

    @app.errorhandler(413)
    def _413(e):
        return jsonify(error="La solicitud excede el tamaño permitido."), 413

    @app.errorhandler(500)
    def _500(e):
        log.exception("Error 500")
        return jsonify(error="Error interno del servidor."), 500
