from pathlib import Path
from datetime import datetime

class InformeEmpresarial:
    def generar(self, root, simbolo, datos, indicadores, predicciones, alerta, tendencia):
        reports=Path(root)/"reports"; reports.mkdir(exist_ok=True)
        name=f"informe_{simbolo.upper()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        html=f"""<!doctype html><html><head><meta charset='utf-8'><title>Informe {simbolo}</title>
        <style>body{{font-family:Arial;margin:40px;color:#172033}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:8px}}.box{{padding:15px;background:#f3f6fa;margin:12px 0}}</style></head>
        <body><h1>Informe empresarial — {simbolo.upper()}</h1><p>Generado: {datetime.now():%Y-%m-%d %H:%M}</p>
        <div class='box'><b>Periodo:</b> {datos[0]['fecha']} a {datos[-1]['fecha']}<br><b>Tendencia:</b> {tendencia}<br><b>Alerta:</b> {alerta['nivel']} — {alerta['fundamento']}</div>
        <h2>Indicadores</h2><table><tr><th>Indicador</th><th>Valor</th><th>Descripción</th></tr>{''.join(f"<tr><td>{i['nombre']}</td><td>{i['valor']:.2f}</td><td>{i['descripcion']}</td></tr>" for i in indicadores)}</table>
        <h2>Predicciones</h2><table><tr><th>Fecha objetivo</th><th>Horizonte</th><th>Estimación</th><th>Modelo</th></tr>{''.join(f"<tr><td>{p['fecha_objetivo']}</td><td>{p['horizonte']}</td><td>{p['valor_estimado']:.2f}</td><td>{p['modelo']} {p['version']}</td></tr>" for p in predicciones)}</table>
        <p><small>Las predicciones son estimaciones computacionales y no constituyen garantía de comportamiento futuro ni instrucciones financieras.</small></p></body></html>"""
        path=reports/name; path.write_text(html,encoding="utf-8"); return path
