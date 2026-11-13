"""Predicciones de usuarios: publicar, guardar las de los modelos, cancelar y evaluar al vencer el horizonte."""
from datetime import date

import numpy as np

from app.repositories.base import ConflictError
from app.security import InvalidInput, clean_symbol
from app.services.evaluation import evaluate

DIRECTIONS = ("alcista", "bajista", "neutral")


def target_date_for(horizon_days, start=None):
    start = start or date.today()
    return str(np.busday_offset(np.datetime64(start.isoformat()), int(horizon_days), roll="forward"))


def _opt_float(value, name, lo, hi):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidInput(f"{name} no es un número válido.") from exc
    if not np.isfinite(number) or not lo < number < hi:
        raise InvalidInput(f"{name} fuera de rango.")
    return number


def publish(repo, provider, user_id, payload):
    payload = payload or {}
    symbol = clean_symbol(payload.get("symbol"))
    direction = str(payload.get("direction", "")).lower()
    if direction not in DIRECTIONS:
        raise InvalidInput("La dirección debe ser alcista, bajista o neutral.")
    try:
        horizon = int(payload.get("horizon_days"))
    except (TypeError, ValueError) as exc:
        raise InvalidInput("Horizonte no válido.") from exc
    if not 1 <= horizon <= 60:
        raise InvalidInput("El horizonte debe estar entre 1 y 60 sesiones.")
    target = _opt_float(payload.get("target_price"), "El precio objetivo", 0, 1e9)
    expected = _opt_float(payload.get("expected_return_pct"), "El retorno esperado", -90, 1000)
    rationale = str(payload.get("rationale") or "").strip()[:2000] or None
    indicators = [str(i)[:40] for i in (payload.get("indicators") or [])[:10]] if isinstance(payload.get("indicators"), list) else None

    quote = provider.latest(symbol)  # el precio de referencia lo fija el servidor, no el cliente
    ref = float(quote["price"])
    if target is not None:
        if direction == "alcista" and target <= ref:
            raise InvalidInput("Una predicción alcista requiere un precio objetivo superior al de referencia.")
        if direction == "bajista" and target >= ref:
            raise InvalidInput("Una predicción bajista requiere un precio objetivo inferior al de referencia.")
    asset_id = repo.get_or_create_asset(quote["symbol"], quote.get("name"), quote.get("currency"))
    return repo.add_prediction({
        "source": "user", "user_id": user_id, "asset_id": asset_id, "direction": direction, "reference_price": ref,
        "target_price": target, "expected_return_pct": expected, "horizon_days": horizon,
        "target_date": target_date_for(horizon), "rationale": rationale, "indicators": indicators,
        "is_public": bool(payload.get("is_public", False))})


def save_model_predictions(repo, user_id, engine_result):
    """Guarda cada modelo válido y el consenso del resultado de ml_engine como predicciones del usuario."""
    symbol, ref, horizon = engine_result["asset"], engine_result["reference_price"], engine_result["horizon_days"]
    asset_id = repo.get_or_create_asset(symbol)
    saved = []
    entries = [(m["model_id"], m["version"], m["predicted_log_return"], m.get("estimated_return_pct"), m.get("estimated_price"))
               for m in engine_result["models"] if m["status"] == "ok"]
    cons = engine_result.get("consensus", {})
    if cons.get("status") == "ok":
        entries.append(("consensus", "1.0", cons["weighted_log_return"], cons["weighted_return_pct"], cons["estimated_price"]))
    for model_id, version, logret, ret_pct, price in entries:
        direction = "alcista" if logret > 0 else "bajista" if logret < 0 else "neutral"
        if model_id == "consensus":
            direction = cons["direction"]
        saved.append(repo.add_prediction({
            "source": "model", "user_id": user_id, "asset_id": asset_id, "direction": direction, "reference_price": ref,
            "target_price": price, "expected_return_pct": ret_pct, "horizon_days": horizon,
            "target_date": target_date_for(horizon, date.fromisoformat(engine_result["as_of"])),
            "rationale": None, "indicators": {"model_id": model_id, "model_version": version, "data_source": engine_result.get("data_source")},
            "is_public": False}))
    if not saved:
        raise InvalidInput("Ningún modelo produjo una predicción válida para guardar.")
    return saved


def cancel(repo, user_id, pred_id):
    pred = repo.get_prediction(pred_id)
    if not pred or pred["user_id"] != user_id:
        return None
    if pred["status"] not in ("pendiente", "en_evaluacion") or pred["evaluation"]:
        raise InvalidInput("Solo se pueden invalidar predicciones pendientes sin evaluar.")
    repo.set_prediction_status(pred_id, "invalidada")  # el historial se conserva; los campos no se editan
    return repo.get_prediction(pred_id)


def evaluate_due(repo, provider, user_id=None, today=None):
    """Evalúa predicciones cuyo horizonte ya terminó. Devuelve contadores; los fallos de datos no abortan."""
    today = today or date.today()
    done = pending = 0
    for pred in repo.due_predictions(today.isoformat(), user_id):
        try:
            price = provider.close_on_or_before(pred["symbol"], date.fromisoformat(pred["target_date"]))
        except Exception:
            price = None
        if price is None:
            repo.set_prediction_status(pred["id"], "en_evaluacion")
            pending += 1
            continue
        ev, status = evaluate(pred, price)
        try:
            repo.save_evaluation(pred["id"], ev, status)
            done += 1
        except ConflictError:
            pass
    return {"evaluated": done, "waiting_for_data": pending}
