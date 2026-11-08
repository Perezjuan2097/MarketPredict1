from datetime import date, timedelta
from app.models.domain import Prediccion

class ModeloPredictivo:
    def predecir(self, datos, horizon, version):
        raise NotImplementedError

class ModeloML(ModeloPredictivo):
    """Regresión lineal simple sobre cierres recientes: demostración de MVP."""
    def predecir(self, datos, horizon, version):
        closes=[x["cierre"] for x in datos[-30:]]
        n=len(closes)
        if n < 20: raise ValueError("No hay suficientes datos para el modelo.")
        xs=list(range(n)); mx=sum(xs)/n; my=sum(closes)/n
        den=sum((x-mx)**2 for x in xs)
        slope=sum((x-mx)*(y-my) for x,y in zip(xs,closes))/den if den else 0
        intercept=my-slope*mx
        last=closes[-1]
        out=[]
        for h in range(1,horizon+1):
            value=max(0.01, intercept+slope*(n-1+h))
            out.append(Prediccion(str(date.today()), str(date.today()+timedelta(days=h)), f"{h} día(s)", round(value,2), "Regresión lineal", version).__dict__)
        return out
