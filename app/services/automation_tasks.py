"""Pure scheduling rules and worker-only computations for explicit dataset versions."""
from datetime import datetime, timedelta, timezone

from app.security import InvalidInput
from app.repositories.organization_policy import OrganizationNotFound


KINDS = ("forecast", "indicators", "evaluation", "report")
RULES = ("sales_change", "demand_change", "metric_degradation", "stale_data", "run_failure")


def next_slot(now, hour, weekday=None):
    start = datetime.fromtimestamp(now, timezone.utc)
    slot = start.replace(hour=hour, minute=0, second=0, microsecond=0)
    if weekday is not None:
        slot += timedelta(days=(weekday - slot.weekday()) % 7)
    if slot <= start:
        slot += timedelta(days=7 if weekday is not None else 1)
    return int(slot.timestamp())


def compute(repo, job):
    actor, slug, dataset_id = job["actor_id"], job["slug"], job["dataset_id"]
    params = job["parameters"]
    version = params["version"]
    origin = repo.automation_source(actor, slug, dataset_id, version)
    kind = job["kind"]
    if kind == "forecast":
        from app.services import enterprise_forecast as engine

        start, end = engine.parse_day(params["start"]), engine.parse_day(params["complete_through"])
        rows, forecast_origin = repo.forecast_input(actor, slug, dataset_id, params, start, end)
        dates, series, transformations = engine.prepare(rows, start, end, [engine.parse_day(d) for d in params["zero_dates"]])
        forecast_origin.update(transformations, engine_version=engine.ENGINE_VERSION)
        result = engine.evaluate(dates, series, params["horizon"], arima=params["arima_enabled"],
                                 scenario_pct=params["scenario_pct"])
        return {"origin": forecast_origin, "forecast": result}

    if kind == "evaluation":
        import numpy as np
        from app.services import enterprise_forecast as engine

        run = repo.get_forecast(actor, slug, dataset_id, params["run_id"])
        if run["state"] != "ready":
            raise InvalidInput("El pronóstico no está disponible.")
        source_version = run["parameters"]["version"]
        if version < source_version:
            raise InvalidInput("La versión de evaluación es anterior a la del pronóstico.")
        points = run["result"]["forecast"]
        dates = [engine.parse_day(p["date"]) for p in points]
        if dates[-1] >= datetime.now(timezone.utc).date():
            return {"origin": origin, "outcome": "pending_actuals", "run_id": run["id"]}
        actual_params = {**run["parameters"], "version": version}
        rows, _ = repo.forecast_input(actor, slug, dataset_id, actual_params, dates[0], dates[-1])
        actual = {r[0]: int(r[1]) / 10000 for r in rows}
        zeros = {engine.parse_day(day) for day in params.get("zero_dates", [])}
        if zeros.intersection(actual) or not zeros <= set(dates):
            raise InvalidInput("Fechas de cero incompatibles con el período evaluado.")
        if set(dates) - actual.keys() - zeros:
            return {"origin": origin, "outcome": "pending_actuals", "run_id": run["id"]}
        values = np.array([actual.get(day, 0) for day in dates])
        expected = np.array([p["value"] for p in points])
        scores = engine._metrics(values, expected)
        scores["coverage"] = float(np.mean([p["low"] <= val <= p["high"] for p, val in zip(points, values)]))
        previous = run["result"]["models"][run["result"]["selected_model"]]["final"]
        return {"origin": origin, "outcome": "evaluated", "run_id": run["id"], "metrics": scores,
                "historical_wape": previous["wape"] if previous else None,
                "actuals": [{"date": day.isoformat(), "value": float(value)} for day, value in zip(dates, values)]}

    summary = repo.dataset_summary(actor, slug, dataset_id, version)
    snapshot = {"origin": origin, "metrics": summary["metrics"], "series": summary["series"]}
    if kind == "indicators":
        return snapshot
    run = None
    if params.get("run_id"):
        run = repo.get_forecast(actor, slug, dataset_id, params["run_id"])
        if run["state"] != "ready" or run["parameters"]["version"] != version:
            raise OrganizationNotFound("Pronóstico de esta versión no encontrado.")
    else:
        run = repo.latest_automation_forecast(actor, slug, dataset_id, version)
    resources = repo.list_job_resources(actor, slug, dataset_id)
    evaluation = resources["latest"]["evaluation"]
    if evaluation and (evaluation["parameters"]["version"] != version
                       or (run and evaluation["result"]["run_id"] != run["id"])):
        evaluation = None
    risks = ["El resultado depende de la versión importada y de la fecha de corte."]
    if run is None:
        risks.append("No existe un pronóstico listo para esta versión.")
    elif not run["result"]["beats_baseline_on_final"]:
        risks.append("El bloque final no muestra mejora sobre la referencia.")
    return {**snapshot, "date": datetime.now(timezone.utc).isoformat(), "forecast_run": run,
            "evaluation": evaluation, "risks": risks,
            "alerts": [alert for alert in resources["alerts"] if alert["evidence"]["version"] == version]}
