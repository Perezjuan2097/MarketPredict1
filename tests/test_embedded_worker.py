"""Worker embebido: mismo ciclo de lease, renovación y códigos de error que el worker separado."""
import threading

from app import embedded_worker


class FakeRepo:
    def __init__(self, renew=True):
        self.calls, self.renew = [], renew

    def complete_job(self, identifier, token, data):
        self.calls.append(("complete", identifier, token, data))
        return True

    def fail_job(self, identifier, token, code, retry):
        self.calls.append(("fail", identifier, code, retry))

    def renew_job(self, identifier, token):
        self.calls.append(("renew", identifier))
        return self.renew

    def heartbeat(self, worker_id, state):
        self.calls.append(("heartbeat", state))


JOB = {"id": 7, "lease_token": "t", "request_id": "r"}


def test_completed_job_publishes_its_output():
    repo = FakeRepo()
    out = embedded_worker.run_job(repo, JOB, worker_id="w", compute=lambda r, j: {"rows": 3})
    assert out == "completed"
    assert repo.calls == [("complete", 7, "t", {"rows": 3})]


def test_unexpected_error_is_reported_as_failed_computation():
    def boom(repo, job):
        raise ValueError("x")
    repo = FakeRepo()
    assert embedded_worker.run_job(repo, JOB, worker_id="w", compute=boom) == "computation_failed"
    assert repo.calls == [("fail", 7, "computation_failed", False)]


def test_long_job_renews_lease_and_times_out():
    release = threading.Event()
    repo = FakeRepo()
    out = embedded_worker.run_job(repo, JOB, worker_id="w", timeout=0.35, renew_every=0.1, poll=0.05,
                                  compute=lambda r, j: release.wait(2))
    release.set()
    assert out == "timeout"
    assert ("renew", 7) in repo.calls and ("heartbeat", "running") in repo.calls
    assert repo.calls[-1] == ("fail", 7, "timeout", True)


def test_lost_lease_stops_without_publishing():
    release = threading.Event()
    repo = FakeRepo(renew=False)
    out = embedded_worker.run_job(repo, JOB, worker_id="w", timeout=5, renew_every=0.05, poll=0.05,
                                  compute=lambda r, j: release.wait(2))
    release.set()
    assert out == "lease_lost"
    assert not any(c[0] in ("complete", "fail") for c in repo.calls)


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENTERPRISE_EMBEDDED_WORKER", raising=False)
    assert embedded_worker.enabled() is False
    monkeypatch.setenv("ENTERPRISE_EMBEDDED_WORKER", "true")
    assert embedded_worker.enabled() is True


def test_worker_id_fits_the_heartbeat_column():
    from app.models.orm import EnterpriseWorkerHeartbeat
    limit = EnterpriseWorkerHeartbeat.__table__.c.id.type.length
    assert len(embedded_worker.new_worker_id()) <= limit
