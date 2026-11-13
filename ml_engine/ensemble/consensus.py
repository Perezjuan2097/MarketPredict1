"""Consenso ponderado de modelos.

Pesos: proporcionales a la mejora fuera de muestra sobre la mejor referencia simple (skill = 1 − RMSE/RMSE_ref),
recortada en 0. Un modelo que no supera a la referencia recibe peso 0 aunque prediga algo "optimista".
Si ningún modelo supera a la referencia, no hay estimación ponderada y el consenso se declara
"sin valor predictivo demostrado".
"""
import numpy as np


def compute(results, neutral_factor=0.25):
    ok = [r for r in results if r["status"] == "ok" and not r["is_baseline"] and "predicted_log_return" in r]
    if not ok:
        return {"status": "sin_modelos", "message": "Ningún modelo produjo resultados."}
    preds = np.array([r["predicted_log_return"] for r in ok])
    # Solo reciben peso los modelos cuya mejora es estadísticamente significativa (si se calculó).
    skills = np.array([max(r["historical_metrics"].get("skill_vs_baseline") or 0.0, 0.0)
                       if r["historical_metrics"].get("significant", True) else 0.0 for r in ok])
    rmses = np.array([r["historical_metrics"]["rmse_return"] for r in ok])
    pct = lambda lr: float(np.exp(lr) - 1) * 100  # noqa: E731

    out = {
        "n_models": len(ok),
        "simple_median_return_pct": round(pct(np.median(preds)), 4),
        "dispersion_std_pct": round(float(np.std([pct(p) for p in preds], ddof=0)), 4),
        "min_return_pct": round(pct(preds.min()), 4), "max_return_pct": round(pct(preds.max()), 4),
        "best_model_by_history": ok[int(np.argmax([r["historical_metrics"].get("skill_vs_baseline") or -9 for r in ok]))]["model_id"],
        "weights": {},
    }
    if skills.sum() <= 0:
        out.update(status="sin_valor_predictivo", direction="neutral", weighted_return_pct=None,
                   message="Ningún modelo superó a las referencias simples fuera de muestra de forma significativa: no se calcula consenso ponderado.",
                   weights={r["model_id"]: 0.0 for r in ok})
        return out

    w = skills / skills.sum()
    central = float(np.dot(w, preds))
    w_rmse = float(np.dot(w, rmses))
    band = neutral_factor * w_rmse
    direction = "alcista" if central > band else "bajista" if central < -band else "neutral"
    agree = float(np.mean(np.sign(preds) == np.sign(central))) if central != 0 else 0.0
    ratio = w_rmse / max(abs(central), 1e-9)
    out.update(
        status="ok", direction=direction, weighted_return_pct=round(pct(central), 4),
        weighted_log_return=central, weighted_rmse=w_rmse, agreement_share=round(agree, 3),
        uncertainty_level="Alta" if ratio > 3 else "Media" if ratio > 1 else "Baja",
        uncertainty_ratio=round(ratio, 2), neutral_band_pct=round(pct(band), 4),
        weights={r["model_id"]: round(float(x), 4) for r, x in zip(ok, w)},
        message="Dirección neutral si el retorno central es menor que ¼ del error típico fuera de muestra.",
    )
    return out
