# MarketPredict Enterprise · Sprint 3 — Predicción empresarial

## Uso y alcance

En **Empresas → Datos empresariales**, elige dataset y versión. En **Predicciones empresariales** indica ingresos o unidades, nivel de agregación, inicio, último día completo y horizonte de 1–14 días. El nivel por fecha usa el total. Producto y categoría filtran un valor exacto y agregan sus filas por fecha. Una ejecución genera una serie. La interfaz muestra historia, estimación por día, bandas, comparación de modelos y un escenario opcional. El historial permite recuperar ejecuciones sin recalcular.

`owner`, `admin` y `member` pueden generar pronósticos. `viewer` puede consultar resultados existentes. Cada acceso verifica membresía actual, organización, dataset y versión. Los IDs sólo identifican recursos. Los resultados dejan de ser visibles al archivar el dataset o al perder la membresía. No hay uso del precio, las características o los modelos públicos en este flujo y ningún dato privado se usa en el motor público.

## Preparación y versiones

- `sales-daily-v1` sigue disponible. Nuevas importaciones que mapean `transaction_id` usan `sales-daily-v2`. El ID es texto obligatorio por fila cuando se mapea. Dos filas equivalentes con IDs distintos se conservan. Una fila repetida con el mismo ID y los mismos campos sigue siendo duplicada. Para líneas idénticas dentro de la misma transacción, el origen debe proveer una clave de línea distinta.
- SQL agrega los valores tipados por día. Devuelve ingresos en la moneda del dataset o cantidades netas. Se conservan devoluciones y otros valores negativos. Se registran filas con valores negativos y días atípicos según MAD. Este diagnóstico no cambia filas, pesos ni selección.
- El usuario declara el último día completo. Debe ser anterior al día UTC actual. Filas posteriores quedan fuera del cálculo, aunque existan en la versión. El sistema no puede determinar por sí solo si llegaron todas las transacciones del último día declarado.
- Un día observado, incluso si su suma es cero, consta como observación. Un día sin filas requiere declaración explícita de cero si se sabe que no hubo actividad. Los días sin información, incluyendo posibles cambios de granularidad o series semanales, impiden pronosticar la ventana elegida. No se rellenan por defecto. Una ventana tampoco puede declarar cero en un día con filas del segmento seleccionado.
- Se conserva el rango solicitado y una huella SHA-256 de las fechas y sumas diarias escaladas. Los registros originales de la versión no se modifican. El diagnóstico de atípicos usa la ventana seleccionada completa y se mantiene fuera de evaluación y selección.

## Evaluación temporal

Se requieren al menos `28 + 16 × horizonte` días completos, con máximo de 730. Dieciséis ventanas expansivas avanzan por el horizonte sin solapar resultados de prueba. Cada ajuste, incluidos escalado y rezagos, usa sólo los datos anteriores a su origen. Ridge y Random Forest predicen recursivamente los pasos futuros. Las ventanas sucesivas pueden incorporar resultados ya observados de ventanas anteriores.

Las primeras cuatro ventanas eligen por MAE. Siempre se prueban Naive y Naive estacional de 7 días. La referencia es la de menor MAE en selección. Un modelo alternativo debe reducir ese MAE más de un 5 % para ser elegido. Media móvil semanal, suavizamiento exponencial y Ridge tienen costes acotados. Random Forest exige 112 días en el **primer** prefijo y usa una semilla fija y un hilo. ARIMA(1,0,0) requiere `statsmodels`, 168 días en el primer prefijo, variación inicial y habilitación explícita con ENTERPRISE_FORECAST_ARIMA=true. Sus fallos de ajuste se registran por etapa.

Ocho ventanas intermedias calibran el semiancho del intervalo mediante un cuantil empírico de errores absolutos del modelo elegido. Las últimas cuatro aportan MAE, RMSE, WAPE y sesgo para los modelos disponibles, y cobertura para el intervalo elegido. WAPE queda sin valor si la suma de valores absolutos observados es cero. La regla nunca cambia el modelo por el resultado final. El pronóstico publicado reajusta la receta elegida con toda la ventana histórica disponible. La cobertura nominal del 80 % es histórica y se estima con pocos orígenes. No es garantía. Esta prueba retrospectiva usa la versión disponible y no certifica que el origen hubiese tenido las mismas revisiones en el pasado.

El escenario opcional multiplica cada punto proyectado por `1 + cambio_pct / 100`. Aparece separado y no tiene probabilidad ni intervalo propios.

## API y persistencia

Base `/api/me/organizations/<slug>/datasets/<id>/forecasts`.

| Método | Ruta | Contrato |
|---|---|---|
| POST | Base | Requiere sesión y CSRF. Recibe `version`, `target` (`revenue` o `quantity`), `group_by` (`date`, `product`, `category`), `group` cuando corresponde, `start`, `complete_through` y `horizon`. `zero_dates` y `scenario_pct` son opcionales. Devuelve `run`, 201. |
| GET | Base | Historial de ejecuciones del dataset, paginado con `after` y `limit`. |
| GET | `/<run_id>` | Recupera parámetros, estado, procedencia y resultado guardado. |

Una FK compuesta vincula organización, dataset y versión exacta. Se guardan el autor, estado, huellas de la fuente y de la serie, corte, mapeo de importación, transformaciones, versión del motor, parámetros, versiones de librerías, semilla y resultados. Se guarda una ventana histórica de visualización de hasta 120 puntos en el resultado. No se guardan modelos serializados.

El procesamiento es síncrono. Una reserva SQL limita a una ejecución activa por organización y, por defecto, una en toda la base. `ENTERPRISE_FORECAST_CONCURRENCY` admite 1–4. También hay un máximo de cuatro ejecuciones por usuario cada diez minutos. Se verifica autorización tanto al reservar como antes de guardar resultados. Una ejecución interrumpida se trata como fallida al vencer la reserva de 150 segundos. La reserva evita escrituras tardías y no interrumpe por sí misma un proceso que haya excedido el plazo. Para aumentar capacidad habrá que medir el coste y pasar a un worker con cancelación y límites de recursos.

## Migración y reversión

La revisión aditiva `20261121_enterprise_forecasts` añade una columna nullable de transacción, un índice único de alcance y la tabla de ejecuciones con RLS. La aplicación aplica autorización de servidor también cuando el rol SQL propietario puede omitir RLS. El downgrade sólo retira el esquema nuevo si no hay ejecuciones ni IDs de transacción guardados. Con datos, revertir el código y conservar tablas y columna. Antes del despliegue autorizado, preparar respaldo, comprobar revisión base y rol efectivo y ensayar la cadena en copia aislada.
