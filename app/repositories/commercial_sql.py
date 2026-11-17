"""Commercial limits and metadata-only operations for the global administration panel."""
from datetime import datetime, timezone
from time import time

from sqlalchemy import LargeBinary, cast, func, or_, select

from app.models.orm import (
    CommercialEvent, EnterpriseAutomation as Schedule, EnterpriseDataset as Dataset,
    EnterpriseDatasetImport as Import, EnterpriseDatasetRow as Row, EnterpriseJob as Job,
    EnterprisePlan as Plan, EnterpriseSubscription as Subscription, EnterpriseWorkerHeartbeat as Worker,
    Organization, OrganizationMembership as Membership, User,
)
from app.repositories.base import AdminAccessDenied, ConflictError
from app.repositories.organization_policy import OrganizationNotFound

QUOTAS = ("users", "datasets", "storage_bytes", "imports", "forecasts", "automations", "retention_days")
PLAN_DEFAULTS = {
    "Pilot": dict(zip(QUOTAS, (5, 3, 5_000_000, 20, 60, 5, 30))),
    "Starter": dict(zip(QUOTAS, (15, 10, 25_000_000, 100, 250, 15, 90))),
    "Professional": dict(zip(QUOTAS, (80, 50, 250_000_000, 500, 2000, 60, 180))),
    "Enterprise": dict(zip(QUOTAS, (500, 200, 2_000_000_000, 5000, 20000, 200, 365))),
}


class QuotaExceeded(ConflictError):
    pass


def _row_bytes(row):
    return 64 + sum(len((row.get(field) or "").encode("utf-8"))
                    for field in ("product", "category", "transaction_id"))


def _plan(session, oid, *, lock=False):
    subscription = session.get(Subscription, oid)
    name = subscription.plan_name if subscription else "Pilot"
    query = select(Plan).where(Plan.name == name)
    if lock:
        query = query.with_for_update(read=True)
    plan = session.scalar(query)
    return name, plan.limits if plan else PLAN_DEFAULTS[name]


def _used(session, oid, quota):
    if quota == "storage_bytes":
        def size(column):
            if session.bind.dialect.name == "postgresql":
                return func.coalesce(func.octet_length(column), 0)
            return func.coalesce(func.length(cast(column, LargeBinary)), 0)
        return int(session.scalar(select(func.coalesce(func.sum(
            64 + size(Row.product) + size(Row.category) + size(Row.transaction_id)), 0))
            .where(Row.organization_id == oid)))
    month = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    model, criteria = {
        "users": (Membership, ()),
        "datasets": (Dataset, (Dataset.archived.is_(False),)),
        "imports": (Import, (Import.uploaded_at >= month,)),
        "forecasts": (Job, (Job.kind == "forecast", Job.created_at >= month)),
        "automations": (Schedule, (Schedule.enabled.is_(True),)),
    }[quota]
    return session.scalar(select(func.count()).select_from(model).where(model.organization_id == oid, *criteria))


def enforce_quota(session, oid, quota, increment=1):
    # The caller holds the organization write lock through commit.
    _, limits = _plan(session, oid, lock=True)
    if _used(session, oid, quota) + increment > limits[quota]:
        raise QuotaExceeded(f"Cuota del plan alcanzada: {quota}.")


def enforce_import_quota(session, oid, records):
    enforce_quota(session, oid, "imports")
    enforce_quota(session, oid, "storage_bytes", sum(_row_bytes(row) for row in records))


def event(session, kind, actor=None, organization_id=None, reference=None):
    session.add(CommercialEvent(kind=kind, actor_id=actor, organization_id=organization_id, reference=reference))


def _global_admin(session, actor):
    user = session.get(User, actor)
    if user is None or user.role != "admin" or user.is_blocked:
        raise AdminAccessDenied()


