"""Private APIs for enterprise jobs, schedules, alerts, and report downloads."""
from hashlib import sha256
from importlib.util import find_spec
import json
import re

from flask import Blueprint, Response, current_app, jsonify, request

from app.controllers.dataset_controller import dataset_guarded
from app.controllers.enterprise_forecast_controller import _configuration
from app.models.orm import EnterpriseAlert, EnterpriseAlertRule
from app.repositories.automation_sql import QueueFull
from app.repositories.organization_policy import OrganizationNotFound
from app.security import InvalidInput, audit, json_object
from app.services.automation_tasks import KINDS, RULES
from app.services.enterprise_forecast import MAX_HORIZON, parse_day
from app.services.enterprise_reports import csv_bytes, pdf_bytes

automation_bp = Blueprint("enterprise_automation", __name__,
                          url_prefix="/api/me/organizations/<slug>/datasets/<int:dataset_id>/automation")


def _positive(value):
    if type(value) is not int or not 1 <= value <= 2147483647:
        raise InvalidInput("Selecciona un identificador válido.")
    return value


def parameters(kind, body):
    if kind not in KINDS:
        raise InvalidInput("Tipo de trabajo no válido.")
    if kind == "forecast":
        params, *_ = _configuration(body)
        params["arima_enabled"] = bool(current_app.config["ENTERPRISE_FORECAST_ARIMA"] and find_spec("statsmodels"))
        return params
    params = {"version": _positive(body.get("version"))}
    if kind == "evaluation" or kind == "report" and body.get("run_id") is not None:
        params["run_id"] = _positive(body.get("run_id"))
    if kind == "evaluation":
        zeros = body.get("zero_dates", [])
        if not isinstance(zeros, list) or len(zeros) > MAX_HORIZON:
            raise InvalidInput("Fechas de cero no válidas.")
        params["zero_dates"] = sorted({parse_day(day).isoformat() for day in zeros})
    return params


def enqueue(repo, actor, slug, dataset_id, kind, body):
    params = parameters(kind, body)
    key = request.headers.get("Idempotency-Key")
    if key is None:
        key = sha256(json.dumps([dataset_id, kind, params], separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", key):
        raise InvalidInput("Clave de idempotencia no válida.")
    try:
        job = repo.enqueue_job(actor, slug, dataset_id, kind, params, key)
    except QueueFull as exc:
        return jsonify(error=str(exc)), 429, {"Retry-After": "30"}
    audit("enterprise_job_requested", actor, job["id"])
    return jsonify(job=job), 202


@automation_bp.get("")
@dataset_guarded
def dashboard(repo, actor, slug, dataset_id):
    return jsonify(repo.list_job_resources(actor, slug, dataset_id))


@automation_bp.post("/jobs")
@dataset_guarded
def create_job(repo, actor, slug, dataset_id):
    config = json_object()
    return enqueue(repo, actor, slug, dataset_id, config.get("kind"), config)


@automation_bp.get("/jobs/<int:job_id>")
@dataset_guarded
def get_job(repo, actor, slug, dataset_id, job_id):
    return jsonify(job=repo.get_job(actor, slug, dataset_id, job_id))


@automation_bp.delete("/jobs/<int:job_id>")
@dataset_guarded
def cancel_job(repo, actor, slug, dataset_id, job_id):
    job = repo.cancel_job(actor, slug, dataset_id, job_id)
    audit("enterprise_job_canceled", actor, job_id)
    return jsonify(job=job)


@automation_bp.post("/schedules")
@dataset_guarded
def create_schedule(repo, actor, slug, dataset_id):
    config = json_object()
    kind = config.get("kind")
    cadence, hour, weekday = config.get("cadence"), config.get("hour_utc"), config.get("weekday")
    if cadence not in ("daily", "weekly") or type(hour) is not int or not 0 <= hour <= 23:
        raise InvalidInput("Selecciona una frecuencia y hora UTC válidas.")
    if cadence == "weekly" and (type(weekday) is not int or not 0 <= weekday <= 6):
        raise InvalidInput("El día de la semana debe estar entre 0 (lunes) y 6.")
    row = repo.manage_schedule(actor, slug, dataset_id, config={
        "kind": kind, "cadence": cadence,
        "parameters": {**parameters(kind, config), "hour_utc": hour,
                       "weekday": weekday if cadence == "weekly" else None},
    })
    audit("enterprise_schedule_created", actor, row["id"])
    return jsonify(schedule=row), 201


def _enabled(body):
    value = body.get("enabled")
    if type(value) is not bool:
        raise InvalidInput("enabled debe ser booleano.")
    return value


@automation_bp.patch("/schedules/<int:schedule_id>")
@dataset_guarded
def update_schedule(repo, actor, slug, dataset_id, schedule_id):
    row = repo.manage_schedule(actor, slug, dataset_id, schedule_id=schedule_id, enabled=_enabled(json_object()))
    audit("enterprise_schedule_changed", actor, schedule_id)
    return jsonify(schedule=row)


@automation_bp.post("/rules")
@dataset_guarded
def create_rule(repo, actor, slug, dataset_id):
    config = json_object()
    kind, comparison = config.get("kind"), config.get("comparison")
    threshold, severity = config.get("threshold"), config.get("severity")
    if (kind not in RULES or comparison not in ("above", "below") or severity not in ("info", "warning", "critical")
            or type(threshold) not in (int, float) or not -100000 <= threshold <= 100000):
        raise InvalidInput("Regla, umbral o severidad no válidos.")
    if kind == "run_failure":
        threshold, comparison = 1, "above"
    row = repo.add_alert_rule(actor, slug, dataset_id, {"kind": kind, "comparison": comparison,
                                                       "threshold": threshold, "severity": severity})
    audit("enterprise_alert_rule_created", actor, row["id"])
    return jsonify(rule=row), 201


@automation_bp.patch("/rules/<int:rule_id>")
@dataset_guarded
def update_rule(repo, actor, slug, dataset_id, rule_id):
    repo.update_alert_resource(actor, slug, dataset_id, EnterpriseAlertRule, rule_id, _enabled(json_object()))
    audit("enterprise_alert_rule_changed", actor, rule_id)
    return "", 204


@automation_bp.patch("/alerts/<int:alert_id>")
@dataset_guarded
def resolve_alert(repo, actor, slug, dataset_id, alert_id):
    repo.update_alert_resource(actor, slug, dataset_id, EnterpriseAlert, alert_id, "resolved")
    audit("enterprise_alert_resolved", actor, alert_id)
    return "", 204


@automation_bp.get("/reports/<int:job_id>/<extension>")
@dataset_guarded
def export_report(repo, actor, slug, dataset_id, job_id, extension):
    job = repo.get_job(actor, slug, dataset_id, job_id)
    if job["kind"] != "report" or job["state"] != "completed" or extension not in ("csv", "pdf"):
        raise OrganizationNotFound("Reporte no encontrado.")
    export, mimetype = (csv_bytes, "text/csv") if extension == "csv" else (pdf_bytes, "application/pdf")
    audit("enterprise_report_exported", actor, job_id)
    return Response(export(job["result"]), mimetype=mimetype,
                    headers={"Content-Disposition": f'attachment; filename="reporte-{job_id}.{extension}"'})
