"""Probabilidad de que el precio SUBA en `horizon` sesiones, con su calibración fuera de muestra.

Idea para principiantes: en vez de prometer "va a subir", el sistema dice "hay un 56 % de probabilidad"
y muestra si, en el pasado, cuando dijo 56 % realmente subió ~56 % de las veces.

Validación: los mismos bloques walk-forward (con purga del horizonte) que el motor de retornos.
Referencia: la frecuencia histórica de subidas del periodo de entrenamiento ("casi siempre sube un poco
más de la mitad de las veces"). Un modelo solo se usa si mejora el Brier score de esa referencia de forma
estadísticamente significativa (Diebold-Mariano con Newey-West): mejorar "por suerte" no cuenta.
"""
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ml_engine.evaluation import backtest as bt
from ml_engine.evaluation.metrics import SIGNIFICANT_T, diebold_mariano

CANDIDATES = {
    "logistica": ("Regresión logística", lambda seed: make_pipeline(StandardScaler(), LogisticRegression(C=0.05, max_iter=500))),
    "bosque": ("Bosque aleatorio (clasificación)", lambda seed: RandomForestClassifier(
        n_estimators=150, max_depth=3, min_samples_leaf=20, random_state=seed, n_jobs=1)),
}
MIN_SKILL = 0.005  # mejora mínima del Brier sobre la referencia para usar el modelo


def _proba(model, X):
    p = model.predict_proba(X)
    classes = list(getattr(model, "classes_", [0, 1]))
    return p[:, classes.index(1)] if 1 in classes else np.zeros(len(X))


def calibration_table(prob, actual, edges=(0.0, 0.4, 0.47, 0.53, 0.6, 1.0)):
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (prob >= lo) & (prob < hi if hi < 1 else prob <= hi)
        if m.sum() == 0:
            continue
        rows.append({"rango": f"{lo * 100:.0f}–{hi * 100:.0f} %", "prob_media": round(float(prob[m].mean()), 4),
                     "frecuencia_real": round(float(actual[m].mean()), 4), "casos": int(m.sum())})
    return rows


def run(X, y, x_now, horizon, seed=0, n_splits=5):
    up = (np.asarray(y) > 0).astype(int)
    blocks = bt.split_blocks(len(X), horizon, n_splits)
    idx = np.concatenate([np.arange(a, b) for _, a, b in blocks])
    actual = up[idx]
    base = np.concatenate([np.full(b - a, up[: tr + 1].mean()) for tr, a, b in blocks])
    brier_base = float(np.mean((base - actual) ** 2))

    evaluated = []
    for key, (name, make) in CANDIDATES.items():
        try:
            preds = []
            for tr, a, b in blocks:
                m = make(seed).fit(X.iloc[: tr + 1], up[: tr + 1])
                preds.append(_proba(m, X.iloc[a:b]))
            p = np.concatenate(preds)
            brier = float(np.mean((p - actual) ** 2))
            t_stat, p_value = diebold_mariano((p - actual) ** 2, (base - actual) ** 2, lag=horizon)
            skill = 1 - brier / brier_base if brier_base > 0 else 0.0
            confident = (p >= 0.6) | (p <= 0.4)
            hits = ((p >= 0.5) == (actual == 1))
            evaluated.append({
                "id": key, "name": name, "brier": round(brier, 5), "brier_skill": round(skill, 4),
                "accuracy": round(float(hits.mean()), 4),
                "confident_cases": int(confident.sum()),
                "confident_accuracy": round(float(hits[confident].mean()), 4) if confident.any() else None,
                "calibration": calibration_table(p, actual), "_make": make,
                "dm_t": t_stat, "p_value": p_value,
                "significant": bool(t_stat is not None and t_stat > SIGNIFICANT_T and skill > MIN_SKILL),
            })
        except Exception as exc:  # un candidato que falle no tumba el resto
            evaluated.append({"id": key, "name": name, "error": str(exc)})

    ok = [e for e in evaluated if "error" not in e]
    best = max(ok, key=lambda e: e["brier_skill"]) if ok else None
    base_rate_all = float(up.mean())
    if best and best["significant"]:
        model = best["_make"](seed).fit(X, up)
        prob_now = float(_proba(model, x_now)[0])
        source, useful = best["name"], True
    else:
        prob_now, source, useful = base_rate_all, "Frecuencia histórica de subidas", False
    for e in evaluated:
        e.pop("_make", None)
    return {
        "prob_up": round(prob_now, 4), "source": source, "has_edge": useful,
        "base_rate": round(base_rate_all, 4), "brier_reference": round(brier_base, 5),
        "n_oos": int(len(idx)), "models": evaluated, "best_model": best["id"] if best else None,
    }
