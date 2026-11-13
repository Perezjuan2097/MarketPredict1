"""Selección del repositorio y del proveedor de precios según el entorno."""
import logging
import os

from app.db import database_url, get_engine

log = logging.getLogger("marketpredict")


def enable_rls(engine):
    """En PostgreSQL/Supabase activa Row Level Security en nuestras tablas (idempotente). Sin políticas,
    la clave pública de Supabase no puede leer ni escribir; el backend (rol postgres) sigue funcionando."""
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import text
    from app.models.orm import Base
    try:
        with engine.begin() as conn:
            for name in Base.metadata.tables:  # nombres propios, no entrada de usuario
                conn.execute(text(f'ALTER TABLE public."{name}" ENABLE ROW LEVEL SECURITY'))
    except Exception as exc:
        log.error("No se pudo activar RLS: %s", type(exc).__name__)


def get_repo(app):
    if "repo" in app.extensions:
        return app.extensions["repo"]
    repo = app.config.get("REPOSITORY")
    if repo is None and database_url():
        try:
            from app.models.orm import Base
            from app.repositories.sql import SQLRepository
            engine = get_engine()
            if app.config.get("AUTO_CREATE_TABLES"):
                Base.metadata.create_all(engine)  # solo crea tablas que faltan; nunca borra ni altera datos
                enable_rls(engine)
            repo = SQLRepository(engine)
        except Exception as exc:
            log.error("No se pudo inicializar el repositorio SQL: %s", type(exc).__name__)
            repo = None
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
