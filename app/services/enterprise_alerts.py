"""Signals derived from committed tenant job outputs."""
from datetime import date, datetime, timezone


def alert_signal(kind, job, output, now):
    if kind == "run_failure":
        return (1.0, f"failure:{job.id}") if output.get("error_code") else None
    origin = output.get("origin") or {}
    if kind == "stale_data":
        last = origin.get("last_source_date")
        if not last:
            return None
        age = (datetime.fromtimestamp(now, timezone.utc).date() - date.fromisoformat(last)).days
        return float(age), f"stale:{job.version_id}:{last}"
    if kind == "metric_degradation" and output.get("outcome") == "evaluated":
        actual = output["metrics"]["wape"]
        historical = output["historical_wape"]
        if actual is not None and historical is not None:
            return (actual - historical) * 100, f"evaluation:{output['run_id']}:{job.version_id}"
    forecast = output.get("forecast")
    if not forecast or (kind, job.parameters.get("target")) not in (
            ("sales_change", "revenue"), ("demand_change", "quantity")):
        return None
    future = forecast["forecast"]
    baseline = sum(point["value"] for point in forecast["history"][-len(future):])
    if baseline:
        delta = (sum(point["value"] for point in future) - baseline) / abs(baseline) * 100
        from hashlib import sha256
        import json
        key = sha256(json.dumps([job.version_id, job.parameters.get("target"), job.parameters.get("group"),
                                 origin.get("series_sha256"), future], sort_keys=True).encode()).hexdigest()
        return delta, "change:" + key
    return None