class CommercialSQL:
    def commercial_overview(self, after, limit):
        # Select explicit metadata columns; do not serialize job parameters, payloads or private records.
        with self._Session() as session:
            orgs = session.execute(select(Organization.id, Organization.name, Organization.slug, Organization.status)
                                   .where(Organization.id > after).order_by(Organization.id).limit(limit)).mappings()
            result = []
            for org in orgs:
                name, limits = _plan(session, org["id"])
                usage = {key: _used(session, org["id"], key) for key in QUOTAS if key != "retention_days"}
                result.append({**org, "plan": name, "limits": limits, "usage": usage})
            jobs = session.execute(select(Job.id, Job.organization_id, Job.kind, Job.state, Job.attempts, Job.error_code,
                                          Job.created_at, Job.started_at, Job.finished_at, Job.request_id)
                                   .order_by(Job.id.desc()).limit(30)).mappings()
            now = int(time())
            job_meta = [{**row, "created_at": row["created_at"].isoformat(),
                         "duration_seconds": max(0, row["finished_at"] - row["started_at"])
                         if row["finished_at"] is not None and row["started_at"] is not None else None}
                        for row in jobs]
            pending = session.scalar(select(func.count()).select_from(Job).where(Job.state == "pending"))
            oldest = session.scalar(select(func.min(Job.available_at)).where(Job.state == "pending"))
            from app.repositories.automation_sql import MAX_PENDING
            saturated = (select(Job.organization_id).where(Job.state.in_(("pending", "running")))
                         .group_by(Job.organization_id).having(func.count() >= MAX_PENDING).subquery())
            saturated_count = session.scalar(select(func.count()).select_from(saturated))
            grouped = session.execute(select(Job.state, func.count()).group_by(Job.state)).all()
            workers = session.scalars(select(Worker).order_by(Worker.seen_at.desc()).limit(30))
            events = session.scalars(select(CommercialEvent).order_by(CommercialEvent.id.desc()).limit(30))
            plans = {row.name: row.limits for row in session.scalars(select(Plan))}
            return {
                "organizations": result, "jobs": job_meta, "plans": {**PLAN_DEFAULTS, **plans},
                "queue": {"pending": pending, "oldest_due_seconds": max(0, now - oldest) if oldest else None,
                          "states": dict(grouped), "failed": dict(grouped).get("failed", 0),
                          "saturated_organizations": saturated_count},
                "workers": [{"id": worker.id, "age_seconds": max(0, now - worker.seen_at),
                             "state": "stale" if worker.state != "stopped" and now - worker.seen_at > 20 else worker.state}
                            for worker in workers],
                "events": [{"kind": row.kind, "actor_id": row.actor_id, "organization_id": row.organization_id,
                            "reference": row.reference, "date": row.created_at.isoformat()} for row in events],
            }

    def commercial_assign(self, org_id, plan_name, actor):
        from app.repositories.organization_sql import lock_tenant_writes
        with self._Session.begin() as session:
            lock_tenant_writes(session, organization_id=org_id)
            _global_admin(session, actor)
            if not session.get(Organization, org_id):
                raise OrganizationNotFound("Organización no encontrada.")
            if session.get(Plan, plan_name) is None:
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert
                insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
                session.execute(insert(Plan).values(name=plan_name, limits=PLAN_DEFAULTS[plan_name])
                                .on_conflict_do_nothing(index_elements=["name"]))
            subscription = session.get(Subscription, org_id)
            if subscription:
                subscription.plan_name = plan_name
            else:
                session.add(Subscription(organization_id=org_id, plan_name=plan_name))
            event(session, "plan_assigned", actor, org_id, plan_name)

    def commercial_configure(self, plan_name, limits, actor):
        from app.repositories.organization_sql import lock_identity_writes
        with self._Session.begin() as session:
            lock_identity_writes(session)
            _global_admin(session, actor)
            row = session.get(Plan, plan_name)
            if row:
                row.limits = limits
            else:
                session.add(Plan(name=plan_name, limits=limits))
            event(session, "plan_configured", actor, reference=plan_name)

    def heartbeat(self, worker_id, state):
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        with self._Session.begin() as session:
            insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
            values = {"id": worker_id, "state": state, "seen_at": int(time())}
            session.execute(insert(Worker).values(**values).on_conflict_do_update(
                index_elements=["id"], set_={"state": values["state"], "seen_at": values["seen_at"]}))

    def commercial_purge_operations(self, slug, dry_run=True):
        from sqlalchemy import delete
        from app.models.orm import EnterpriseAlert
        from app.repositories.organization_sql import lock_tenant_writes

        with self._Session.begin() as session:
            lock_tenant_writes(session, slug)
            org = session.scalar(select(Organization).where(Organization.slug == slug))
            if org is None:
                raise OrganizationNotFound("Organización no encontrada.")
            _, limits = _plan(session, org.id, lock=True)
            cutoff = int(time()) - limits["retention_days"] * 86400
            month = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            criteria = (Job.organization_id == org.id, Job.state.in_(("completed", "failed", "canceled")),
                        Job.finished_at < cutoff, or_(Job.kind != "forecast", Job.created_at < month))
            alert_cutoff = datetime.fromtimestamp(cutoff, timezone.utc)
            alert_criteria = (EnterpriseAlert.organization_id == org.id, EnterpriseAlert.state == "resolved",
                              EnterpriseAlert.created_at < alert_cutoff)
            counts = {"jobs": session.scalar(select(func.count()).select_from(Job).where(*criteria)),
                      "resolved_alerts": session.scalar(select(func.count()).select_from(EnterpriseAlert)
                                                       .where(*alert_criteria))}
            if not dry_run:
                session.execute(delete(Job).where(*criteria))
                session.execute(delete(EnterpriseAlert).where(*alert_criteria))
                event(session, "operational_retention_applied", organization_id=org.id)
            return counts
