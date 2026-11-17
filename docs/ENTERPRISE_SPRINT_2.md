# MarketPredict Enterprise · Sprint 2 — Gestión de datos

## Uso

En **Empresas**, selecciona una organización y abre **Datos empresariales**. Crea un dataset, elige un CSV o un XLSX, lee los encabezados y asigna las columnas. La segunda vista previa muestra filas normalizadas, faltantes, duplicados y errores por fila. Confirma sólo cuando la vista previa sea válida. Cada confirmación aceptada guarda una versión completa. Selecciona una versión para consultar sus registros, calidad, métricas e historial. El dashboard calcula registros, ventas, unidades, productos distintos y totales de hasta 90 fechas con datos. Los totales generales abarcan toda la versión. Se conserva la moneda de la organización al crear el dataset. No se mezclan unidades entre versiones ni monedas entre datasets.

| Operación | owner | admin | member | viewer |
|---|---|---|---|---|
| Consultar datasets, métricas, registros e historial | Sí | Sí | Sí | Sí |
| Inspeccionar, validar y confirmar importaciones | Sí | Sí | Sí | — |
| Crear o renombrar datasets | Sí | Sí | — | — |
| Archivar dataset | Sí | — | — | — |

El slug y el ID sólo seleccionan recursos. La sesión identifica al actor y el servidor consulta su membresía vigente. Un dataset de otra organización o archivado responde 404. Permisos insuficientes dentro de la organización responden 403. No hay autorización empresarial implícita para administradores globales. Archivar retira el dataset y todas sus versiones de las rutas de lectura. El archivado conserva filas para recuperación y no dispone todavía de restauración ni purga por API.

## Contrato de datos

`sales-daily-v1` requiere fecha, producto y cantidad, más al menos una columna de precio o ingresos. Se puede mapear categoría y también suministrar precio e ingresos. Una fila puede omitir uno de estos dos valores si conserva el otro. Las columnas mapeadas deben ser distintas. Las filas completamente vacías se omiten. Los duplicados exactos después de normalizar los seis campos impiden confirmar el lote. Si dos operaciones legítimas tienen los mismos seis campos, preparar un origen agregado o un contrato que distinga transacciones.

Fechas CSV en `AAAA-MM-DD`, fechas numéricas enteras XLSX con época 1900 o 1904. La fecha de Excel 1900-02-29 se rechaza. Se conservan cantidades e ingresos negativos para devoluciones. El precio debe ser no negativo. Se admiten hasta cuatro decimales y un valor absoluto de 10.000.000.000. Los números usan punto decimal y las ventas derivadas se redondean a cuatro decimales. Cuando el origen informa ingresos se conservan tal como llegan y se cuentan discrepancias contra cantidad × precio. Esto permite descuentos o ajustes sin ocultar diferencias. Los productos y categorías son texto literal, normalizado NFC, con máximo 160 caracteres.

- **CSV** UTF-8 con BOM opcional y separador coma o punto y coma. Encabezados únicos y celdas con longitud acotada.
- **XLSX** OOXML convencional con una hoja visible, texto compartido o en línea y números. El lector procesa XML UTF-8 dentro de un ZIP limitado. Rechaza fórmulas, enlaces externos, celdas combinadas, macros, ZIP cifrados, XML con DTD y contenido dañado. No evalúa fórmulas ni consulta rutas externas. [Estructura de SpreadsheetML](https://learn.microsoft.com/en-us/office/open-xml/spreadsheet/structure-of-a-spreadsheetml-document)
- **Límites** `DATASET_MAX_BYTES` de 750.000 por defecto y hasta 900.000, dentro del límite HTTP existente. `DATASET_MAX_ROWS` de 10.000 por defecto y hasta 50.000. Máximo 64 columnas, 512 caracteres por celda de origen, 128 partes ZIP y 8 MiB descomprimidos. Los límites son por archivo. No hay cuotas acumuladas por cuenta en este sprint.

Las cantidades, precios e ingresos usan enteros escalados por 10.000 en SQL y se devuelven como cadenas decimales para preservar precisión. No se guardan columnas sin mapear. El contrato, la moneda de origen, la versión y los registros ordenados permiten consumir un dataset en futuros modelos privados. Un consumidor deberá comprobar la organización y fijar versión y ventana temporal. Ninguna ruta del motor público consume estos datos.

## API

Base `/api/me/organizations/<slug>/datasets`. Requiere sesión. Las mutaciones requieren CSRF. Las respuestas privadas se envían con `Cache-Control: no-store`.

| Método | Sufijo | Contrato |
|---|---|---|
| GET, POST | vacío | Lista paginada `after` y `limit`, o crea con `name`. |
| GET | `/<id>` | Resumen, métricas y serie temporal. Admite `version` explícita. |
| PATCH, DELETE | `/<id>` | Renombra con `name` o archiva. |
| GET | `/<id>/versions` | Historial descendente con `before` y `limit`, procedencia, mapeo y calidad. |
| GET | `/<id>/rows` | Filas paginadas con `version`, `after` y `limit`. |
| POST | `/<id>/preview` | Multipart con `file` y `mapping` JSON opcional. Sin mapeo devuelve encabezados y muestra. Con mapeo devuelve calidad y token si todas las filas son válidas. |
| POST | `/<id>/imports` | Multipart con el mismo `file` y `preview_token`. Devuelve versión y estado. |

La vista previa dura 15 minutos. La firma vincula actor, organización, dataset, versión de partida, contrato, huella del archivo y mapeo. La confirmación valida de nuevo y comprueba permisos dentro de la transacción. Una repetición de la última importación con la misma huella devuelve la versión existente. Un cambio concurrente con huella distinta produce 409 y requiere nueva vista previa. La escritura de versión, esquema, procedencia, calidad y registros se revierte como unidad ante cualquier error. Los resultados inválidos se muestran sin persistir cuerpos ni filas rechazadas.

## Almacenamiento y migración

La revisión aditiva `20261120_enterprise_datasets` sigue a `20261119_organizations`. Crea datasets, versiones, esquemas, importaciones, calidad y filas. Las FK compuestas impiden vincular una versión o registro a una organización distinta. Habilita RLS sin políticas de acceso directo a clientes. El rol backend del ensayo era propietario. Antes de una futura intervención deberá verificarse el rol SQL aplicado, secuencias, RLS, respaldo y cadena Alembic. Para revertir, conservar tablas con datos y revertir código. El downgrade sólo elimina tablas vacías.

El archivo original no se conserva. Werkzeug puede usar almacenamiento temporal durante la carga y lo libera al cerrar la solicitud. PostgreSQL guarda filas relacionales y JSON acotado para mapeo y contadores. El historial retiene huella SHA-256, formato, autor, fecha, estado y versión. No hay URL de archivos, descarga pública ni log de contenido. Una futura política de retención debe considerar restauración de archivados y purga autorizada.

La lectura y validación funcionan como funciones independientes de Flask. El procesamiento es síncrono y acotado. Para lotes mayores, un futuro worker puede recibir una referencia de almacenamiento privado con vida limitada, aplicar el contrato y usar el mismo guardado transaccional. Harán falta estados de trabajo, cuotas, reintentos y mediciones de uso de memoria y contención. La escritura actual comparte el bloqueo de identidad con revocaciones de membresía. El repositorio de desarrollo en memoria muestra el portal público y las organizaciones, y devuelve 503 para datasets, que requieren SQL.
