"""Organization-scoped dataset persistence and descriptive aggregates."""
from sqlalchemy import func, insert, select

from app.models.orm import (
    EnterpriseDataset as Dataset, EnterpriseDatasetImport as Import, EnterpriseDatasetQuality as Quality,
    EnterpriseDatasetRow as Row, EnterpriseDatasetSchema as Schema, EnterpriseDatasetVersion as Version,
    OrganizationSettings,
)
from app.repositories.base import ConflictError
from app.repositories.organization_policy import OrganizationNotFound
from app.repositories.organization_sql import lock_identity_writes
from app.services.dataset_imports import CONTRACT, amount


def _dataset(row):
    return {"id": row.id, "name": row.name, "currency": row.currency, "owner_id": row.owner_id,
            "created_at": row.created_at.isoformat()}


class DatasetSQL:
    def _dataset_access(self, session, actor, slug, dataset_id, permission):
        org, _ = self._organization_access(session, actor, slug, permission)
        dataset = session.scalar(select(Dataset).where(Dataset.organization_id == org.id,
                                                       Dataset.id == dataset_id, Dataset.archived.is_(False)))
        if dataset is None:
            raise OrganizationNotFound("Dataset no encontrado.")
        return dataset

    @staticmethod
    def _versions(dataset):
        return select(Version).where(Version.organization_id == dataset.organization_id,
                                     Version.dataset_id == dataset.id)

    def list_datasets(self, actor, slug, after, limit):
        with self._Session() as session:
            org, _ = self._organization_access(session, actor, slug, "dataset_read")
            rows = session.scalars(select(Dataset).where(Dataset.organization_id == org.id,
                                   Dataset.archived.is_(False), Dataset.id > after).order_by(Dataset.id).limit(limit))
            return [_dataset(row) for row in rows]

    def create_dataset(self, actor, slug, name):
        with self._Session.begin() as session:
            lock_identity_writes(session)
            org, _ = self._organization_access(session, actor, slug, "dataset_manage")
            row = Dataset(organization_id=org.id, owner_id=actor, name=name, archived=False,
                          currency=session.get(OrganizationSettings, org.id).currency)
            session.add(row)
            session.flush()
            return _dataset(row)

    def update_dataset(self, actor, slug, dataset_id, *, name=None, archive=False):
        with self._Session.begin() as session:
            lock_identity_writes(session)
            dataset = self._dataset_access(session, actor, slug, dataset_id,
                                           "dataset_delete" if archive else "dataset_manage")
            if archive:
                dataset.archived = True
            else:
                dataset.name = name
            return _dataset(dataset)

    def dataset_import_scope(self, actor, slug, dataset_id):
        with self._Session() as session:
            dataset = self._dataset_access(session, actor, slug, dataset_id, "dataset_import")
            number = session.scalar(self._versions(dataset).with_only_columns(func.max(Version.version))) or 0
            return {"organization_id": dataset.organization_id, "dataset_id": dataset.id,
                    "actor": actor, "base_version": number}

    def save_dataset_import(self, actor, slug, dataset_id, scope, provenance, parsed):
        with self._Session.begin() as session:
            lock_identity_writes(session)
            dataset = self._dataset_access(session, actor, slug, dataset_id, "dataset_import")
            if scope["organization_id"] != dataset.organization_id:
                raise OrganizationNotFound("Dataset no encontrado.")
            last = session.scalar(self._versions(dataset).order_by(Version.version.desc()).limit(1))
            if last and session.get(Import, (dataset.organization_id, last.id)).fingerprint == provenance["fingerprint"]:
                return {"version": last.version, "status": last.status, "reused": True}
            current = last.version if last else 0
            if current != scope["base_version"]:
                raise ConflictError("El dataset cambió. Genera una nueva vista previa.")
            version = Version(organization_id=dataset.organization_id, dataset_id=dataset.id,
                              version=current + 1, status="ready")
            session.add(version)
            session.flush()
            keys = {"organization_id": dataset.organization_id, "version_id": version.id}
            session.add_all([
                Schema(**keys, contract=CONTRACT, mapping=provenance["mapping"]),
                Import(**keys, uploaded_by=actor, source_format=provenance["format"],
                       source_sha256=provenance["sha256"], fingerprint=provenance["fingerprint"]),
                Quality(**keys, counts=parsed["quality"]),
            ])
            session.execute(insert(Row), [{**keys, **row} for row in parsed["records"]])
            return {"version": version.version, "status": version.status, "reused": False}

    def dataset_history(self, actor, slug, dataset_id, before, limit):
        with self._Session() as session:
            dataset = self._dataset_access(session, actor, slug, dataset_id, "dataset_read")
            query = self._versions(dataset)
            if before is not None:
                query = query.where(Version.version < before)
            result = []
            for version in session.scalars(query.order_by(Version.version.desc()).limit(limit)):
                key = (dataset.organization_id, version.id)
                provenance, schema, quality = (session.get(model, key) for model in (Import, Schema, Quality))
                result.append({"version": version.version, "status": version.status, "format": provenance.source_format,
                               "source_sha256": provenance.source_sha256, "uploaded_by": provenance.uploaded_by,
                               "uploaded_at": provenance.uploaded_at.isoformat(), "contract": schema.contract,
                               "mapping": schema.mapping, "quality": quality.counts})
            return result

    def _selected_version(self, session, dataset, version_number):
        query = self._versions(dataset)
        if version_number is not None:
            query = query.where(Version.version == version_number)
        version = session.scalar(query.order_by(Version.version.desc()).limit(1))
        if version is None and version_number is not None:
            raise OrganizationNotFound("Versión no encontrada.")
        return version

    def dataset_rows(self, actor, slug, dataset_id, version_number, after, limit):
        with self._Session() as session:
            dataset = self._dataset_access(session, actor, slug, dataset_id, "dataset_read")
            version = self._selected_version(session, dataset, version_number)
            if version is None:
                return {"version": None, "rows": []}
            records = session.scalars(select(Row).where(Row.organization_id == dataset.organization_id,
                                      Row.version_id == version.id, Row.row_number > after)
                                      .order_by(Row.row_number).limit(limit))
            return {"version": version.version, "rows": [
                {"row_number": row.row_number, "date": row.date.isoformat(), "product": row.product,
                 "category": row.category, **{key: amount(getattr(row, key)) for key in ("quantity", "price", "revenue")}}
                for row in records
            ]}

    def dataset_summary(self, actor, slug, dataset_id, version_number):
        with self._Session() as session:
            dataset = self._dataset_access(session, actor, slug, dataset_id, "dataset_read")
            version = self._selected_version(session, dataset, version_number)
            result = {"dataset": _dataset(dataset), "version": None, "metrics": None, "series": []}
            if version is None:
                return result
            scope = (Row.organization_id == dataset.organization_id, Row.version_id == version.id)
            count, sales, units, products = session.execute(select(
                func.count(), func.sum(Row.revenue), func.sum(Row.quantity), func.count(func.distinct(Row.product))
            ).where(*scope)).one()
            days = session.execute(select(Row.date, func.sum(Row.revenue), func.sum(Row.quantity))
                                   .where(*scope).group_by(Row.date).order_by(Row.date.desc()).limit(90)).all()
            result.update(version=version.version, metrics={"records": count, "sales": amount(sales),
                                                            "units": amount(units), "products": products},
                          series=[{"date": day.isoformat(), "sales": amount(revenue), "units": amount(quantity)}
                                  for day, revenue, quantity in reversed(days)])
            return result
