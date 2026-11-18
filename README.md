# MarketPredict

Plataforma académica de análisis de mercado, predicción estadística y pronóstico empresarial (Flask, arquitectura en capas).
Proyecto de Juan Carlos Pérez Ayala · Universidad Antonio Nariño · Calidad de Software.

> Los pronósticos son estimaciones estadísticas con incertidumbre. No son recomendaciones de inversión. Cuando ningún modelo supera a una referencia simple de forma significativa, la aplicación lo indica (`sin_valor_predictivo`).

## Estado (18 de noviembre de 2026)
| Componente | Estado |
|---|---|
| Interfaz académica en seis módulos (Inicio, Análisis de mercado, Predicciones, Mis predicciones, Datos empresariales, Administración SaaS) | Implementada en `feat/academic-marketpredict-demo` |
| Motor multimodelo (`ml_engine`): referencias, Ridge, Random Forest, XGBoost, ARIMA/SARIMA opcional, walk-forward con purga, Diebold-Mariano, Monte Carlo | Implementado |
| Cuentas, sesiones revocables, emisiones firmadas, evaluación por sesión exacta, portafolio simulado | Implementado |
| Extensión empresarial (organizaciones, datasets versionados, pronóstico, worker, alertas, reportes, planes) | Implementada en ramas; **no desplegada** |
| Producción (Render, plan gratuito + Supabase) | Ejecuta el commit `5da6fe0` |
| Staging aislado (`render.sprint5-staging.yaml`) | Planificado, no creado |

Cifras verificables: 9 blueprints, 77 rutas, 38 tablas, 9 migraciones, 239 pruebas Python, 20 pruebas JavaScript y un recorrido E2E de 27 casos con capturas (`docs/academic/evidencia`).

## Instalación local
```
git checkout feat/academic-marketpredict-demo
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -c constraints-py312.txt -r requirements.txt -r requirements-ml.txt
cp .env.example .env
alembic upgrade head
python app.py                                            # http://127.0.0.1:5000
python -m scripts.enterprise_worker                      # en otra terminal, para Datos empresariales
```
Sin red: `USE_DEMO=true` genera datos sintéticos marcados como DEMO.

## Pruebas
```
python -m pytest -q
node --test tests/*.cjs
python scripts/academic_e2e.py --base http://127.0.0.1:8000 --out evidencia
```
Integración continua: `baseline-ci.yml` (Linux, Windows, PostgreSQL 17), `migration-rehearsal.yml` y `academic-evidence.yml` (recorrido E2E con capturas publicado en `docs/academic/evidencia`).

## Documentación
La documentación académica completa (Documento final, ERS 2.0, manuales técnico y de usuario, UML, pruebas, calidad y riesgos, gestión, auditoría y trazabilidad) se entrega en el paquete `MarketPredict_Entrega_Academica_2026`. Documentos técnicos del repositorio: `docs/INSTALACION.md`, `docs/MODELOS.md`, `docs/BASE_DE_DATOS.md`, `docs/SEGURIDAD_1C.md`, `docs/INTEGRIDAD_TEMPORAL.md`, `docs/ENTERPRISE_SPRINT_*.md`.

## Limitaciones
- La extensión empresarial y la interfaz académica no están desplegadas en producción.
- Yahoo Finance se consulta con una biblioteca no oficial y puede fallar; la aplicación informa el error sin mostrar datos falsos.
- El plan gratuito de Render se suspende por inactividad.
- No hay auditoría de accesibilidad automatizada ni pruebas en Firefox y Safari.
