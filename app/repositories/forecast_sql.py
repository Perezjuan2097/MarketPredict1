"""Tenant-scoped forecast inputs, capacity leases and immutable results."""
from time import time

from sqlalchemy import case, func, or_, select, update

from app.models.orm import (
    EnterpriseDatasetImport as Import, EnterpriseDatasetRow as Row, EnterpriseDatasetSchema as Schema,
    EnterpriseForecastRun as Run,
)
from app.repositories.base import ConflictError
from app.repositories.organization_policy import OrganizationNotFound
from app.repositories.organization_sql import lock_identity_writes
from app.services.dataset_imports import CONTRACT, TRANSACTION_CONTRACT
from app.security import InvalidInput

LEASE_SECONDS = 150
ACTOR_WINDOW_SECONDS = 600
ACTOR_LIMIT = 4


class ForecastBusy(Exception):
    pass


def _run_view(run, include_result=False):
    return {"id": run.id, "state": run.state, "parameters": run.parameters, "requested_by": run.requested_by,
            "provenance": run.provenance, "created_at": run.created_at.isoformat(),
            **({"result": run.result} if include_result else {})}


class ForecastSQL:
    def forecast_input(self, user, slug, dataset_id, params, start, end):
        with self._Session() as session:
            dataset = self._dataset_access(session, user, slug, dataset_id, "forecast_run")
            version = self._selected_version(session, dataset, params["version"])
            key = (dataset.organization_id, version.id)
            schema, source = session.get(Schema, key), session.get(Import, key)
            if schema.contract not in (CONTRACT, TRANSACTION_CONTRACT):
                raise InvalidInput("Esta versión no utiliza un contrato de ventas compatible.")
            scope = [Row.organization_id == dataset.organization_id, Row.version_id == version.id]
            if params["group_by"] != "date":
                column = Row.product if params["group_by"] == "product" else Row.category
                scope.append(column == params["group"])
            target = Row.revenue if params["target"] == "revenue" else Row.quantity
            rows = session.execute(select(
                Row.date, func.sum(target), func.count(),
                func.sum(case((or_(Row.quantity < 0, Row.revenue < 0), 1), else_=0)),
            ).where(*scope, Row.date.between(start, end)).group_by(Row.date).order_by(Row.date)).all()
            tail = session.scalar(select(func.max(Row.date)).where(*scope))
            return rows, {
                "organization_id": dataset.organization_id, "dataset_id": dataset.id, "version_id": version.id,
                "version": version.version, "contract": schema.contract, "mapping": schema.mapping,
                "source_sha256": source.source_sha256, "source_uploaded_at": source.uploaded_at.isoformat(),
                "currency": dataset.currency, "last_source_date": tail.isoformat() if tail else None,
                "observations_excluded_after_cutoff": bool(tail and tail > end),
            }

    def reserve_forecast(self, user, slug, dataset_id, params, provenance, concurrency):
        with self._Session.begin() as session:
            lock_identity_writes(session)
            now = int(time())
            dataset = self._dataset_access(session, user, slug, dataset_id, "forecast_run")
            version = self._selected_version(session, dataset, params["version"])
            if (dataset.organization_id, dataset.id, version.id) != (
                    provenance["organization_id"], provenance["dataset_id"], provenance["version_id"]):
                raise OrganizationNotFound("Versión no encontrada.")
            active = (Run.state == "running", Run.lease_until > now)
            actor_count = session.scalar(select(func.count()).select_from(Run).where(
                Run.requested_by == user, Run.created_epoch >= now - ACTOR_WINDOW_SECONDS))
            if actor_count >= ACTOR_LIMIT:
                raise ForecastBusy("Límite de cuatro ejecuciones por usuario en diez minutos.")
            if session.scalar(select(func.count()).select_from(Run).where(*active)) >= concurrency or session.scalar(
                    select(Run.id).where(*active, Run.organization_id == dataset.organization_id).limit(1)) is not None:
                raise ForecastBusy("Hay una ejecución en curso. Intenta nuevamente más tarde.")
            session.execute(update(Run).where(Run.state == "running", Run.lease_until <= now).values(state="failed"))
            run = Run(organization_id=dataset.organization_id, dataset_id=dataset.id, version_id=version.id,
                      requested_by=user, parameters=params, provenance=provenance, state="running",
                      created_epoch=now, lease_until=now + LEASE_SECONDS)
            session.add(run)
            session.flush()
            return run.id

    def finish_forecast(self, user, slug, dataset_id, run_id, result):
        with self._Session.begin() as session:
            lock_identity_writes(session)
            dataset = self._dataset_access(session, user, slug, dataset_id, "forecast_run")
            run = session.scalar(select(Run).where(Run.organization_id == dataset.organization_id,
                                 Run.dataset_id == dataset.id, Run.id == run_id, Run.requested_by == user))
            if not run or run.state != "running" or run.lease_until <= int(time()):
                raise ConflictError("La ejecución ya no está activa.")
            run.result, run.state = result, "ready"
            return _run_view(run, include_result=True)

    def fail_forecast(self, user, organization_id, run_id):
        with self._Session.begin() as session:
            session.execute(update(Run).where(Run.organization_id == organization_id, Run.id == run_id,
                            Run.requested_by == user, Run.state == "running").values(state="failed"))

    def list_forecasts(self, user, slug, dataset_id, after, limit):
        with self._Session() as session:
            dataset = self._dataset_access(session, user, slug, dataset_id, "dataset_read")
            runs = session.scalars(select(Run).where(Run.organization_id == dataset.organization_id,
                                   Run.dataset_id == dataset.id, Run.id > after)
                                   .order_by(Run.id).limit(limit))
            return [self._visible_run(run) for run in runs]

    @staticmethod
    def _visible_run(run, *, detail=False):
        data = _run_view(run, include_result=detail)
        if run.state == "running" and run.lease_until <= int(time()):
            data["state"] = "failed"
        return data

    def get_forecast(self, user, slug, dataset_id, run_id):
        with self._Session() as session:
            dataset = self._dataset_access(session, user, slug, dataset_id, "dataset_read")
            run = session.scalar(select(Run).where(Run.organization_id == dataset.organization_id,
                                                  Run.dataset_id == dataset.id, Run.id == run_id))
            if not run:
                raise OrganizationNotFound("Pronóstico no encontrado.")
            return self._visible_run(run, detail=True)
