# MarketPredict Enterprise · Sprint 4 — Automatización

## Flujo y permisos

En **Empresas → Datos empresariales → Predicciones empresariales**, elige una versión y completa el formulario de pronóstico. La solicitud vuelve con un ID de trabajo. El navegador consulta su estado y recupera el pronóstico cuando el worker lo guarda. En **Monitoreo y automatizaciones** se pueden encolar trabajos, programarlos, consultar indicadores y alertas y descargar reportes privados.

| Operación | owner / admin | member | viewer |
|---|---|---|---|
| Consultar monitoreo, resultados y reportes | Sí | Sí | Sí |
| Encolar un trabajo manual | Sí | Sí | — |
| Cancelar un trabajo propio activo | Sí | Sí | — |
| Cancelar otro trabajo del dataset, programar y administrar reglas | Sí | — | — |

La aplicación resuelve organización, dataset y versión en SQL. Comprueba permisos al encolar, reclamar y publicar. Si se revoca la membresía o se archiva el dataset durante un trabajo, se descarta el resultado. Las programaciones se ejecutan bajo el usuario que las creó y requieren que conserve permiso para administrarlas. Pausar una automatización impide que sus trabajos pendientes publiquen mientras esté pausada. Los cambios manuales dejan eventos de auditoría con IDs internos.

## Cola y ejecución

Estados persistidos `pending`, `running`, `completed`, `failed` y `canceled`. Las reservas se serializan en PostgreSQL y SQLite y tienen un token por intento. Se renuevan cada cinco segundos y vencen a los 30 segundos. Al encontrar una reserva vencida, el worker reprograma o termina el trabajo. Una publicación comprueba la reserva y guarda resultado y alertas en una sola transacción. El cálculo puede repetirse tras perder una reserva. Sólo el intento vigente puede publicar.

La política aplica tres intentos como máximo, con esperas de 5 y 10 segundos. Se reintentan tiempos excedidos, cierres del proceso hijo, desconexiones recuperables y apagados del worker. Se terminan los errores de entrada y los fallos de cómputo permanentes. Una organización puede tener un trabajo ejecutando y hasta 20 activos entre pendientes y ejecutando. El tope global usa `ENTERPRISE_FORECAST_CONCURRENCY` (1–4, por defecto 1). Cada proceso worker ejecuta un hijo a la vez. El límite global sólo aumenta si se ejecutan varios workers.

El worker crea un proceso hijo con hilos numéricos limitados a uno. En Linux aplica límites duros de tiempo de CPU y espacio de direcciones. El padre impone tiempo de pared y termina el hijo ante cancelación, pérdida de reserva o cierre. En Windows se comprueban tiempo de pared y flujo de procesos. Los límites duros de CPU y memoria se aplican en Linux. Variables opcionales:

- `ENTERPRISE_WORKER_WALL_SECONDS=120`
- `ENTERPRISE_WORKER_CPU_SECONDS=90`
- `ENTERPRISE_WORKER_MEMORY_MIB=768`

La configuración de memoria debe dejar espacio para proceso supervisor, bibliotecas y proceso hijo. Los límites de ventana, horizonte y candidatos del Sprint 3 siguen vigentes. La cola no depende de memoria compartida ni del sistema de archivos web.

## Programación, alertas y reportes

