"""Signed forecasts for saving exactly what the public model endpoint emitted."""
from datetime import timedelta
from hashlib import sha256
import json

from itsdangerous import BadData, URLSafeSerializer

from app.security import InvalidInput
from ml_engine.calendar import utc_instant

MAX_TOKEN_LENGTH = 32768


def _signer(secret):
    return URLSafeSerializer(secret, salt="marketpredict.forecast-emission.v1",
                             signer_kwargs={"key_derivation": "hmac", "digest_method": sha256})


def issue(result, secret):
    provenance = result["provenance"]
    if not result["target_date"] or not provenance["expires_at"]:
        return None
    expires = min(utc_instant(provenance["cutoff_at"]) + timedelta(hours=1),
                  utc_instant(provenance["expires_at"]), utc_instant(provenance["target_close_at"]))
    forecasts = []
    for model in result["models"]:
        if model["status"] != "ok" or "predicted_log_return" not in model:
            continue
        log_return = model["predicted_log_return"]
        forecasts.append({
            "model_id": model["model_id"], "model_version": model["version"],
            "log_return": log_return, "return_pct": model["estimated_return_pct"],
            "price": model["estimated_price"], "interval": model.get("interval"),
            "direction": "alcista" if log_return > 0 else "bajista" if log_return < 0 else "neutral",
        })
    consensus = result["consensus"]
    if consensus["status"] == "ok":
        forecasts.append({
            "model_id": "consensus", "model_version": "1.0", "direction": consensus["direction"],
            "log_return": consensus["weighted_log_return"], "return_pct": consensus["weighted_return_pct"],
            "price": consensus["estimated_price"], "interval": consensus.get("interval"),
            "weights": consensus["weights"],
        })
    if not forecasts:
        return None
    payload = {"symbol": result["asset"], "horizon": result["horizon_days"],
               "reference_price": result["reference_price"], "target_date": result["target_date"],
               "data_source": result["data_source"], "provenance": provenance, "forecasts": forecasts,
               "expires_at": expires.isoformat()}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    emission_id = sha256(canonical.encode()).hexdigest()
    token = _signer(secret).dumps({**payload, "id": emission_id})
    return {"id": emission_id, "expires_at": payload["expires_at"], "token": token} if len(token) <= MAX_TOKEN_LENGTH else None


def read(token, secret):
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
        raise InvalidInput("Ejecuta los modelos y envía el token de la emisión mostrada.")
    try:
        return _signer(secret).loads(token)
    except BadData as exc:
        raise InvalidInput("Emisión no válida. Ejecuta los modelos nuevamente.") from exc


def prediction_rows(user_id, asset_id, emission):
    for forecast in emission["forecasts"]:
        yield {
            "source": "model", "user_id": user_id, "asset_id": asset_id, "direction": forecast["direction"],
            "reference_price": emission["reference_price"], "target_price": forecast["price"],
            "expected_return_pct": forecast["return_pct"], "horizon_days": emission["horizon"],
            "target_date": emission["target_date"], "is_public": False,
            "indicators": {"model_id": forecast["model_id"], "model_version": forecast["model_version"],
                           "data_source": emission["data_source"], "provenance": emission["provenance"],
                           "emission_id": emission["id"], "emission_expires_at": emission["expires_at"],
                           "forecast": forecast},
        }
