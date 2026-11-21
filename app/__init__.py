import sys

from flask import Flask

from app.settings import Settings


def create_app(overrides=None):
    """Fábrica única de la aplicación (sustituye a la antigua duplicada en app.py)."""
    settings = Settings(overrides)
    app = Flask(__name__, template_folder="views", static_folder="static")
    app.config.from_mapping(settings.as_flask_config())
    app.config["JSON_SORT_KEYS"] = False

    from app.observability import observe_requests, setup_logging
    setup_logging(settings.LOG_LEVEL, sys.stdout)
    observe_requests(app)

    from app.security import register_security
    register_security(app)

    from app.controllers.main_controller import main_bp
    from app.controllers.admin_controller import admin_bp
    from app.controllers.account_controller import account_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(account_bp)
    from app.controllers.organization_controller import organization_bp
    app.register_blueprint(organization_bp)
    from app.controllers.dataset_controller import dataset_bp
    app.register_blueprint(dataset_bp)
    from app.controllers.enterprise_forecast_controller import forecast_bp
    app.register_blueprint(forecast_bp)
    from app.controllers.automation_controller import automation_bp
    app.register_blueprint(automation_bp)
    from app.controllers.commercial_controller import commercial_bp
    app.register_blueprint(commercial_bp)
    from app.controllers.broker_controller import broker_bp
    app.register_blueprint(broker_bp)
    app.extensions["runtime_config"] = {}
    # Solo con ENTERPRISE_EMBEDDED_WORKER=true (despliegues de un único proceso web); ver app/embedded_worker.py.
    from app.embedded_worker import start as start_embedded_worker
    start_embedded_worker(app)
    return app
