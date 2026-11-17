"""Run with python -m scripts.enterprise_worker. The Flask process never starts this worker."""
import argparse
import logging
import multiprocessing
import os
import signal
from threading import Event
from time import monotonic
from uuid import uuid4


log = logging.getLogger("enterprise_worker")


def _compute(job, pipe, cpu_seconds, memory_mib):
    try:
        if os.name == "posix":
            import resource
            memory = memory_mib * 1024**2
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        from sqlalchemy.exc import OperationalError
        from app.db import get_engine
        from app.repositories.organization_policy import OrganizationAccessDenied, OrganizationNotFound
        from app.repositories.sql import SQLRepository
        from app.security import InvalidInput
        from app.services.automation_tasks import compute

        try:
            output = compute(SQLRepository(get_engine()), job)
            pipe.send(("ok", output))
        except (OrganizationAccessDenied, OrganizationNotFound):
            pipe.send(("permission_revoked", False))
        except InvalidInput:
            pipe.send(("invalid_input", False))
        except OperationalError:
            pipe.send(("database_unavailable", True))
        except MemoryError:
            pipe.send(("resource_limit", False))
        except Exception:
            pipe.send(("computation_failed", False))
    except MemoryError:
        pipe.send(("resource_limit", False))
    finally:
        pipe.close()


def execute(repo, job, stop, *, worker_id, timeout=120, cpu_seconds=90, memory_mib=768):
    ctx = multiprocessing.get_context("spawn")
    receive, send = ctx.Pipe(duplex=False)
    child = ctx.Process(target=_compute, args=(job, send, cpu_seconds, memory_mib), daemon=True)
    identifier, token = job["id"], job["lease_token"]
    outcome = "interrupted"
    start = monotonic()
    try:
        child.start()
        send.close()
        start = heartbeat = monotonic()
        while True:
            now = monotonic()
            if stop.is_set():
                repo.fail_job(identifier, token, "worker_shutdown", True)
                break
            if now - start >= timeout:
                outcome = "timeout"
                repo.fail_job(identifier, token, "timeout", True)
                break
            if now - heartbeat >= 5:
                if not repo.renew_job(identifier, token):
                    outcome = "lease_lost"
                    break
                repo.heartbeat(worker_id, "running")
                heartbeat = now
            if receive.poll(0.5):
                try:
                    status, data = receive.recv()
                except (EOFError, OSError):
                    outcome = "worker_child_exited"
                    repo.fail_job(identifier, token, "worker_child_exited", True)
                else:
                    if status == "ok":
                        outcome = "completed" if repo.complete_job(identifier, token, data) else "discarded"
                    else:
                        outcome = status
                        repo.fail_job(identifier, token, status, data)
                break
    finally:
        receive.close()
        send.close()
        if child.pid is not None:
            if child.is_alive():
                child.terminate()
            child.join(timeout=2)
            if child.is_alive():
                child.kill()
                child.join()
            child.close()
        log.info("enterprise_job_attempt", extra={
            "job_id": identifier, "worker_id": worker_id, "request_id": job.get("request_id"),
            "outcome": outcome, "duration_ms": round((monotonic() - start) * 1000, 2),
        })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    from app.observability import setup_logging
    setup_logging(logging.INFO)
    # The spawned child inherits these limits before importing numerical libraries.
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    from sqlalchemy.exc import SQLAlchemyError
    from app.db import get_engine
    from app.repositories.sql import SQLRepository
    from app.settings import Settings

    settings = Settings()
    concurrency = settings.ENTERPRISE_FORECAST_CONCURRENCY
    timeout = int(os.getenv("ENTERPRISE_WORKER_WALL_SECONDS", "120"))
    memory = int(os.getenv("ENTERPRISE_WORKER_MEMORY_MIB", "768"))
    cpu = int(os.getenv("ENTERPRISE_WORKER_CPU_SECONDS", "90"))
    if not 5 <= timeout <= 300 or not 1 <= cpu <= 300 or not 256 <= memory <= 4096:
        parser.error("Límites del worker fuera de rango.")
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    engine = get_engine()
    repo = SQLRepository(engine)
    worker_id = uuid4().hex
    try:
        while not stop.is_set():
            try:
                repo.heartbeat(worker_id, "idle")
                repo.schedule_due()
                job = repo.claim_job(concurrency)
                if job:
                    repo.heartbeat(worker_id, "running")
                    execute(repo, job, stop, worker_id=worker_id, timeout=timeout, cpu_seconds=cpu, memory_mib=memory)
                elif not args.once:
                    stop.wait(2)
            except (SQLAlchemyError, OSError) as exc:
                log.warning("worker_cycle_error type=%s", type(exc).__name__)
                if args.once:
                    return 1
                stop.wait(5)
            if args.once:
                break
    finally:
        try:
            repo.heartbeat(worker_id, "stopped")
        except SQLAlchemyError:
            log.warning("worker_heartbeat_unavailable", extra={"worker_id": worker_id})
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
