"""Traduce los resultados del motor a lenguaje sencillo (para cualquier persona) sin exagerar.

Regla: nunca se afirma certeza. Cada frase dice qué tan confiable fue esa parte del sistema en el pasado.
"""


def _pct(x, d=1):
    return f"{x:+.{d}f} %".replace(".", ",")


def _p(x):
    return f"{x * 100:.0f} %"


def build(result):
    h = result["horizon_days"]
    ses = "sesión" if h == 1 else f"{h} sesiones"
    cons, prob, risk = result.get("consensus") or {}, result.get("probability") or {}, result.get("risk") or {}
    mc = result.get("monte_carlo") or {}
    points, verdict = [], "neutral"

    # 1) Dirección
    if prob.get("has_edge"):
        p = prob["prob_up"]
        points.append(f"Probabilidad de que el precio esté más alto dentro de {ses}: {_p(p)} "
                      f"(modelo: {prob['source']}; en pruebas pasadas mejoró a la referencia).")
        verdict = "favorable" if p >= 0.58 else "desfavorable" if p <= 0.42 else "neutral"
    elif prob:
        points.append(f"Ningún modelo adivinó la dirección mejor que la estadística básica: históricamente este activo "
                      f"subió en {_p(prob['base_rate'])} de los periodos de {ses}. Eso es lo más honesto que se puede decir "
                      f"sobre si subirá o bajará.")
    if cons.get("status") == "ok":
        points.append(f"El consenso de modelos de precio estima {_pct(cons['weighted_return_pct'], 2)} "
                      f"(dirección {cons['direction']}, incertidumbre {str(cons.get('uncertainty_level', '')).lower()}).")
    else:
        points.append("Los modelos de precio no superaron a las referencias simples, así que no se muestra un precio objetivo.")

    # 2) Riesgo (la parte más fiable)
    if risk:
        rg = risk["range_90"]
        cov = rg.get("historical_coverage")
        points.append(f"Riesgo: el precio se suele mover cerca de ±{risk['expected_move_pct']:.1f} % en {ses}. "
                      f"Rango probable (90 %): {rg['low_price']:,.2f} a {rg['high_price']:,.2f}. "
                      + (f"En el pasado este rango acertó {_p(cov)} de las veces. " if cov is not None else "")
                      + f"Nivel de riesgo actual: {risk['risk_level'].lower()} comparado con su historia.")
    if mc.get("prob_loss_gt_10pct") is not None and mc["prob_loss_gt_10pct"] > 0.01:
        points.append(f"Escenario malo: en las simulaciones, {_p(mc['prob_loss_gt_10pct'])} de los casos pierden más de 10 %.")

    reliability = ("Media" if prob.get("has_edge") or cons.get("status") == "ok"
                   else "Baja para la dirección; buena para el riesgo" if risk.get("beats_reference")
                   else "Baja")
    if verdict == "neutral":
        headline = "Sin ventaja clara en la dirección"
    else:
        p = prob["prob_up"]
        fuerza = "" if abs(p - 0.5) >= 0.15 else "ligeramente "
        headline = (f"Señal estadística {fuerza}{verdict} "
                    f"({_p(p)} de probabilidad de subir en {ses})")
    return {
        "headline": headline, "verdict": verdict, "reliability": reliability, "points": points,
        "what_to_do": ("Usa esto para dimensionar el riesgo (cuánto podrías ganar o perder), no como una orden de compra o venta. "
                       "Ningún modelo garantiza resultados; invierte solo lo que puedas permitirte perder y diversifica."),
    }


GLOSSARY = {
    "Probabilidad de subida": "Porcentaje de veces que, en situaciones parecidas, el precio terminó más alto.",
    "Referencia simple": "Una regla tonta (\"el precio no cambia\" o \"sube lo de siempre\"). Si un modelo no le gana, no aporta.",
    "Walk-forward": "Se entrena con el pasado y se prueba con el 'futuro' que el modelo no vio, como en la vida real.",
    "Volatilidad": "Cuánto se mueve el precio normalmente. Más volatilidad = más riesgo y más incertidumbre.",
    "Intervalo 90 %": "Rango donde el precio debería quedar 9 de cada 10 veces si el modelo está bien calibrado.",
    "Brier score": "Error de una probabilidad (0 es perfecto). Se compara con la referencia para ver si el modelo aporta.",
    "VIX": "Índice del 'miedo' del mercado de EE. UU.: sube cuando los inversores esperan movimientos fuertes.",
}
