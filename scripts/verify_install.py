"""Verifica la instalación y los componentes opcionales. Uso:  python scripts/verify_install.py"""
import importlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OK, WARN, FAIL = "[OK]   ", "[AVISO]", "[FALLO]"
failures = 0


def line(tag, msg):
    print(f"{tag} {msg}")


def version(mod):
    m = importlib.import_module(mod)
    return getattr(m, "__version__", "?")


print(f"Python {sys.version.split()[0]}\n")
print("== Librerías del núcleo ==")
for mod, pip in [("flask", "Flask"), ("numpy", "numpy"), ("pandas", "pandas"), ("sklearn", "scikit-learn"),
                 ("yfinance", "yfinance"), ("sqlalchemy", "SQLAlchemy"), ("alembic", "alembic"), ("psycopg2", "psycopg2-binary")]:
    try:
        line(OK, f"{pip} {version(mod)}")
    except Exception as exc:
        failures += 1
        line(FAIL, f"{pip} no disponible ({exc}). Ejecute: pip install -r requirements.txt")

print("\n== Modelos opcionales ==")
try:
    import numpy as np
    import pandas as pd
    from tests.test_ml_engine import make_data
    from ml_engine.features import builder
    from ml_engine.model_registry import registry

    for mid, st in registry.optional_status().items():
        if not st["installed"]:
            line(WARN, f"{mid}: no instalado (requiere {st['requires']}). pip install -r requirements-ml.txt")
        elif mid in ("arima", "sarima") and not st["enabled"]:
            line(WARN, f"{mid}: instalado pero desactivado (defina ENABLE_TS_MODELS=true para activarlo)")
        else:
            line(OK, f"{mid}: disponible")

    df = builder.to_frame(make_data(400))
    X, y = builder.build(df, 5)
    mask = X.notna().all(axis=1) & y.notna()
    Xl, yl = X[mask].reset_index(drop=True), y[mask].reset_index(drop=True)
    if registry.lib_available("xgboost"):
        try:
            m = registry.create("xgboost").fit(Xl, yl)
            line(OK, f"XGBoost entrena y predice ({float(m.predict(Xl.tail(1))[0]):+.4f})")
        except Exception as exc:
            failures += 1
            line(FAIL, f"XGBoost instalado pero falla: {exc}")
    if registry.lib_available("statsmodels"):
        rets = np.diff(np.log(df["cierre"].to_numpy()))
        pos = pd.DataFrame({"pos": np.flatnonzero(mask.to_numpy())})
        for mid in ("arima", "sarima"):
            try:
                m = registry.create(mid, returns=rets, horizon=5).fit(pos.iloc[:250], yl.iloc[:250])
                line(OK, f"{mid.upper()} ajusta y pronostica ({float(m.predict(pos.tail(1))[0]):+.4f})")
            except Exception as exc:
                failures += 1
                line(FAIL, f"{mid.upper()} instalado pero falla: {exc}")
except Exception as exc:
    failures += 1
    line(FAIL, f"No se pudo comprobar el motor de modelos: {exc}")

print("\n== Base de datos ==")
try:
    from sqlalchemy import create_engine
    from app.models.orm import Base
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    line(OK, f"Esquema ORM válido ({len(Base.metadata.tables)} tablas, probado en SQLite en memoria)")
except Exception as exc:
    failures += 1
    line(FAIL, f"Esquema ORM: {exc}")
from app.db import check_db
st = check_db()
tag = OK if st["status"] == "ok" else WARN if st["status"] == "no_configurada" else FAIL
failures += st["status"] == "error"
line(tag, f"Supabase/PostgreSQL: {st['status']} - {st['detail']}")

print("\n== Pruebas automatizadas ==")
env = dict(os.environ, ENABLE_TS_MODELS=os.getenv("ENABLE_TS_MODELS", "true"))
res = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=ROOT, capture_output=True, text=True, env=env)
tail = [l for l in res.stderr.strip().splitlines() if l.strip()][-3:]
print("\n".join("   " + l for l in tail))
if res.returncode != 0:
    failures += 1
    line(FAIL, "Hay pruebas fallidas; copie la salida completa de: python -m unittest discover -s tests -v")
else:
    line(OK, "Todas las pruebas pasan")

print(f"\nResultado: {'TODO CORRECTO' if failures == 0 else str(failures) + ' problema(s) a revisar'}")
sys.exit(1 if failures else 0)
