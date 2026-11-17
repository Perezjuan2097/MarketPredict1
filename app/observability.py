"""Structured request and worker telemetry with no payload logging."""
from datetime import datetime, timezone
import json
import logging
import re
from time import monotonic
from uuid import uuid4

from flask import g, request


class JsonLogFormatter(logging.Formatter):
    def format(self, record):
        fields = {"time": datetime.now(timezone.utc).isoformat(), "level": record.levelname,
                  "logger": record.name, "message": record.getMessage()}
        for name in ("request_id", "endpoint", "status", "duration_ms", "job_id", "outcome", "worker_id"):
            if hasattr(record, name):
                fields[name] = getattr(record, name)
        return json.dumps(fields, ensure_ascii=False)


def setup_logging(level, stream=None):
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logging.basicConfig(level=level, handlers=[handler])


def observe_requests(app):
    log = logging.getLogger("marketpredict.http")

    @app.before_request
    def identify():
        supplied = request.headers.get("X-Request-ID", "")
        g.request_id = supplied if re.fullmatch(r"[A-Za-z0-9_-]{8,64}", supplied) else uuid4().hex
        g.started = monotonic()

    @app.after_request
    def respond(response):
        response.headers["X-Request-ID"] = g.request_id
        log.info("http_request", extra={
            "request_id": g.request_id, "endpoint": request.endpoint or "unmatched",
            "status": response.status_code, "duration_ms": round((monotonic() - g.started) * 1000, 2),
        })
        return response
