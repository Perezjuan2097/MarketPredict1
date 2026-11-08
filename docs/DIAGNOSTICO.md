# Diagnóstico técnico inicial (Fase 0)

Proyecto: Flask monolítico, ~3.200 líneas. Servicios: data, analysis, prediction, alert, report, storage, community. Vista única `index.html`.

## Hallazgos
1. `/admin/` devolvía 404 (blueprint sin prefijo) y `admin.html` estaba vacío; `/admin/config` modificaba `os.environ` sin autenticación. **Corregido.**
2. Dos fábricas de app; `SECRET_KEY` fija y `debug=True`. **Corregido.**
3. Comunidad sin identidad; votos duplicables; IDs `len+1`; JSON sin bloqueo. **Pendiente (Fase 4, requiere BD).**
4. `/api/save` aceptaba payload arbitrario; 3 de 4 registros existentes están duplicados; el campo `real` nunca se rellena. **Validación y deduplicación corregidas; evaluación automática pendiente (Fase 3).** Los registros históricos no se han modificado.
5. Sin CSRF/límites/logs/cabeceras. **Corregido** (CSRF en admin; el resto de formularios llegará con las sesiones de usuario).
6. XSS: el JS escapa el contenido de usuario de forma consistente (`escapeHTML`). Sin hallazgos.
7. Modo demo generaba datos sintéticos por defecto sin avisar. **Ahora desactivado por defecto y señalizado en la interfaz.**
8. Fechas del demo serializadas como fecha HTTP. **Corregido (ISO).**
9. Predicción: regresión lineal sin intervalo ni validación; días naturales; alerta basada solo en caída proyectada. **Pendiente (Fase 3/5).**
10. Cobertura de pruebas: 1 prueba de humo. **Ahora 12 pruebas.**
11. Módulos visuales sin función: Productos, Brókers, Más, Opciones.
