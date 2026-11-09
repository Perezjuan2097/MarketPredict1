"""Conexión a PostgreSQL/Supabase. Totalmente opcional: sin DATABASE_URL o sin SQLAlchemy la app sigue funcionando."""
import logging
import os

log = logging.getLogger("marketpredict")
_engine = None


def database_url():
    url = os.getenv("DATABASE_URL", "").strip()
    if url.startswith("postgres://"):  # formato antiguo
        url = "postgresql://" + url[len("postgres://"):]
    return url


def get_engine():
    """Crea (una vez) el motor SQLAlchemy. Lanza RuntimeError si no está configurado."""
    global _engine
    if _engine is not None:
        return _engine
    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL no está definida.")
    try:
        from sqlalchemy import create_engine
    except ImportError as exc:
        raise RuntimeError("SQLAlchemy no está instalado.") from exc
    kwargs = {"pool_pre_ping": True, "future": True}
    if not url.startswith("sqlite"):
        kwargs.update(pool_size=3, max_overflow=2, pool_recycle=1800)
    _engine = create_engine(url, **kwargs)
    return _engine


def reset_engine():
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def check_db():
    """Estado de la base de datos sin exponer credenciales."""
    if not database_url():
        return {"status": "no_configurada", "detail": "Defina DATABASE_URL (cadena Session pooler de Supabase)."}
    try:
        from sqlalchemy import text
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "detail": "Conexión correcta."}
    except RuntimeError as exc:
        return {"status": "error", "detail": str(exc)}
    except Exception as exc:  # el mensaje original puede contener host/usuario: solo al log
        log.error("Fallo de conexión a la base de datos: %s", type(exc).__name__)
        log.debug("Detalle", exc_info=True)
        return {"status": "error", "detail": "No fue posible conectar. Revise DATABASE_URL (use la cadena Session pooler de Supabase) y los logs."}
