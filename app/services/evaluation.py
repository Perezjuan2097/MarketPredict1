"""Evaluación de una predicción contra el resultado real. Función pura, sin acceso a datos externos."""


def evaluate(pred, actual_price, neutral_band_pct=1.0):
    """Devuelve (evaluación, nuevo_estado).

    Criterio de acierto (documentado a propósito): alcista = el precio real superó al de referencia;
    bajista = quedó por debajo; neutral = el movimiento absoluto fue <= `neutral_band_pct` %.
    Si hay precio objetivo (o retorno esperado) se informa también el error absoluto y porcentual.
    Siempre se compara con la referencia ingenua "mantener el último precio".
    """
    ref, actual = float(pred["reference_price"]), float(actual_price)
    if ref <= 0 or actual <= 0:
        raise ValueError("Precios no válidos para evaluar.")
    ret = (actual / ref - 1.0) * 100.0
    direction = pred["direction"]
    if direction == "alcista":
        ok = ret > 0
    elif direction == "bajista":
        ok = ret < 0
    else:
        ok = abs(ret) <= neutral_band_pct
    if pred.get("target_price") is not None:
        predicted = float(pred["target_price"])
    elif pred.get("expected_return_pct") is not None:
        predicted = ref * (1.0 + float(pred["expected_return_pct"]) / 100.0)
    else:
        predicted = None
    abs_err = None if predicted is None else abs(actual - predicted)
    ev = {"actual_price": round(actual, 6), "actual_return_pct": round(ret, 4),
          "abs_error": None if abs_err is None else round(abs_err, 6),
          "pct_error": None if abs_err is None else round(abs_err / actual * 100.0, 4),
          "direction_correct": bool(ok), "baseline_abs_error": round(abs(actual - ref), 6)}
    return ev, ("cumplida" if ok else "no_cumplida")
