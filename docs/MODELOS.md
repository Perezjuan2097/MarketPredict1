# Motor de predicción (`ml_engine/`)

## Objetivo y datos

Retorno logarítmico a *h* sesiones bursátiles o días UTC para mercados continuos: `log(cierre[t+h] / cierre[t])`. El motor usa las barras disponibles al corte. El calendario de cada activo determina festivos, horario, cierres anticipados y fecha objetivo. Los índices sin volumen usan un indicador de volumen relativo neutro.

La aplicación pasa al motor el historial del activo. VIX, S&P 500, tasa estadounidense a 10 años y dólar están preparados como entradas de biblioteca con fecha efectiva de disponibilidad. Quedan inactivos en las rutas web. Las cotizaciones que muestra el panel de mercados y el pulso de noticias son informativos. HAR usa la volatilidad del activo, sin VIX en esta integración.

## Modelos

| Componente | Uso y validación |
|---|---|
| Ridge y Random Forest | Estiman el retorno con indicadores técnicos del activo. Se contrastan con el último cierre y el retorno medio histórico. |
| XGBoost | Opcional, ejercitado en CI completo. Se activa cuando está instalado. |
| ARIMA y SARIMA | Opcionales, ejercitados en CI completo. Requieren `statsmodels` y `ENABLE_TS_MODELS=true`. Usan retornos diarios. |
| Monte Carlo | Escenarios mediante bootstrap de retornos y percentiles de las trayectorias. Se informa cobertura histórica. |
| Probabilidad de subida | Regresión logística y bosque de clasificación. Se comparan por Brier y un filtro Diebold–Mariano con la frecuencia de subidas del entrenamiento. Los tramos de probabilidad muestran frecuencias observadas como diagnóstico. No hay una etapa de recalibración. |
| Riesgo | HAR o la volatilidad del último mes. HAR se elige cuando reduce el RMSE del logaritmo de la volatilidad en más del 1 % en la muestra de selección. |
| Consenso | Agrega modelos de precio con mejora positiva sobre las referencias que pasan el filtro histórico de Diebold–Mariano. Puede abstenerse. |

La validación usa bloques walk-forward expansivos y purga por horizonte. Se informan MAE, RMSE y otros errores, además de acierto direccional. El Sharpe y drawdown provienen de una simulación sin costes. Los resultados históricos y los ensayos sintéticos no acreditan precisión ni rentabilidad prospectivas.

## Rangos de incertidumbre

- **Modelo individual.** Aproximación normal en log-retorno, centrada en la estimación y con semiancho `1,645 × RMSE` histórico fuera de muestra del propio modelo. El 90 % es nominal.
- **Consenso.** Mismo factor aplicado al promedio ponderado de los RMSE individuales. Resume el error de sus integrantes. Falta medir la cobertura del rango de la mezcla y no se puede comparar directamente con la de un modelo individual.
- **Riesgo.** Rango alrededor del cierre de referencia. Usa multiplicador empírico, con piso de 1,645, si hay al menos diez casos de calibración. Si hay menos, aplica la aproximación normal. La cobertura reportada se mide en el tramo histórico final.
- **Monte Carlo.** Los percentiles describen la distribución de las simulaciones. Su cobertura histórica depende de los retornos y del supuesto de remuestreo.

Las claves JSON históricas `level`, `calibration`, `range_90` y `interval` conservan su significado técnico y formato. El nivel nominal y las frecuencias observadas responden a preguntas distintas.

## Guardado y evaluación

Consultar un pronóstico o generar un informe no lo guarda. **Guardar predicciones** en el panel de modelos envía símbolo y horizonte y persiste en SQL el resultado que el servidor resuelve en ese momento. **Publicar predicción** registra una predicción manual en SQL. **Guardar predicción** en Seguimiento conserva el primer pronóstico del análisis en el historial JSON separado. Ese historial queda fuera de la evaluación SQL.

Abrir **Mi cuenta** o pulsar **Actualizar resultados** solicita la evaluación de pronósticos SQL vencidos. Sólo se evalúan cuando se puede verificar el cierre exacto de su sesión objetivo con la fuente registrada. No hay tarea periódica. Ver [Cuentas](CUENTAS.md).

## Comparación de estabilización 2B.1

Se ejecutó el perfil núcleo con 600 barras sintéticas, corte fijo UTC del 17 de noviembre de 2025 a las 12:00 y semilla del motor 0. Una ejecución con DemoFuente (h=5) y dos con cambios de volatilidad sintéticos (h=10 y 15, generador NumPy semilla 27) compararon el motor vigente con una copia experimental que entrenaba y predecía usando sólo las 14 variables técnicas. El join de contexto también admite datos reales en llamadas directas a la biblioteca.

| Horizonte | Ridge, precio igual en ambas variantes | Random Forest, precio igual en ambas variantes | Probabilidad, igual en ambas variantes |
|---|---:|---:|---:|
| 5 | 256,334332 | 255,855813 | 0,7348 |
| 10 | 124,594628 | 126,399334 | 0,5611 |
| 15 | 555,344031 | 554,144628 | 0,7215 |

Las ocho variables externas y cuatro indicadores de disponibilidad son cero cuando falta contexto. La variante experimental cambió la huella de procedencia en los tres casos porque la entrada al modelo forma parte de esa huella. La evidencia se limita al núcleo y a esos cortes. Se mantienen las columnas y versiones vigentes. Si en otra fase se modifica la entrada, versionar el contrato de variables y los modelos tabulares afectados, y comprobar equivalencia o diferencias en perfiles núcleo y completo.

En las mismas series se comparó la regla HAR vigente (reducción de RMSE > 1 %) con añadir Diebold–Mariano sobre la pérdida cuadrática del logaritmo de la volatilidad, contra referencia predefinida, con HAC de rezago *h* y umbral t > 1,96. Se usó la misma ventana de selección vigente. La mejora en la ventana final se muestra como diagnóstico separado.

| Horizonte | Casos de selección / finales | Mejora de RMSE en selección | t HAC | Vigente / candidata | Mejora de HAR en tramo final |
|---|---:|---:|---:|---|---:|
| 5 | 139 / 72 | 3,56 % | 0,691 | HAR / referencia | 4,36 % |
| 10 | 133 / 71 | 27,79 % | 1,643 | HAR / referencia | 29,84 % |
| 15 | 126 / 71 | 29,57 % | 1,395 | HAR / referencia | 42,23 % |

La regla candidata habría cambiado la selección en los tres casos. La prueba ilustra sensibilidad y no justifica adoptar ni descartar el filtro en mercado real. Antes de cambiar la regla, fijar universo, horizontes, tamaño de muestra y control de comparaciones, y comparar error y cobertura en un tramo final intacto además de comprobar estabilidad con bloques temporales. La selección y las fórmulas de rangos se mantienen en esta entrega.

`ModeloML` permanece como compatibilidad interna ejercitada por `tests/test_smoke.py`. Los controladores llaman a `predicciones_desde_motor`. Se retiró la clase `ClasificadorAlertas`, sin referencias de uso, y se conserva `alerta_desde_motor`. El historial JSON mantiene su interfaz y sus rutas activas.
