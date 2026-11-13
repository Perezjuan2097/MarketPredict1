import logging
import sys

from flask import Flask

from app.settings import Settings


def create_app(overrides=None):
    """Fábrica única de la aplicación (sustituye a la antigua duplicada en app.py)."""
    settings = Settings(overrides)
    app = Flask(__name__, template_folder="views", static_folder="static")
    app.config.from_mapping(settings.as_flask_config())
    app.config["JSON_SORT_KEYS"] = False

    logging.basicConfig(
        stream=sys.stdout, level=settings.LOG_LEVEL,
        format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )

    from app.security import register_security
    register_security(app)

    from app.controllers.main_controller import main_bp
    from app.controllers.admin_controller import admin_bp
    from app.controllers.account_controller import account_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(account_bp)
    app.extensions["runtime_config"] = {}
    return app
