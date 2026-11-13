"""Inicio rápido SIN migraciones: crea todas las tablas en DATABASE_URL. Para entornos de prueba.
Para trabajo continuo use Alembic (docs/BASE_DE_DATOS.md)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.db import database_url, get_engine  # noqa: E402
from app.models.orm import Base  # noqa: E402

if not database_url():
    sys.exit("Defina DATABASE_URL antes de ejecutar este script.")
engine = get_engine()
Base.metadata.create_all(engine)  # solo crea las que faltan; nunca borra ni modifica tablas existentes
from app.repositories.factory import enable_rls  # noqa: E402
enable_rls(engine)  # solo actúa en PostgreSQL
print(f"Tablas disponibles: {', '.join(sorted(Base.metadata.tables))}")
