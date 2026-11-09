# MarketPredict

Plataforma académica de análisis y predicción de activos financieros (Flask, arquitectura MVC).

## Estado de la evolución
| Fase | Estado |
|---|---|
| 0 Auditoría | Hecha (ver `docs/DIAGNOSTICO.md`) |
| 1 Estabilización | **Base hecha**: fábrica de app única, configuración por entorno, admin protegido, validación, límites, cabeceras, pruebas. Pendiente: BD Supabase |
| 3 Predicción | **Parcial**: Ridge, Random Forest, referencias, Monte Carlo, consenso, walk-forward (ver `docs/MODELOS.md`). XGBoost, ARIMA y SARIMA están escritos pero **sin verificar** (requieren `requirements-ml.txt`); pendiente LSTM y la evaluación automática |
| Navegación | Menú superior funcional (Productos, Comunidad, Mercados, Brókers, Más) |
| 2, 4–8 | Pendientes |

## Instalación rápida y verificación
`scripts\instalar_local.bat` (Windows) o `bash scripts/instalar_local.sh` instala todo y ejecuta `scripts/verify_install.py`.
Guías: `docs/INSTALACION.md`, `docs/BASE_DE_DATOS.md`, `docs/MODELOS.md`. Estado en vivo: `/api/health`.

## Ejecución local
```
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env     # y complete los valores (se leen del entorno)
python app.py            # http://127.0.0.1:5000
python -m unittest discover -s tests -v
```
Sin red puede usar `USE_DEMO=true`: genera datos **sintéticos** y la interfaz lo indica; no son datos de mercado.

## Variables de entorno
Ver `.env.example`. En producción (`APP_ENV=production`) `SECRET_KEY` es obligatoria y el modo debug queda desactivado.
`/admin` solo funciona si se define `ADMIN_TOKEN` (acceso provisional hasta que existan roles en base de datos).

## Despliegue (Netlify + Render + Supabase)
- **Supabase**: base de datos PostgreSQL (provisional/sustentación). La cadena va en `DATABASE_URL`.
- **Render**: ejecuta el backend Flask (`render.yaml`). El plan gratuito se suspende tras inactividad: abra la URL unos minutos antes de una presentación.
- **Netlify**: dominio público; `netlify.toml` reenvía todo el tráfico al backend como proxy. Edite `TU-BACKEND` con la URL real de Render.

## Limitaciones actuales
- El historial y la comunidad siguen en JSON local hasta migrar a Supabase.
- Los votos de la comunidad aún no controlan duplicados (requiere usuarios autenticados, Fase 4).
- No se ha podido probar la conexión real con Yahoo Finance ni con Supabase, ni XGBoost/ARIMA/SARIMA (el entorno de desarrollo no tenía red). Usa `scripts/verify_install.py` para verificarlos en tu equipo.
- La app aún no escribe en la base de datos (esquema y migraciones preparados, pendientes de verificar).
