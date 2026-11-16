import hmac

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

from app.models.config import ModelConfig
from app.repositories.factory import get_repo
from app.security import InvalidInput, admin_required, audit, check_csrf, csrf_token, json_object, limiter, client_ip
from app.session_store import end_session, start_session

admin_bp = Blueprint("admin", __name__)


def _cfg():
    return ModelConfig.from_env(current_app.extensions.get("runtime_config"))


@admin_bp.app_context_processor
def _inject():
    return {"csrf_token": csrf_token}


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    token = current_app.config.get("ADMIN_TOKEN", "")
    if request.method == "POST":
        check_csrf()
        # Freno específico contra fuerza bruta: 5 intentos por minuto y por IP.
        if not limiter.allow("admin-login:" + client_ip(), 5):
            audit("admin_login_rate_limited")
            flash("Demasiados intentos. Espere un minuto.", "error")
        elif token and hmac.compare_digest(request.form.get("token", "").encode(), token.encode()):
            start_session(admin=True)
            csrf_token()
            audit("admin_login_success")
            return redirect(url_for("admin.panel"))
        else:
            audit("admin_login_failed")
            flash("Credenciales inválidas.", "error")
    return render_template("admin.html", view="login", configured=bool(token))


@admin_bp.post("/logout")
@admin_required
def logout():
    check_csrf()
    end_session()
    audit("admin_logout")
    return redirect(url_for("admin.login"))


@admin_bp.get("/")
@admin_required
def panel():
    return render_template("admin.html", view="panel", cfg=_cfg(), configured=True)


@admin_bp.post("/config")
@admin_required
def config():
    check_csrf()
    try:
        new = ModelConfig(
            window=int(request.form.get("window", "")),
            horizon=int(request.form.get("horizon", "")),
            alert_drop=float(request.form.get("alert_drop", "")),
            model_version=request.form.get("version", "").strip(),
        ).validate()
    except ValueError as e:
        flash(str(e) if "debe" in str(e) or "no válida" in str(e) else "Valores numéricos no válidos.", "error")
        return redirect(url_for("admin.panel"))
    # Se guarda en memoria de la app (no en os.environ). Persistencia en BD: Fase 6.
    current_app.extensions["runtime_config"] = new.as_dict()
    audit("admin_config_changed")
    flash("Configuración actualizada (vigente hasta reiniciar el servidor).", "ok")
    return redirect(url_for("admin.panel"))


@admin_bp.get("/api/status")
@admin_required
def status():
    return jsonify(env=current_app.config["ENV"], demo=current_app.config["USE_DEMO"], config=_cfg().as_dict())


@admin_bp.post("/api/users/<int:uid>/block")
@admin_required
def block_user(uid):
    check_csrf()
    blocked = json_object().get("blocked")
    if not isinstance(blocked, bool):
        raise InvalidInput("blocked debe ser booleano.")
    repo = get_repo(current_app)
    if repo is None:
        return jsonify(error="El almacenamiento de cuentas no está disponible."), 503
    if not repo.set_user_blocked(uid, blocked):
        audit("admin_user_not_found", target_id=uid)
        return jsonify(error="Usuario no encontrado."), 404
    if blocked and not repo.persistent_sessions:
        current_app.extensions["session_store"].revoke_user(uid)
    audit("account_blocked" if blocked else "account_unblocked", target_id=uid)
    return jsonify(ok=True)
