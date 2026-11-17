"""Predicciones de usuarios: publicar, guardar las de los modelos, cancelar y evaluar al vencer el horizonte."""
from datetime import date, datetime, timedelta, timezone
import logging

import numpy as np

from app.repositories.base import ConflictError
from app.security import InvalidInput, clean_symbol
from app.services.evaluation import evaluate
from ml_engine.calendar import CONTRACT_VERSION, calendar_timezone, session_close, target_session, utc_instant

DIRECTIONS = ("alcista", "bajista", "neutral")
log = logging.getLogger("marketpredict")


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

    now = datetime.now(timezone.utc)
    quote = provider.forecast_reference(symbol, now)
    ref = float(quote["price"])
    if not np.isfinite(ref) or ref <= 0:
        raise InvalidInput("Precio de referencia no válido.")
    calendar = quote["calendar"]
    origin = date.fromisoformat(quote["as_of"])
    target_date = target_session(calendar, origin, horizon)
    target_close = session_close(calendar, target_date)
    lag = quote.get("lag_minutes", 0)
    if session_close(calendar, target_session(calendar, origin, 1)) + timedelta(minutes=lag) <= now:
        raise InvalidInput("El cierre de referencia está desactualizado.")
    if target_close <= now:
        raise InvalidInput("El cierre objetivo ya pasó.")
    if target is not None:
        if direction == "alcista" and target <= ref:
            raise InvalidInput("Una predicción alcista requiere un precio objetivo superior al de referencia.")
        if direction == "bajista" and target >= ref:
            raise InvalidInput("Una predicción bajista requiere un precio objetivo inferior al de referencia.")
    asset_id = repo.get_or_create_asset(quote["symbol"], quote.get("name"), quote.get("currency"))
    return repo.add_prediction({
        "source": "user", "user_id": user_id, "asset_id": asset_id, "direction": direction, "reference_price": ref,
        "target_price": target, "expected_return_pct": expected, "horizon_days": horizon,
        "target_date": target_date.isoformat(), "rationale": rationale,
        "indicators": {"user_indicators": indicators, "data_source": quote["source"], "provenance": {
            "contract_version": CONTRACT_VERSION, "calendar": calendar, "timezone": calendar_timezone(calendar),
            "origin_at": session_close(calendar, origin).isoformat(), "origin_session": origin.isoformat(),
            "cutoff_at": now.isoformat(), "target_close_at": target_close.isoformat(), "lag_minutes": lag,
            "horizon": horizon}},
        "is_public": bool(payload.get("is_public", False))})


def save_model_predictions(repo, user_id, emission, *, now=None):
    """Store the authenticated user's copy of a previously issued forecast."""
    now = utc_instant(now or datetime.now(timezone.utc))
    return repo.save_forecast_emission(user_id, emission, expired=utc_instant(emission["expires_at"]) <= now)


def cancel(repo, user_id, pred_id):
    pred = repo.get_prediction(pred_id)
    if not pred or pred["user_id"] != user_id:
        return None
    if pred["status"] not in ("pendiente", "en_evaluacion") or pred["evaluation"]:
        raise InvalidInput("Solo se pueden invalidar predicciones pendientes sin evaluar.")
    repo.set_prediction_status(pred_id, "invalidada")  # el historial se conserva; los campos no se editan
    return repo.get_prediction(pred_id)


def evaluate_due(repo, provider, user_id=None, *, now=None):
    """Evalúa predicciones cuyo horizonte ya terminó. Devuelve contadores; los fallos de datos no abortan."""
    now = utc_instant(now or datetime.now(timezone.utc))
    done = pending = 0
    for pred in repo.due_predictions((now.date() + timedelta(days=1)).isoformat(), user_id):
        target = date.fromisoformat(pred["target_date"])
        metadata = pred["indicators"] if isinstance(pred["indicators"], dict) else {}
        provenance = metadata.get("provenance", {})
        target_close = provenance.get("target_close_at")
        if target_close:
            if utc_instant(target_close) + timedelta(minutes=provenance.get("lag_minutes", 0)) > now:
                continue
        elif target >= now.date():
            continue
        reason = "sin cierre exacto válido"
        price = None
        try:
            source = metadata.get("data_source")
            if not target_close or provenance.get("contract_version") != CONTRACT_VERSION:
                reason = "contrato temporal histórico sin verificar"
            elif source != provider.source:
                reason = "fuente distinta de la registrada"
            else:
                price = provider.close_on(pred["symbol"], target)
        except Exception:
            reason = "proveedor de precios no disponible"
        if price is None or not np.isfinite(price) or price <= 0:
            if pred["status"] == "pendiente":
                try:
                    repo.set_prediction_status(pred["id"], "en_evaluacion")
                except ConflictError:
                    continue
            log.info("Predicción %s pendiente de evaluación (%s): %s", pred["id"], target, reason)
            pending += 1
            continue
        try:
            ev, status = evaluate(pred, price)
        except ValueError:
            log.info("Predicción %s pendiente de evaluación: precio de referencia inválido", pred["id"])
            pending += 1
            continue
        try:
            repo.save_evaluation(pred["id"], ev, status)
            done += 1
        except ConflictError:
            pass
    return {"evaluated": done, "waiting_for_data": pending}
