# Instalación y verificación

## En tu computador (Windows)
1. Instala Python 3.12 desde python.org (marca "Add Python to PATH").
2. Descomprime el proyecto, abre una terminal en esa carpeta y ejecuta:
   ```
   scripts\instalar_local.bat
   ```
   Crea el entorno virtual, instala todo (`requirements-dev.txt`) y ejecuta `scripts/verify_install.py`.
   En Linux/macOS: `bash scripts/instalar_local.sh`.
3. Revisa el informe final. Cada línea marca **[OK]**, **[AVISO]** (opcional/pendiente) o **[FALLO]**.
   Si algo falla, copia el informe completo y compártelo.
4. Para ejecutar la app: `copy .env.example .env`, completa los valores y `python app.py`.

## En Render (despliegue por etapas, para no romper lo que ya funciona)
**Etapa A, núcleo + base de datos:** Build Command `pip install -r requirements.txt`. Tras desplegar, abre
`https://TU-APP.onrender.com/api/health` y comprueba `"status": "ok"`.

**Etapa B, modelos opcionales (XGBoost/ARIMA):** cambia el Build Command a
`pip install -r requirements.txt -r requirements-ml.txt`. Para ARIMA/SARIMA añade la variable `ENABLE_TS_MODELS=true`.
Vuelve a abrir `/api/health`: `optional_models` debe mostrar `installed: true`. Si el build falla por memoria o
versión, vuelve a la Etapa A (la app funciona igual sin estos modelos) y comparte el log.

> El plan gratuito de Render tiene 512 MB de RAM. Si ARIMA/SARIMA hacen lento el panel de modelos, deja
> `ENABLE_TS_MODELS=false`.
