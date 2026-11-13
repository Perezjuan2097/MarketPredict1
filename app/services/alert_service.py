class ClasificadorAlertas:
    def clasificar(self, datos, predicciones, drop_threshold):
        actual=datos[-1]["cierre"]; futuro=predicciones[-1]["valor_estimado"]
        change=((futuro-actual)/actual)*100
        if change <= -abs(drop_threshold):
            nivel="ALTA"; fundamento=f"El modelo estima una variación de {change:.2f}% respecto al último cierre."
        elif change < 0:
            nivel="MEDIA"; fundamento=f"El modelo estima una variación negativa de {change:.2f}%."
        else:
            nivel="BAJA"; fundamento=f"El modelo no estima una caída: variación proyectada de {change:.2f}%."
        return {"nivel":nivel,"fecha":predicciones[-1]["fecha_objetivo"],"fundamento":fundamento}


def alerta_desde_motor(resultado, drop_threshold):
    """Alerta basada en el consenso validado del motor, no en una recta sin validar.

    Solo se emite una señal (ALTA/MEDIA/BAJA) si algún modelo superó a las referencias
    simples fuera de muestra. Si no, se informa que no hay señal fiable.
    """
    cons = resultado.get("consensus") or {}
    h = resultado["horizon_days"]
    objetivo = resultado["target_date_approx"]
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
