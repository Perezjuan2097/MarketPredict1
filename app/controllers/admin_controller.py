import hmac

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

from app.models.config import ModelConfig
from app.repositories.base import AdminAccessDenied, ConflictError
from app.repositories.factory import get_repo
from app.security import InvalidInput, admin_required, audit, check_csrf, csrf_token, json_object, limiter, client_ip
from app.session_store import end_session, start_session, user_id

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
    actor_id = user_id()
    end_session()
    audit("admin_logout", actor_id)
    return redirect(url_for("admin.login"))


@admin_bp.get("/")
@admin_required
def panel():
    repo = get_repo(current_app)
    after = request.args.get("after", default=0, type=int)
    users = repo.list_users(max(0, min(after, 2147483647)), 51) if repo is not None else []
    return render_template("admin.html", view="panel", cfg=_cfg(), users=users[:50],
                           next_user=users[49]["id"] if len(users) > 50 else None,
                           accounts_available=repo is not None, actor_id=user_id())


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
    audit("admin_config_changed", user_id())
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
    if request.is_json:
        blocked = json_object().get("blocked")
    else:
        blocked = {"true": True, "false": False}.get(request.form.get("blocked"))
    if not isinstance(blocked, bool):
        raise InvalidInput("blocked debe ser booleano.")
    return _update_user(uid, "account_blocked" if blocked else "account_unblocked", blocked=blocked)


@admin_bp.post("/api/users/<int:uid>/role")
@admin_required
def role_user(uid):
    check_csrf()
    role = (json_object() if request.is_json else request.form).get("role")
    if role not in ("user", "admin"):
        raise InvalidInput("El rol debe ser user o admin.")
    return _update_user(uid, f"account_role_{role}", role=role)


def _update_user(uid, action, **changes):
    actor_id = user_id()
    repo = get_repo(current_app)
    if repo is None:
        return _update_response("El almacenamiento de cuentas no está disponible.", 503)
    try:
        found = repo.admin_update_user(uid, actor_id, **changes)
    except AdminAccessDenied:
        audit("admin_access_denied", actor_id, uid)
        return _update_response("Acceso restringido a administradores.", 403)
    except ConflictError as exc:
        audit("admin_user_change_rejected", actor_id, uid)
        return _update_response(str(exc), 409)
    if not found:
        audit("admin_user_not_found", actor_id, uid)
        return _update_response("Usuario no encontrado.", 404)
    if changes.get("blocked") and not repo.persistent_sessions:
        current_app.extensions["session_store"].revoke_user(uid)
    audit(action, actor_id, uid)
    return _update_response("Cuenta actualizada.", 200)


def _update_response(message, status):
    if request.is_json:
        return (jsonify(ok=True) if status == 200 else jsonify(error=message)), status
    flash(message, "ok" if status == 200 else "error")
    return redirect(url_for("admin.panel"))
