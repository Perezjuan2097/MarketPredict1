# Contrato temporal de pronóstico diario

El contrato `daily-v1` conserva la etiqueta local de sesión y usa UTC para comparar
instantes. `event_at` identifica el cierre del mercado, `published_at` la publicación
del dato cuando el origen la informa, y `available_at` el instante en que el dato
estuvo accesible. `cutoff_at` identifica el corte de la ejecución. El máximo de los
instantes aplicables debe ser menor o igual al corte.

## Calendarios y datos

- Bolsas admitidas mediante metadatos explícitos de Yahoo: Nueva York, Nasdaq, Madrid,
  Londres, París, Xetra, Toronto, Hong Kong, Tokio y Australia. Los índices principales
  soportados tienen mapeo explícito. El paquete de calendario versionado proporciona
  festivos, cambios de horario y cierres abreviados. El pronóstico financiero requiere
  un mercado reconocido. Los gráficos y las demás consultas conservan sus contratos.
- Cripto y la serie demo usan sesiones diarias UTC, incluidos fines de semana. La fecha
  objetivo identifica el día de la sesión. Su cierre ocurre a las 00:00 UTC del día
  siguiente. Una misma fecha demo produce la misma barra aunque se soliciten ventanas
  diferentes.
- Yahoo identifica fechas de sesiones y no ofrece en este flujo un archivo histórico
  de instantes de publicación. Para cierres del activo, el motor usa el cierre de la
  bolsa más un margen operativo de 20 minutos y excluye barras posteriores al corte.
  Es una política operativa, no un sello de publicación del proveedor. Para las
  variables externas se exige disponibilidad explícita. Las series diarias de contexto
  descargadas por Yahoo quedan fuera del motor hasta disponer de ese dato.
- Contexto usa un esquema fijo con indicadores de disponibilidad. Los eventos
  tardíos cuyo evento ya quedó atrás se descartan y no reescriben características.
  El valor anterior puede arrastrarse si falta otro más reciente. No se fija aún una
  caducidad por serie. Un proveedor con revisiones debe conservar la publicación
  original y no reemplazar retroactivamente su valor.
- Se rechazan cierres inválidos, fechas duplicadas, orden incorrecto, huecos de
  sesión dentro de la serie y cierres demasiado desactualizados. Las barras abiertas
  no entran al pronóstico. En el marco técnico se conserva la aproximación existente
  con el cierre para OHLC ausente y volumen neutro cuando no se publica.

## Evaluación

El objetivo avanza sesiones desde el cierre de referencia, tanto en el motor como
en predicciones manuales. El campo `target_date_approx` se conserva como alias de API.
`provenance.target_close_at` identifica la hora real de cierre programada y también
se aplica el margen del proveedor antes de intentar evaluar.

`pendiente` indica una predicción todavía vigente. Tras el vencimiento, el
registro es evaluable cuando existe un único cierre exacto válido de la fuente
registrada. Si no existe, pasa a `en_evaluacion`, conserva el resultado vacío y
registra la causa en logs. Solo al existir un observado se calculan métricas y se
guarda `cumplida` o `no_cumplida`. Una evaluación concurrente no puede sobrescribir
una cancelación ni restaurar un registro ya evaluado.

Los registros antiguos conservan la fecha y el resultado que ya tenían. Los que
siguen pendientes permanecen sin evaluar cuando falta el contrato temporal, porque
el horizonte original no se puede verificar. La serie sintética anterior no se
mezcla con las evaluaciones nuevas.

## Evaluación histórica y reproducción

El walk-forward mantiene la purga por horizonte y el escalado se ajusta en cada
entrenamiento. Con historial suficiente, los regresores usan desarrollo temporal,
un tramo posterior para estimar el error de calibración y un último tramo reservado
para las métricas finales. Los pesos del consenso se fijan con los errores de
desarrollo. La clasificación publica su Brier final separado de la selección.
Riesgo separa selección, ajuste del multiplicador y cobertura final en sus
predicciones walk-forward. Monte Carlo conserva su lectura como simulación.
Cuando falta historial se indica la ausencia de evaluación final independiente.
Los intervalos de regresión todavía usan una aproximación normal y los resultados
de simulación direccional carecen de costos.

La salida y las predicciones guardadas de modelos registran la huella SHA-256 de
la serie y las características efectivas, la sesión, el corte, el calendario,
el horizonte, semilla, modelos activos, versiones y parámetros accesibles.
Se reutiliza el JSON de metadatos de SQLite y PostgreSQL. No se requiere una
migración. La reproducción requiere aportar las mismas barras y las publicaciones
originales, así como el código y las dependencias correspondientes. La huella
permite verificarlas, pero el sistema todavía no archiva las barras descargadas.
Las revisiones históricas, ajustes corporativos, interrupciones excepcionales y
variaciones numéricas entre plataformas siguen siendo límites de reproducción.
