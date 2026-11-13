from datetime import date, timedelta
from app.models.domain import Prediccion

class ModeloPredictivo:
    def predecir(self, datos, horizon, version):
        raise NotImplementedError

class ModeloML(ModeloPredictivo):
    """OBSOLETO: recta sobre 30 cierres, sin validación. Ya no se usa en la app (ver predicciones_desde_motor)."""
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


def predicciones_desde_motor(resultado):
    """Convierte la salida de ml_engine.run_all en la tarjeta de "Predicciones".

    Así "Analizar", la alerta, el informe y el consenso usan el MISMO motor validado (antes
    la tarjeta usaba ModeloML, una recta sobre 30 cierres sin validación, que podía
    contradecir al consenso).
    """
    cons = resultado.get("consensus") or {}
    h = resultado["horizon_days"]
    ref = resultado["reference_price"]
    hoy = resultado["as_of"]
    objetivo = resultado["target_date_approx"]
    horizonte = f"{h} sesión" if h == 1 else f"{h} sesiones"
    out = []
    if cons.get("status") == "ok" and cons.get("estimated_price"):
        intervalo = cons.get("interval") or {}
        out.append(dict(Prediccion(hoy, objetivo, horizonte, round(cons["estimated_price"], 2),
                                   "Consenso multimodelo", "ml_engine").__dict__,
                        intervalo_bajo=round(intervalo["low_price"], 2) if intervalo else None,
                        intervalo_alto=round(intervalo["high_price"], 2) if intervalo else None,
                        valor_predictivo=True,
                        nota=f"Dirección {cons.get('direction')} · incertidumbre {cons.get('uncertainty_level', '—').lower()}."))
    for m in resultado.get("models", []):
        if m.get("status") != "ok" or m.get("estimated_price") is None:
            continue
        intervalo = m.get("interval") or {}
        out.append(dict(Prediccion(hoy, objetivo, horizonte, round(m["estimated_price"], 2),
                                   m.get("name") or m["model_id"], m.get("version", "")).__dict__,
                        intervalo_bajo=round(intervalo["low_price"], 2) if intervalo else None,
                        intervalo_alto=round(intervalo["high_price"], 2) if intervalo else None,
                        valor_predictivo=cons.get("status") == "ok",
                        nota="Sin valor predictivo demostrado: no supera a las referencias simples."
                        if cons.get("status") != "ok" else None))
    if not out:
        raise ValueError("El motor de modelos no produjo predicciones para este activo.")
    return out
