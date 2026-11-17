"""Private enterprise forecast API."""
from datetime import datetime, timezone
import math
from unicodedata import normalize

from flask import Blueprint, jsonify

from app.controllers.dataset_controller import dataset_guarded
from app.controllers.organization_controller import _page
from app.security import InvalidInput, json_object
from app.services import enterprise_forecast as engine

forecast_bp = Blueprint("enterprise_forecasts", __name__,
                        url_prefix="/api/me/organizations/<slug>/datasets/<int:dataset_id>/forecasts")


def _configuration(body):
    version, horizon = body.get("version"), body.get("horizon")
    if type(version) is not int or not 1 <= version <= 2147483647:
        raise InvalidInput("Selecciona una versión del dataset.")
    if type(horizon) is not int or not 1 <= horizon <= engine.MAX_HORIZON:
        raise InvalidInput("El horizonte debe ser de 1 a 14 días.")
    target, grouping = body.get("target"), body.get("group_by", "date")
    if target not in ("revenue", "quantity") or grouping not in ("date", "product", "category"):
        raise InvalidInput("Variable o agrupación no válida.")
    group = body.get("group")
    if grouping != "date":
        if not isinstance(group, str) or not 1 <= len(group.strip()) <= 160 or not group.isprintable():
            raise InvalidInput("Indica el producto o la categoría exactos.")
        group = normalize("NFC", group.strip())
    else:
        group = None
    start, end = engine.parse_day(body.get("start")), engine.parse_day(body.get("complete_through"))
    days = (end - start).days + 1
    if end >= datetime.now(timezone.utc).date() or not 1 <= days <= engine.MAX_DAYS:
        raise InvalidInput("Usa una ventana histórica de hasta 730 días, con la última fecha ya cerrada.")
    if days < engine.MIN_TRAIN + engine.ORIGINS * horizon:
        raise InvalidInput("Historial insuficiente para el horizonte y los bloques de evaluación.")
    zero = body.get("zero_dates", [])
    if not isinstance(zero, list) or len(zero) > engine.MAX_DAYS:
        raise InvalidInput("La lista de fechas sin actividad no es válida.")
    zero = sorted({engine.parse_day(day) for day in zero})
    scenario = body.get("scenario_pct")
    if scenario is not None and (type(scenario) not in (int, float) or not -100 <= scenario <= 100
                                 or not math.isfinite(scenario)):
        raise InvalidInput("El ajuste de escenario debe estar entre -100 y 100 por ciento.")
    return {"version": version, "horizon": horizon, "target": target, "group_by": grouping, "group": group,
            "start": start.isoformat(), "complete_through": end.isoformat(),
            "zero_dates": [day.isoformat() for day in zero], "scenario_pct": scenario}, start, end, zero


@forecast_bp.post("")
@dataset_guarded
def create_forecast(repo, actor, slug, dataset_id):
    from app.controllers.automation_controller import enqueue

    return enqueue(repo, actor, slug, dataset_id, "forecast", json_object())


@forecast_bp.get("")
@dataset_guarded
def list_forecasts(repo, actor, slug, dataset_id):
    return jsonify(runs=repo.list_forecasts(actor, slug, dataset_id, *_page()))


@forecast_bp.get("/<int:run_id>")
@dataset_guarded
def get_forecast(repo, actor, slug, dataset_id, run_id):
    return jsonify(run=repo.get_forecast(actor, slug, dataset_id, run_id))
