"""Configuración centralizada. Todo proviene de variables de entorno (ver .env.example)."""
import os
import secrets
from datetime import timedelta
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
        if env == "production" and len(secret) < 32:
            raise RuntimeError("SECRET_KEY debe tener al menos 32 caracteres en producción.")
        self.SECRET_KEY = secret
        self.SESSION_COOKIE_HTTPONLY = True
        self.SESSION_COOKIE_SAMESITE = "Lax"
        self.SESSION_COOKIE_SECURE = env == "production"
        self.MAX_CONTENT_LENGTH = 1 * 1024 * 1024  # 1 MB por petición
        self.DATASET_MAX_BYTES = int(os.getenv("DATASET_MAX_BYTES", "750000"))
        self.ENTERPRISE_FORECAST_ARIMA = _bool("ENTERPRISE_FORECAST_ARIMA")
        self.ENTERPRISE_FORECAST_CONCURRENCY = int(os.getenv("ENTERPRISE_FORECAST_CONCURRENCY", "1"))
        self.DATASET_MAX_ROWS = int(os.getenv("DATASET_MAX_ROWS", "10000"))
        self.ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")  # acceso independiente de recuperación
        self.RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))
        self.PERMANENT_SESSION_LIFETIME = timedelta(days=7)
        self.ADMIN_SESSION_LIFETIME = timedelta(hours=1)
        self.SESSION_REFRESH_EACH_REQUEST = False
        self.TRUSTED_PROXY_CIDRS = os.getenv("TRUSTED_PROXY_CIDRS", "")
        self.AUTO_CREATE_TABLES = _bool("AUTO_CREATE_TABLES", False)
        self.USE_DEMO = _bool("USE_DEMO", False)
        self.LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
        for key, value in (overrides or {}).items():
            setattr(self, key, value)

        if not 1 <= self.ENTERPRISE_FORECAST_CONCURRENCY <= 4:
            raise ValueError("El límite de pronósticos empresariales debe estar entre 1 y 4.")
        if not 1 <= self.DATASET_MAX_BYTES <= 900000 or not 1 <= self.DATASET_MAX_ROWS <= 50000:
            raise ValueError("Los límites de importación están fuera del intervalo permitido.")

    def as_flask_config(self):
        return {k: v for k, v in vars(self).items() if k.isupper()}
