import hmac

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from app.models.config import ModelConfig
from app.security import admin_required, check_csrf, csrf_token, limiter, client_ip

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
            flash("Demasiados intentos. Espere un minuto.", "error")
        elif token and hmac.compare_digest(request.form.get("token", ""), token):
            session.clear()
            session["is_admin"] = True
            return redirect(url_for("admin.panel"))
        else:
            flash("Credenciales inválidas.", "error")
    return render_template("admin.html", view="login", configured=bool(token))


@admin_bp.post("/logout")
def logout():
    check_csrf()
    session.clear()
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
    flash("Configuración actualizada (vigente hasta reiniciar el servidor).", "ok")
    return redirect(url_for("admin.panel"))


@admin_bp.get("/api/status")
@admin_required
def status():
    return jsonify(env=current_app.config["ENV"], demo=current_app.config["USE_DEMO"], config=_cfg().as_dict())
