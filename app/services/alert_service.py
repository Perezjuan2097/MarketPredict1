def alerta_desde_motor(resultado, drop_threshold):
    """Alerta basada en el consenso validado del motor, no en una recta sin validar.

    Solo se emite una señal (ALTA/MEDIA/BAJA) si algún modelo superó a las referencias
    simples fuera de muestra. Si no, se informa que no hay señal fiable.
    """
    cons = resultado.get("consensus") or {}
    h = resultado["horizon_days"]
    objetivo = resultado["target_date"]
    if cons.get("status") != "ok":
        return {"nivel": "SIN SEÑAL", "fecha": objetivo,
                "fundamento": "Ningún modelo superó a las referencias simples fuera de muestra, "
                              "así que no hay una señal fiable de caída ni de subida."}
    change = cons["weighted_return_pct"]
    detalle = (f"El consenso validado estima {change:+.2f}% a {h} sesiones "
               f"(dirección {cons.get('direction')}, incertidumbre {str(cons.get('uncertainty_level', '')).lower()}).")
    if cons.get("direction") == "bajista" and change <= -abs(drop_threshold):
        nivel = "ALTA"
    elif cons.get("direction") == "bajista":
        nivel = "MEDIA"
    else:
        nivel = "BAJA"
    return {"nivel": nivel, "fecha": objetivo, "fundamento": detalle}
