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
