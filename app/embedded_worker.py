"""Worker empresarial embebido en el proceso web, para despliegues de un solo proceso.

El diseño normal ejecuta `python -m scripts.enterprise_worker` como proceso aparte, con límites de CPU y
memoria por trabajo. Algunos planes de alojamiento (por ejemplo, el plan gratuito de Render) solo permiten
un servicio web. Con ENTERPRISE_EMBEDDED_WORKER=true el proceso web inicia un hilo que toma trabajos de la
misma cola, con los mismos leases, renovaciones y códigos de error.

Limitación conocida y documentada: el cálculo corre en un hilo del proceso web, así que no tiene el
aislamiento de memoria y CPU del worker separado; el límite de tiempo marca el trabajo como fallido pero no
puede detener el hilo. Es adecuado para demostraciones y pilotos pequeños, no para producción con carga.
"""
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from uuid import uuid4

log = logging.getLogger("embedded_worker")
_started = False
_guard = threading.Lock()


def enabled():
    return os.getenv("ENTERPRISE_EMBEDDED_WORKER", "false").strip().lower() in {"1", "true", "yes", "on"}


def _default_compute(repo, job):
    from app.services.automation_tasks import compute
    return compute(repo, job)


def _safe(compute, repo, job):
    """Traduce las excepciones a los mismos códigos que usa el worker separado."""
    from sqlalchemy.exc import OperationalError
    from app.repositories.organization_policy import OrganizationAccessDenied, OrganizationNotFound
    from app.security import InvalidInput
    try:
        return "ok", compute(repo, job)
    except (OrganizationAccessDenied, OrganizationNotFound):
        return "permission_revoked", False
    except InvalidInput:
        return "invalid_input", False
    except OperationalError:
        return "database_unavailable", True
    except MemoryError:
        return "resource_limit", False
    except Exception:  # noqa: BLE001 - cualquier otro fallo se registra como cálculo fallido
        log.exception("embedded_job_failed")
        return "computation_failed", False


def run_job(repo, job, *, worker_id, timeout=120, renew_every=5.0, poll=1.0, compute=None):
    """Ejecuta un trabajo reclamado, renovando su lease mientras calcula. Devuelve el resultado final."""
    compute = compute or _default_compute
    identifier, token = job["id"], job["lease_token"]
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="embedded-job")
    future = pool.submit(_safe, compute, repo, job)
    start = last_renew = time.monotonic()
    outcome = "interrupted"
    try:
        while True:
            try:
                status, data = future.result(timeout=poll)
            except FutureTimeout:
                now = time.monotonic()
                if now - start >= timeout:
                    repo.fail_job(identifier, token, "timeout", True)
                    outcome = "timeout"
                    break
                if now - last_renew >= renew_every:
                    if not repo.renew_job(identifier, token):
                        outcome = "lease_lost"
                        break
                    repo.heartbeat(worker_id, "running")
                    last_renew = now
                continue
            if status == "ok":
                outcome = "completed" if repo.complete_job(identifier, token, data) else "discarded"
            else:
                outcome = status
                repo.fail_job(identifier, token, status, data)
            break
    finally:
        pool.shutdown(wait=False)
        log.info("enterprise_job_attempt", extra={
            "job_id": identifier, "worker_id": worker_id, "request_id": job.get("request_id"),
            "outcome": outcome, "duration_ms": round((time.monotonic() - start) * 1000, 2), "embedded": True,
        })
    return outcome


def _loop(concurrency, timeout):
    from sqlalchemy.exc import SQLAlchemyError
    from app.db import get_engine
    from app.repositories.sql import SQLRepository

    repo = SQLRepository(get_engine())
    worker_id = "embedded-" + uuid4().hex
    log.info("embedded_worker_started", extra={"worker_id": worker_id})
    while True:
        try:
            repo.heartbeat(worker_id, "idle")
            repo.schedule_due()
            job = repo.claim_job(concurrency)
            if job:
                repo.heartbeat(worker_id, "running")
                run_job(repo, job, worker_id=worker_id, timeout=timeout)
            else:
                time.sleep(2)
        except (SQLAlchemyError, OSError) as exc:
            log.warning("embedded_worker_cycle_error type=%s", type(exc).__name__)
            time.sleep(5)
        except Exception:  # noqa: BLE001 - el hilo nunca debe morir en silencio
            log.exception("embedded_worker_unexpected_error")
            time.sleep(5)


def start(app):
    """Inicia el hilo una sola vez por proceso, solo si está activado y hay base de datos SQL."""
    global _started
    if not enabled() or app.config.get("TESTING"):
        return False
    from app.db import database_url
    if not database_url():
        log.warning("embedded_worker_skipped reason=no_database_url")
        return False
    with _guard:
        if _started:
            return False
        _started = True
    concurrency = int(os.getenv("ENTERPRISE_FORECAST_CONCURRENCY", "1"))
    timeout = int(os.getenv("ENTERPRISE_WORKER_WALL_SECONDS", "120"))
    threading.Thread(target=_loop, args=(concurrency, timeout), name="embedded-enterprise-worker", daemon=True).start()
    return True
