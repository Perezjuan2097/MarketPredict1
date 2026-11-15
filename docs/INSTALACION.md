# Instalación y verificación

## Entorno de pruebas de referencia

Usa CPython 3.12.x. La referencia local es CPython 3.12.14 en Windows x64. El flujo de CI
prepara Windows y Ubuntu x64 con Python 3.12. Otras versiones de Python quedan fuera de
la matriz de esta entrega.

Crea un entorno virtual separado para cada perfil y actívalo antes de instalar:

```sh
python -m venv .venv
# Núcleo (Flask, Ridge, Random Forest, persistencia y pruebas)
python -m pip install -r requirements-test.txt
# Para el perfil completo, ejecuta este comando en otro entorno vacío
python -m pip install -r requirements-dev.txt
python -m pip check
python -m pytest -q -rs
```

En Windows activa el entorno con `.venv\Scripts\Activate.ps1` y en Linux con
`source .venv/bin/activate`. Ambos perfiles usan `constraints-py312.txt` para fijar
las versiones resueltas de las dependencias de referencia. Los requisitos de despliegue
siguen en `requirements.txt`. El perfil completo instala XGBoost CPU y statsmodels.
Los tests ejercitan ARIMA y SARIMA con su bandera activada localmente en cada caso.

Para ejecutar el contrato del repositorio y la revisión de identidad sobre PostgreSQL,
define `TEST_POSTGRES_URL` con una instancia desechable y ejecuta
`python -m pytest -q -rs tests/test_repository_contract.py tests/test_identity_migration.py tests/test_account.py`.
El usuario debe poder crear esquemas. Las pruebas crean y eliminan sus propios esquemas
aislados. Sin esa variable solo se omiten los casos específicos de PostgreSQL.

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
