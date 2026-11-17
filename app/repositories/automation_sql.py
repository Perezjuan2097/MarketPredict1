"""Persistent queue, scheduling and alerts. Every worker publication is fenced."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from time import time
from uuid import uuid4

from sqlalchemy import func, select, update

from app.models.orm import (
    EnterpriseAlert as Alert, EnterpriseAlertRule as Rule, EnterpriseAutomation as Schedule,
    EnterpriseDatasetImport as Import, EnterpriseDatasetQuality as Quality, EnterpriseDatasetRow as Row,
    EnterpriseDatasetSchema as Schema, EnterpriseForecastRun as Run, EnterpriseJob as Job, Organization,
)
from app.repositories.base import ConflictError
from app.repositories.organization_policy import OrganizationAccessDenied, OrganizationNotFound, PERMISSIONS
from app.repositories.organization_sql import lock_identity_writes
from app.services.automation_tasks import next_slot

LEASE = 30
MAX_PENDING = 20
ACTIVE = ("pending", "running")


class QueueFull(Exception):
    pass


def job_view(row, detail=False):
    return {"id": row.id, "kind": row.kind, "state": row.state, "parameters": row.parameters,
            "attempts": row.attempts, "max_attempts": row.max_attempts, "error_code": row.error_code,
            "created_at": row.created_at.isoformat(), "finished_at": row.finished_at,
            "available_at": row.available_at, **({"result": row.result} if detail else {})}


def _schedule_view(row):
    return {"id": row.id, "kind": row.kind, "parameters": row.parameters, "cadence": row.cadence,
            "enabled": row.enabled, "next_at": row.next_at, "actor_id": row.actor_id}


def _rule_view(row):
    return {"id": row.id, "kind": row.kind, "comparison": row.comparison, "threshold": float(row.threshold),
            "severity": row.severity, "enabled": row.enabled}


class AutomationSQL:
    @staticmethod
    def _resource_scope(model, dataset):
        return (model.organization_id == dataset.organization_id, model.dataset_id == dataset.id)

    def _resources_row(self, session, model, dataset, identifier):
        row = session.scalar(select(model).where(*self._resource_scope(model, dataset), model.id == identifier))
        if row is None:
            raise OrganizationNotFound("Recurso no encontrado.")
        return row

    def _enqueue(self, session, dataset, version, actor, kind, params, key, now):
        fingerprint = sha256(json.dumps([dataset.id, version.id, kind, params], sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()
        existing = session.scalar(select(Job).where(Job.organization_id == dataset.organization_id,
                                                    Job.idempotency_key == key))
        if existing:
            if existing.fingerprint != fingerprint:
                raise ConflictError("La clave de idempotencia corresponde a otra solicitud.")
            return existing
        pending = session.scalar(select(func.count()).select_from(Job).where(
            Job.organization_id == dataset.organization_id, Job.state.in_(ACTIVE)))
        if pending >= MAX_PENDING:
            raise QueueFull("La organización alcanzó el límite de trabajos activos.")
        row = Job(organization_id=dataset.organization_id, dataset_id=dataset.id, version_id=version.id,
                  actor_id=actor, kind=kind, parameters=params, idempotency_key=key, fingerprint=fingerprint,
                  state="pending", attempts=0, max_attempts=3, available_at=now)
        session.add(row)
        session.flush()
        return row

    def enqueue_job(self, actor, slug, dataset_id, kind, params, key):
        with self._Session.begin() as session:
            lock_identity_writes(session)
            dataset = self._dataset_access(session, actor, slug, dataset_id, "forecast_run")
            version = self._selected_version(session, dataset, params["version"])
            row = self._enqueue(session, dataset, version, actor, kind, params, f"manual:{actor}:{key}", int(time()))
            return job_view(row)

    def get_job(self, actor, slug, dataset_id, job_id):
        with self._Session() as session:
            dataset = self._dataset_access(session, actor, slug, dataset_id, "dataset_read")
            return job_view(self._resources_row(session, Job, dataset, job_id), detail=True)

    def cancel_job(self, actor, slug, dataset_id, job_id):
        with self._Session.begin() as session:
            lock_identity_writes(session)
            dataset = self._dataset_access(session, actor, slug, dataset_id, "forecast_run")
            job = self._resources_row(session, Job, dataset, job_id)
            if job.actor_id != actor:
                self._dataset_access(session, actor, slug, dataset_id, "dataset_manage")
            if job.state not in ACTIVE:
                raise ConflictError("El trabajo ya terminó.")
            self._terminate(job, "canceled", int(time()))
            return job_view(job)

    def manage_schedule(self, actor, slug, dataset_id, *, schedule_id=None, enabled=None, config=None):
        with self._Session.begin() as session:
            lock_identity_writes(session)
            dataset = self._dataset_access(session, actor, slug, dataset_id, "dataset_manage")
            now = int(time())
            if schedule_id is not None:
                row = self._resources_row(session, Schedule, dataset, schedule_id)
                if enabled:
                    self._dataset_access(session, row.actor_id, slug, dataset_id, "dataset_manage")
                row.enabled = enabled
                if enabled:
                    row.next_at = next_slot(now, row.parameters["hour_utc"], row.parameters["weekday"])
            else:
                count = session.scalar(select(func.count()).select_from(Schedule).where(*self._resource_scope(Schedule, dataset)))
                if count >= 30:
                    raise ConflictError("Se alcanzó el límite de automatizaciones de este dataset.")
                version = self._selected_version(session, dataset, config["parameters"]["version"])
                params = config["parameters"]
                row = Schedule(organization_id=dataset.organization_id, dataset_id=dataset.id, version_id=version.id,
                               actor_id=actor, kind=config["kind"], parameters=params, cadence=config["cadence"],
                               next_at=next_slot(now, params["hour_utc"], params["weekday"]), enabled=True)
                session.add(row)
                session.flush()
            return _schedule_view(row)

    def schedule_due(self, now=None):
        now = int(time()) if now is None else now
        created = 0
        with self._Session.begin() as session:
            lock_identity_writes(session)
            full_orgs = (select(Job.organization_id).where(Job.state.in_(ACTIVE))
                         .group_by(Job.organization_id).having(func.count() >= MAX_PENDING))
            rows = session.execute(select(Schedule, Organization.slug).join(
                Organization, Organization.id == Schedule.organization_id).where(
                    Schedule.enabled.is_(True), Schedule.next_at <= now, Schedule.organization_id.not_in(full_orgs))
                .order_by(Schedule.next_at, Schedule.id).limit(30)).all()
            for row, slug in rows:
                slot = row.next_at
                row.next_at = next_slot(now, row.parameters["hour_utc"], row.parameters["weekday"])
                try:
                    dataset = self._dataset_access(session, row.actor_id, slug, row.dataset_id, "dataset_manage")
                    version = self._selected_version(session, dataset, row.parameters["version"])
                    self._enqueue(session, dataset, version, row.actor_id, row.kind,
                                  {**row.parameters, "schedule_id": row.id}, f"schedule:{row.id}:{slot}", now)
                    created += 1
                except (OrganizationNotFound, OrganizationAccessDenied):
                    row.enabled = False
                except QueueFull:
                    # Leave this slot eligible, but allow other tenants to schedule.
                    row.next_at = slot
        return created

    def _check_actor(self, session, job, slug):
        dataset = self._dataset_access(session, job.actor_id, slug, job.dataset_id, "forecast_run")
        schedule_id = job.parameters.get("schedule_id")
        if schedule_id:
            self._dataset_access(session, job.actor_id, slug, job.dataset_id, "dataset_manage")
            schedule = self._resources_row(session, Schedule, dataset, schedule_id)
            if not schedule.enabled:
                raise OrganizationAccessDenied("Automatización pausada.")
        return dataset

    def claim_job(self, concurrency, now=None):
        now = int(time()) if now is None else now
        with self._Session.begin() as session:
            lock_identity_writes(session)
            # Lock expired rows before changing tokens; heartbeat uses a conditional UPDATE.
            expired = session.scalars(select(Job).where(Job.state == "running", Job.lease_until <= now)
                                      .limit(50).with_for_update()).all()
            for job in expired:
                try:
                    self._check_actor(session, job, session.get(Organization, job.organization_id).slug)
                except (OrganizationAccessDenied, OrganizationNotFound):
                    self._terminate(job, "canceled", now, "permission_revoked")
                else:
                    self._retry(session, job, now, "lease_expired", True)
            running = select(Job.organization_id).where(Job.state == "running", Job.lease_until > now)
            if session.scalar(select(func.count()).select_from(running.subquery())) >= concurrency:
                return None
            rows = session.execute(select(Job, Organization.slug).join(
                Organization, Organization.id == Job.organization_id).where(
                    Job.state == "pending", Job.available_at <= now, Job.organization_id.not_in(running))
                .order_by(Job.available_at, Job.id).limit(30)).all()
            for job, slug in rows:
                try:
                    self._check_actor(session, job, slug)
                except (OrganizationAccessDenied, OrganizationNotFound):
                    self._terminate(job, "canceled", now, "permission_revoked")
                    continue
                job.state, job.lease_token, job.lease_until = "running", uuid4().hex, now + LEASE
                job.attempts += 1
                return {**job_view(job), "lease_token": job.lease_token, "slug": slug,
                        "actor_id": job.actor_id, "dataset_id": job.dataset_id}
        return None

    @staticmethod
    def _lease_query(job_id, token, now):
        return (Job.id == job_id, Job.state == "running", Job.lease_token == token, Job.lease_until > now)

    def renew_job(self, job_id, token, now=None):
        now = int(time()) if now is None else now
        with self._Session.begin() as session:
            return session.execute(update(Job).where(*self._lease_query(job_id, token, now))
                                   .values(lease_until=now + LEASE)).rowcount == 1

    @staticmethod
    def _terminate(job, state, now, error=None):
        job.state, job.finished_at, job.error_code = state, now, error
        job.lease_token = job.lease_until = None

    def _retry(self, session, job, now, code, retryable):
        if retryable and job.attempts < job.max_attempts:
            job.state, job.error_code = "pending", code
            job.available_at = now + 5 * 2 ** (job.attempts - 1)
            job.lease_token = job.lease_until = None
        else:
            self._terminate(job, "failed", now, code)
            self._emit_alerts(session, job, {"error_code": code}, now)

    def fail_job(self, job_id, token, code, retryable, now=None):
        now = int(time()) if now is None else now
        with self._Session.begin() as session:
            lock_identity_writes(session)
            job = session.scalar(select(Job).where(*self._lease_query(job_id, token, now)).with_for_update())
            if job:
                slug = session.get(Organization, job.organization_id).slug
                try:
                    self._check_actor(session, job, slug)
                except (OrganizationAccessDenied, OrganizationNotFound):
                    self._terminate(job, "canceled", now, "permission_revoked")
                    return
                self._retry(session, job, now, code, retryable)

    def complete_job(self, job_id, token, result, now=None):
        now = int(time()) if now is None else now
        with self._Session.begin() as session:
            lock_identity_writes(session)
            job = session.scalar(select(Job).where(*self._lease_query(job_id, token, now)).with_for_update())
            if job is None:
                return False
            slug = session.get(Organization, job.organization_id).slug
            try:
                self._check_actor(session, job, slug)
            except (OrganizationAccessDenied, OrganizationNotFound):
                self._terminate(job, "canceled", now, "permission_revoked")
                return False
            if job.kind == "forecast":
                run = Run(organization_id=job.organization_id, dataset_id=job.dataset_id, version_id=job.version_id,
                          requested_by=job.actor_id, state="ready", created_epoch=now, lease_until=now,
                          parameters=job.parameters, provenance=result["origin"], result=result["forecast"])
                session.add(run)
                session.flush()
                public_result = {"run_id": run.id, "origin": result["origin"]}
            else:
                public_result = result
            self._emit_alerts(session, job, result, now)
            job.result = public_result
            self._terminate(job, "completed", now)
            return True

    def automation_source(self, actor, slug, dataset_id, version_number):
        with self._Session() as session:
            dataset = self._dataset_access(session, actor, slug, dataset_id, "forecast_run")
            version = self._selected_version(session, dataset, version_number)
            key = (dataset.organization_id, version.id)
            imported, schema, quality = (session.get(model, key) for model in (Import, Schema, Quality))
            tail = session.scalar(select(func.max(Row.date)).where(Row.organization_id == dataset.organization_id,
                                                                    Row.version_id == version.id))
            return {"dataset": dataset.name, "version": version.version, "source_sha256": imported.source_sha256,
                    "uploaded_at": imported.uploaded_at.isoformat(), "contract": schema.contract,
                    "quality": quality.counts, "currency": dataset.currency,
                    "last_source_date": tail.isoformat() if tail else None}

    def latest_automation_forecast(self, actor, slug, dataset_id, version_number):
        with self._Session() as session:
            dataset = self._dataset_access(session, actor, slug, dataset_id, "dataset_read")
            version = self._selected_version(session, dataset, version_number)
            run = session.scalar(select(Run).where(*self._resource_scope(Run, dataset), Run.version_id == version.id,
                                                   Run.state == "ready").order_by(Run.id.desc()).limit(1))
            return self._visible_run(run, detail=True) if run else None

    def list_job_resources(self, actor, slug, dataset_id):
        with self._Session() as session:
            dataset = self._dataset_access(session, actor, slug, dataset_id, "dataset_read")

            def recent(model, *conditions, limit=30):
                return list(session.scalars(select(model).where(*self._resource_scope(model, dataset), *conditions)
                                            .order_by(model.id.desc()).limit(limit)))

            _, role = self._organization_access(session, actor, slug, "dataset_read")
            allowed = PERMISSIONS[role]
            latest = {kind: next(iter(recent(Job, Job.state == "completed", Job.kind == kind, limit=1)), None)
                      for kind in ("indicators", "evaluation", "forecast")}
            latest_views = {kind: job_view(row, detail=True) if row else None for kind, row in latest.items()}
            if latest_views["forecast"] is None:
                historical = next(iter(recent(Run, Run.state == "ready", limit=1)), None)
                if historical:
                    latest_views["forecast"] = {"result": {"run_id": historical.id, "origin": historical.provenance}}
            jobs = recent(Job)
            active = session.scalars(select(Job).where(*self._resource_scope(Job, dataset), Job.state.in_(ACTIVE))
                                     .order_by(Job.id.desc()).limit(MAX_PENDING)).all()
            merged = {row.id: row for row in [*active, *jobs]}
            return {"jobs": [{**job_view(row), "can_cancel": "forecast_run" in allowed and
                              (row.actor_id == actor or "dataset_manage" in allowed)} for row in merged.values()],
                    "schedules": [_schedule_view(row) for row in recent(Schedule)],
                    "rules": [_rule_view(row) for row in recent(Rule)],
                    "alerts": [{"id": row.id, "rule_id": row.rule_id, "evidence": row.evidence,
                                "state": row.state, "created_at": row.created_at.isoformat()}
                               for row in recent(Alert, Alert.state == "active")],
                    "reports": [job_view(row) for row in recent(Job, Job.state == "completed", Job.kind == "report")],
                    "latest": latest_views}

    def add_alert_rule(self, actor, slug, dataset_id, values):
        with self._Session.begin() as session:
            lock_identity_writes(session)
            dataset = self._dataset_access(session, actor, slug, dataset_id, "dataset_manage")
            if session.scalar(select(func.count()).select_from(Rule).where(*self._resource_scope(Rule, dataset))) >= 30:
                raise ConflictError("Se alcanzó el límite de reglas de este dataset.")
            rule = Rule(organization_id=dataset.organization_id, dataset_id=dataset.id, enabled=True, **values)
            session.add(rule)
            session.flush()
            return _rule_view(rule)

    def update_alert_resource(self, actor, slug, dataset_id, model, identifier, value):
        with self._Session.begin() as session:
            lock_identity_writes(session)
            dataset = self._dataset_access(session, actor, slug, dataset_id, "dataset_manage")
            row = self._resources_row(session, model, dataset, identifier)
            if model is Rule:
                row.enabled = value
            else:
                row.state = "resolved"

    def _emit_alerts(self, session, job, output, now):
        from app.services.enterprise_alerts import alert_signal

        rules = session.scalars(select(Rule).where(Rule.organization_id == job.organization_id,
                                                   Rule.dataset_id == job.dataset_id, Rule.enabled.is_(True)))
        for rule in rules:
            signal = alert_signal(rule.kind, job, output, now)
            if signal is None:
                continue
            value, event_key = signal
            threshold = float(rule.threshold)
            if (rule.comparison == "above" and value < threshold) or (rule.comparison == "below" and value > threshold):
                continue
            if session.scalar(select(Alert.id).where(Alert.rule_id == rule.id, Alert.event_key == event_key)):
                continue
            session.add(Alert(organization_id=job.organization_id, dataset_id=job.dataset_id, rule_id=rule.id,
                              event_key=event_key, state="active", evidence={
                                  "kind": rule.kind, "severity": rule.severity, "value": value,
                                  "threshold": threshold, "comparison": rule.comparison, "job_id": job.id,
                                  "version": job.parameters["version"],
                              }))
