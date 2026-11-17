"""Selección del repositorio y del proveedor de precios según el entorno."""
import logging
import os

from app.db import database_url, get_engine, identity_schema_issues

log = logging.getLogger("marketpredict")


def enable_rls(engine):
    """En PostgreSQL/Supabase activa Row Level Security en nuestras tablas (idempotente). Sin políticas,
    la clave pública de Supabase no puede leer ni escribir; el backend (rol postgres) sigue funcionando.

    Falla cerrado: si no se puede activar, el error se propaga y la aplicación no usa esas tablas. Antes sólo
    se registraba y las tablas recién creadas quedaban expuestas a ``anon``/``authenticated`` en Supabase."""
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import text
    from app.models.orm import Base
    with engine.begin() as conn:
        for name in Base.metadata.tables:  # nombres propios, no entrada de usuario
            conn.execute(text(f'ALTER TABLE public."{name}" ENABLE ROW LEVEL SECURITY'))


def auto_create_allowed(app, engine):
    """En producción con PostgreSQL el esquema lo gobierna Alembic. ``create_all`` allí crea tablas sin
    registrar la revisión, que es como la base de producción quedó con tablas Enterprise en 20261117."""
    if not app.config.get("AUTO_CREATE_TABLES"):
        return False
    if engine.dialect.name == "postgresql" and app.config.get("ENV") == "production":
        log.warning("AUTO_CREATE_TABLES se ignora en producción con PostgreSQL; aplique migraciones Alembic.")
        return False
    return True


def get_repo(app):
    if "repo" in app.extensions:
        return app.extensions["repo"]
    repo = app.config.get("REPOSITORY")
    if repo is None and database_url():
        try:
            from app.models.orm import Base
            from app.repositories.sql import SQLRepository
            engine = get_engine()
            if auto_create_allowed(app, engine):
                Base.metadata.create_all(engine)  # solo crea tablas que faltan; nunca borra ni altera datos
                enable_rls(engine)
            with engine.connect() as conn:
                missing = identity_schema_issues(conn)
            if missing:
                log.warning("Migración requerida para cuentas: %s", ", ".join(missing))
                return None
            repo = SQLRepository(engine)
        except Exception as exc:
            detail = f" ({exc.name})" if isinstance(exc, ModuleNotFoundError) else ""
            log.error("No se pudo inicializar el repositorio SQL: %s%s", type(exc).__name__, detail)
            return None
    if repo is None and os.getenv("USE_MEMORY_DB", "false").lower() == "true":
        from app.repositories.memory import MemoryRepository
        repo = MemoryRepository()  # solo desarrollo: los datos se pierden al reiniciar
    if repo is not None:
        app.extensions["repo"] = repo
    return repo


def get_provider(app):
    provider = app.config.get("PRICE_PROVIDER")
    if provider is not None:
        return provider
    from app.services.price_provider import DemoPriceProvider, YahooPriceProvider
    return DemoPriceProvider() if os.getenv("USE_DEMO", "false").lower() == "true" else YahooPriceProvider()