- **Programación diaria o semanal:** escoge una hora UTC y, para frecuencia semanal, el día 0–6 (lunes a domingo). Una fila de programación produce como máximo un trabajo por turno. Después de una interrupción se atiende un turno atrasado y se calcula el siguiente desde la hora corriente. La versión y el rango de entrada son explícitos y fijos. Repetir una programación no implica que se hayan importado datos nuevos. Para cambiar la versión, pausa la programación y crea otra.
- **Indicadores:** toma los KPIs reales, calidad, fecha de último dato y procedencia de la versión seleccionada.
- **Evaluación:** compara un pronóstico existente contra el mismo objetivo y segmento en la versión explícita de resultados observados. Requiere que hayan vencido todas las fechas UTC y que existan las observaciones exactas o ceros declarados. Si faltan, registra `pending_actuals` sin calcular métricas. La interfaz separa este estado de los fallos del trabajo.
- **Alertas:** se pueden configurar cambios previstos de ventas o demanda frente a la suma del mismo número de días previos, deterioro de WAPE real frente al bloque final histórico en puntos porcentuales, antigüedad desde el último dato y fallo terminal. La evidencia indica versión, trabajo, valor, umbral y severidad. La clave regla/evento deduplica repeticiones. Si no hay denominador para una variación o WAPE, la señal queda indisponible. Las alertas viven en el dashboard.
- **Reportes:** guardan una instantánea con métricas, calidad, procedencia, riesgos, alertas y el pronóstico listo de la versión si existe. Se generan en el worker. CSV y PDF se construyen desde esa instantánea al descargar, con sesión y membresía vigentes. El CSV protege celdas ante fórmulas de hoja de cálculo. El PDF paginado usa WinAnsi y reemplaza caracteres fuera de ese alfabeto. El CSV conserva Unicode.

## API

Base `/api/me/organizations/<slug>/datasets/<id>/automation`. Las respuestas son privadas con `Cache-Control: no-store`. Las mutaciones requieren CSRF.

| Método y ruta | Función |
|---|---|
| `GET` base | Dashboard. Incluye estados recientes, programaciones, reglas, alertas, reportes e indicadores disponibles. |
| `POST /jobs` | Encola trabajo con `kind` y `version`. `forecast` acepta el contrato del Sprint 3. `evaluation` necesita `run_id` y puede declarar `zero_dates`. `report` admite `run_id` opcional. |
| `GET /jobs/<id>`, `DELETE /jobs/<id>` | Consulta o cancela un trabajo activo. |
| `POST /schedules`, `PATCH /schedules/<id>` | Crea con parámetros del trabajo, `cadence`, `hour_utc` y `weekday` cuando sea semanal. Pausa o reactiva con `enabled`. |
| `POST /rules`, `PATCH /rules/<id>` | Crea regla con `kind`, `comparison`, `threshold` y `severity`, o modifica el estado `enabled`. |
| `PATCH /alerts/<id>` | Resuelve una alerta. |
| `GET /reports/<id>/csv`, `GET /reports/<id>/pdf` | Descarga una instantánea completada. |

`POST /forecasts` del Sprint 3 conserva sus parámetros y ahora responde **202 con `job`**. Al completar, `job.result.run_id` identifica la ejecución que se recupera con el GET existente. El historial del Sprint 3 sigue accesible. La cabecera `Idempotency-Key` permite reintentar una solicitud sin duplicarla. Omitirla usa una huella del contenido. Enviar la misma clave con distinta entrada responde 409. Usar una nueva clave para solicitar intencionalmente otra ejecución.

## Migración y operación autorizada

La revisión aditiva `20261122_enterprise_automation` añade trabajos, horarios, reglas y alertas con RLS. Los reportes, indicadores y evaluaciones se guardan como resultados de trabajos. No modifica las ejecuciones históricas. El downgrade sólo retira las tablas nuevas si están vacías. Con datos, revertir web y worker y conservar las tablas.

Una intervención futura debe respaldar y comprobar revisión, rol y RLS, aplicar la revisión en ambiente de ensayo y arrancar el worker separado con:

```sh
python -m scripts.enterprise_worker
```

`--once` permite un ensayo con un clúster aislado. En Render, usar un **Background Worker** sin tráfico entrante, con la misma revisión y conexión PostgreSQL aprobada que la aplicación web, y recursos donde quepan los límites configurados. No iniciar el worker en Gunicorn ni modificar el proceso web desplegado. El handler de SIGTERM deja de aceptar trabajos y entrega el intento para reintento. El siguiente worker puede recuperar un intento abandonado. [Background Workers de Render](https://render.com/docs/background-workers) · [Apagado durante despliegues](https://render.com/docs/deploys#graceful-shutdown)

Pendientes operativos antes de producción: medir RAM y tiempo bajo carga en Linux, verificar permisos efectivos del rol PostgreSQL y definir retención de resultados antiguos. No se incluyeron notificaciones externas ni una política de purga automática.
