import statistics
from app.models.domain import Indicador

class AnalizadorMercado:
    def indicadores(self, datos):
        closes=[x["cierre"] for x in datos]
        n=min(14,len(closes))
        sma=sum(closes[-n:])/n
        mean=statistics.mean(closes)
        stdev=statistics.pstdev(closes)
        return [
            Indicador("Precio actual", closes[-1], "Último cierre disponible"),
            Indicador("Media móvil", sma, f"Promedio de los últimos {n} periodos"),
            Indicador("Volatilidad", stdev, "Desviación estándar de los cierres"),
            Indicador("Variación diaria", datos[-1]["variacion"], "Cambio porcentual frente al periodo anterior")
        ]

    def tendencia(self, datos):
        closes=[x["cierre"] for x in datos]
        half=max(5,len(closes)//2)
        a=sum(closes[:half])/half
        b=sum(closes[-half:])/half
        return "Alcista" if b>a*1.01 else "Bajista" if b<a*0.99 else "Lateral"
