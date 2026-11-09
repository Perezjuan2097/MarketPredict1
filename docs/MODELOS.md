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

## Validación
Walk-forward expansivo, cronológico, con purga del horizonte (el entrenamiento solo incluye filas cuyo objetivo ya era conocido en la fecha de predicción). Métricas: MAE, MSE, RMSE, MAPE (excluye denominadores ≈ 0), acierto direccional, Sharpe y máximo drawdown de una estrategia simulada con operaciones no solapadas y sin costes.

## Consenso
Peso ∝ mejora (skill) fuera de muestra sobre la mejor referencia, recortada en 0. Si ningún modelo supera a las referencias, se declara "sin valor predictivo demostrado" y no hay estimación ponderada. El intervalo (~90 %) usa ±1,645 × RMSE fuera de muestra (supone errores aproximadamente normales).

## Limitaciones
- La mejora sobre las referencias es un indicio, no una prueba de significancia estadística.
- La fecha objetivo usa días hábiles sin calendario de festivos.
- Las predicciones aún no se almacenan para evaluarse después contra el resultado real (requiere la base de datos, siguiente paso).
- Fibonacci es una herramienta interpretativa y no entra en los modelos.
