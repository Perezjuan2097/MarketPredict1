from datetime import datetime
from html import escape
from io import BytesIO
import re

class InformeEmpresarial:
    def generar(self, simbolo, datos, indicadores, predicciones, alerta, tendencia):
        safe_symbol = re.sub(r"[^A-Z0-9._-]", "_", simbolo.upper())[:40] or "activo"
        name = f"informe_{safe_symbol}_{datetime.now():%Y%m%d_%H%M%S}.html"
        html=f"""<!doctype html><html><head><meta charset='utf-8'><title>Informe {escape(simbolo)}</title>
        <style>body{{font-family:Arial;margin:40px;color:#172033}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:8px}}.box{{padding:15px;background:#f3f6fa;margin:12px 0}}</style></head>
        <body><h1>Informe empresarial — {escape(simbolo.upper())}</h1><p>Generado: {datetime.now():%Y-%m-%d %H:%M}</p>
        <div class='box'><b>Periodo:</b> {escape(str(datos[0]['fecha']))} a {escape(str(datos[-1]['fecha']))}<br><b>Tendencia:</b> {escape(tendencia)}<br><b>Alerta:</b> {escape(alerta['nivel'])} — {escape(alerta['fundamento'])}</div>
        <h2>Indicadores</h2><table><tr><th>Indicador</th><th>Valor</th><th>Descripción</th></tr>{''.join(f"<tr><td>{escape(i['nombre'])}</td><td>{i['valor']:.2f}</td><td>{escape(i['descripcion'])}</td></tr>" for i in indicadores)}</table>
        <h2>Predicciones</h2><table><tr><th>Fecha objetivo</th><th>Horizonte</th><th>Estimación</th><th>Modelo</th></tr>{''.join(f"<tr><td>{escape(str(p['fecha_objetivo']))}</td><td>{escape(p['horizonte'])}</td><td>{p['valor_estimado']:.2f}</td><td>{escape(p['modelo'])} {escape(p['version'])}</td></tr>" for p in predicciones)}</table>
        <p><small>Las predicciones son estimaciones computacionales y no constituyen garantía de comportamiento futuro ni instrucciones financieras.</small></p></body></html>"""
        return BytesIO(html.encode("utf-8")), name
