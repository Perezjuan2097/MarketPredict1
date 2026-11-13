"""Pulso de noticias: tono (positivo/negativo) de los titulares recientes de un activo.

Método: léxico financiero (inspirado en Loughran-McDonald) en inglés y español, con negaciones simples.
Es un TERMÓMETRO del ambiente informativo, NO un predictor validado: no existe un archivo histórico
de titulares para comprobar si este tono anticipó movimientos de precio, así que se muestra aparte del
motor de modelos y nunca se mezcla con sus probabilidades.
"""
import re

POSITIVE = {
    # inglés
    "beat", "beats", "record", "surge", "surges", "soar", "soars", "rally", "rallies", "gain", "gains", "jump",
    "jumps", "upgrade", "upgraded", "outperform", "strong", "growth", "profit", "profits", "bullish", "buy",
    "rebound", "rebounds", "boost", "boosts", "raise", "raises", "raised", "high", "highs", "win", "wins",
    "approval", "approved", "breakthrough", "expands", "expansion", "optimism", "optimistic", "recover", "recovery",
    "rises", "rise", "climbs", "climb", "safe", "haven", "dividend", "partnership", "exceeds", "tops",
    # español
    "sube", "suben", "récord", "alza", "ganancia", "ganancias", "crece", "crecimiento", "mejora", "mejoras",
    "beneficio", "beneficios", "repunta", "repunte", "optimismo", "aprobación", "supera", "fuerte", "dispara",
}
NEGATIVE = {
    "miss", "misses", "fall", "falls", "fell", "drop", "drops", "plunge", "plunges", "plummet", "plummeted",
    "slump", "slumps", "downgrade", "downgraded", "underperform", "weak", "loss", "losses", "bearish", "sell",
    "selloff", "sell-off", "cut", "cuts", "lawsuit", "probe", "investigation", "scandal", "fraud", "recall",
    "warning", "warns", "decline", "declines", "risk", "risks", "fear", "fears", "crash", "crisis", "layoffs",
    "resigns", "resignation", "ouster", "tumble", "tumbles", "slide", "slides", "rout", "turmoil", "uncertainty",
    "worst", "low", "lows", "bankruptcy", "default", "tariff", "tariffs", "fine", "fined", "delay", "delays",
    # español
    "cae", "caen", "caída", "baja", "bajan", "pérdida", "pérdidas", "desploma", "desplome", "recorte", "demanda",
    "investigación", "escándalo", "fraude", "crisis", "riesgo", "miedo", "renuncia", "despidos", "quiebra",
}
NEGATIONS = {"not", "no", "never", "without", "sin", "nunca"}
WORD = re.compile(r"[a-záéíóúñü\-]+", re.IGNORECASE)


def score_text(text):
    words = [w.lower() for w in WORD.findall(text or "")]
    pos = neg = 0
    for i, w in enumerate(words):
        flip = i > 0 and words[i - 1] in NEGATIONS
        if w in POSITIVE:
            neg, pos = (neg + 1, pos) if flip else (neg, pos + 1)
        elif w in NEGATIVE:
            pos, neg = (pos + 1, neg) if flip else (pos, neg + 1)
    return pos, neg


def pulse(news, symbol=None):
    """news: lista de {"title", "summary"?}. Devuelve el tono agregado y por titular."""
    items, total_pos, total_neg = [], 0, 0
    for n in news or []:
        pos, neg = score_text(f"{n.get('title', '')} {n.get('summary') or ''}")
        total_pos, total_neg = total_pos + pos, total_neg + neg
        tone = "positivo" if pos > neg else "negativo" if neg > pos else "neutral"
        items.append({"title": n.get("title"), "tone": tone})
    hits = total_pos + total_neg
    score = (total_pos - total_neg) / hits if hits else 0.0
    label = ("Muy positivo" if score >= 0.5 else "Positivo" if score >= 0.15 else
             "Muy negativo" if score <= -0.5 else "Negativo" if score <= -0.15 else "Neutral")
    counts = {t: sum(1 for i in items if i["tone"] == t) for t in ("positivo", "neutral", "negativo")}
    return {
        "score": round(score, 3), "label": label, "headlines": len(items), "counts": counts, "items": items,
        "method": "Léxico financiero sobre titulares recientes (inglés y español)",
        "validated": False,
        "note": ("Termómetro del ambiente de noticias, no una predicción validada: las noticias suelen reflejarse en el "
                 "precio en minutos, antes de que un inversor común pueda reaccionar."),
    }
