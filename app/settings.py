"""Configuración centralizada. Todo proviene de variables de entorno (ver .env.example)."""
import os
import secrets
from pathlib import Path


def _bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """Se instancia al crear la app para leer el entorno en ese momento."""

    def __init__(self, overrides=None):
        env = os.getenv("APP_ENV", "development").lower()
        self.ENV = env
        self.DEBUG = _bool("FLASK_DEBUG", False) and env != "production"
        self.PROJECT_ROOT = Path(__file__).resolve().parent.parent
        secret = os.getenv("SECRET_KEY", "")
        if not secret:
            if env == "production":
                raise RuntimeError("SECRET_KEY es obligatoria en producción (APP_ENV=production).")
            # En desarrollo se genera una clave efímera: las sesiones se pierden al reiniciar.
            secret = secrets.token_hex(32)
        self.SECRET_KEY = secret
        self.SESSION_COOKIE_HTTPONLY = True
        self.SESSION_COOKIE_SAMESITE = "Lax"
        self.SESSION_COOKIE_SECURE = env == "production"
        self.MAX_CONTENT_LENGTH = 1 * 1024 * 1024  # 1 MB por petición
        self.ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")  # provisional hasta tener roles en BD (Fase 4)
        self.RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))
        self.USE_DEMO = _bool("USE_DEMO", False)
        self.LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
        for key, value in (overrides or {}).items():
            setattr(self, key, value)

    def as_flask_config(self):
        return {k: v for k, v in vars(self).items() if k.isupper()}
