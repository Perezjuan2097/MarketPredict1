"""Global operations metadata. Enterprise payloads remain on tenant-authorized routes."""
from flask import Blueprint, current_app, jsonify, render_template
from sqlalchemy import inspect

from app.controllers.organization_controller import _page
from app.db import get_engine
from app.repositories.base import AdminAccessDenied
from app.repositories.commercial_sql import PLAN_DEFAULTS, QUOTAS
from app.repositories.factory import get_repo
from app.repositories.organization_policy import OrganizationNotFound
from app.security import InvalidInput, admin_required, audit, check_csrf_header, csrf_token, json_object
from app.session_store import user_id

commercial_bp = Blueprint("commercial", __name__)
EXPECTED_REVISION = "20261123_enterprise_commercial"
REQUIRED_TABLES = {
    "enterprise_jobs", "enterprise_automations", "enterprise_forecast_runs", "enterprise_dataset_rows",
    "enterprise_plan_assignments", "enterprise_plans", "enterprise_worker_heartbeats", "enterprise_commercial_events",
}


def readiness():
    try:
        engine = get_engine()
        with engine.connect() as connection:
            if not REQUIRED_TABLES <= set(inspect(connection).get_table_names()):
                return False
            return connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalars().all() == [EXPECTED_REVISION]
    except Exception:
        return False


def _repo():
    repo = get_repo(current_app)
    return repo if hasattr(repo, "commercial_overview") else None


@commercial_bp.get("/api/live")
def live():
    return jsonify(status="alive"), 200, {"Cache-Control": "no-store"}


@commercial_bp.get("/api/ready")
def ready():
    available = readiness()
    return jsonify(status="ready" if available else "unavailable"), 200 if available else 503, {"Cache-Control": "no-store"}


@commercial_bp.get("/admin/commercial")
@admin_required
def panel():
    return render_template("commercial.html", csrf=csrf_token(), writable=user_id() is not None)


@commercial_bp.get("/admin/api/commercial")
@admin_required
def overview():
    repo = _repo()
    if repo is None:
        return jsonify(error="El panel requiere PostgreSQL o SQLite configurado."), 503
    return jsonify(**repo.commercial_overview(*_page()), web_ready=readiness())


@commercial_bp.patch("/admin/api/commercial/organizations/<int:org_id>")
@admin_required
def assign(org_id):
    check_csrf_header()
    actor = user_id()
    if actor is None:
        return jsonify(error="Se requiere una cuenta administradora."), 403
    plan = json_object().get("plan")
    if not isinstance(plan, str) or plan not in PLAN_DEFAULTS:
        raise InvalidInput("Plan no válido.")
    repo = _repo()
    if repo is None:
        return jsonify(error="Almacenamiento comercial no disponible."), 503
    try:
        repo.commercial_assign(org_id, plan, actor)
    except OrganizationNotFound:
        return jsonify(error="Organización no encontrada."), 404
    except AdminAccessDenied:
        return jsonify(error="Se requiere una cuenta administradora vigente."), 403
    audit("enterprise_plan_assigned", actor, org_id)
    return "", 204


@commercial_bp.put("/admin/api/commercial/plans/<plan>")
@admin_required
def configure(plan):
    check_csrf_header()
    actor = user_id()
    if actor is None:
        return jsonify(error="Se requiere una cuenta administradora."), 403
    limits = json_object()
    if plan not in PLAN_DEFAULTS or set(limits) != set(QUOTAS) or any(
            type(value) is not int or not 0 <= value <= 2_000_000_000 for value in limits.values()):
        raise InvalidInput("Indica todos los límites del plan como enteros no negativos.")
    if not 1 <= limits["retention_days"] <= 3650:
        raise InvalidInput("La retención debe ser de 1 a 3650 días.")
    repo = _repo()
    if repo is None:
        return jsonify(error="Almacenamiento comercial no disponible."), 503
    try:
        repo.commercial_configure(plan, limits, actor)
    except AdminAccessDenied:
        return jsonify(error="Se requiere una cuenta administradora vigente."), 403
    audit("enterprise_plan_configured", actor)
    return "", 204
