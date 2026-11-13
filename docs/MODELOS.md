# Motor de predicción (`ml_engine/`)

## Qué predice
Retorno logarítmico a *h* sesiones bursátiles: `log(cierre[t+h] / cierre[t])`. Las variables usan solo datos hasta *t*.

## Modelos
| Modelo | Estado | Notas |
|---|---|---|
| Regresión lineal (Ridge) | Implementado | Referencia con indicadores técnicos. |
| Random Forest | Implementado | Poco profundo y regularizado; reporta importancia de variables. |
| Referencia: último precio / retorno medio | Implementado | Un modelo solo "aporta" si las supera fuera de muestra. |
| Monte Carlo (bootstrap) | Implementado | Escenarios y percentiles; calibración fuera de muestra. Supuestos documentados en el código. |
| XGBoost | Escrito, **sin verificar** | Se activa solo si `xgboost` está instalado; si falla, el motor lo marca como error y sigue. |
| ARIMA(1,0,1), SARIMA(1,0,1)(1,0,1,5) | Escritos, **sin verificar** | Requieren `statsmodels` y `ENABLE_TS_MODELS=true`. Modelan retornos diarios; sin fuga de datos (prueba incluida). |
| LSTM, Prophet | **Pendiente** | Requieren TensorFlow/PyTorch o prophet; solo se añadirán si superan a modelos más simples. |

## Módulos de decisión (v3)
| Módulo | Qué responde | Referencia que debe superar |
|---|---|---|
| Probabilidad de subida (`ml_engine/probability.py`) | ¿Qué probabilidad hay de que el precio esté más alto en *h* sesiones? | Frecuencia histórica de subidas (Brier score) |
| Riesgo (`ml_engine/risk.py`) | ¿Cuánto se puede mover? Rango del 90 % | "La volatilidad será la del último mes" |
| Contexto (`features/builder.py`) | VIX, S&P 500, tasa a 10 años y dólar como variables | — (entran a todos los modelos) |
| Pulso de noticias (`app/services/news_pulse.py`) | Tono de los titulares recientes | **No validado** (no hay histórico de titulares): se muestra aparte |
| Explicación (`ml_engine/explain.py`) | Resumen en lenguaje sencillo + glosario | Nunca afirma certeza |

**Significancia.** Un modelo (de precio o de probabilidad) solo se usa si su mejora sobre la referencia supera la
prueba de Diebold-Mariano con varianza Newey-West (t > 1,96). En 12 paseos aleatorios simulados (ruido puro) el sistema
declaró ventaja 0 veces; en datos con señal la detecta. Así se evita "encontrar" patrones que son suerte.

**Sin volumen.** Índices y divisas no publican volumen: el volumen relativo queda neutro (1,0) en vez de anular el activo.

**Resultados.** La pestaña *Resultados* ejecuta el motor completo sobre 11 activos (acciones, ETF, IBEX, EUR/USD,
oro, BTC, ETH) y resume dónde hay ventaja y dónde no.

## Validación
Walk-forward expansivo, cronológico, con purga del horizonte (el entrenamiento solo incluye filas cuyo objetivo ya era conocido en la fecha de predicción). Métricas: MAE, MSE, RMSE, MAPE (excluye denominadores ≈ 0), acierto direccional, Sharpe y máximo drawdown de una estrategia simulada con operaciones no solapadas y sin costes.

## Consenso
Peso ∝ mejora (skill) fuera de muestra sobre la mejor referencia, recortada en 0. Si ningún modelo supera a las referencias, se declara "sin valor predictivo demostrado" y no hay estimación ponderada. El intervalo (~90 %) usa ±1,645 × RMSE fuera de muestra (supone errores aproximadamente normales).

## Limitaciones
- La mejora sobre las referencias es un indicio, no una prueba de significancia estadística.
- La fecha objetivo usa días hábiles sin calendario de festivos.
- Las predicciones aún no se almacenan para evaluarse después contra el resultado real (requiere la base de datos, siguiente paso).
- Fibonacci es una herramienta interpretativa y no entra en los modelos.
