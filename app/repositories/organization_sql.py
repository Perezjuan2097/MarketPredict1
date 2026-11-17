"""Organization persistence. Every scoped operation checks membership in the database."""
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.orm import Organization, OrganizationMembership, OrganizationSettings, User
from app.repositories.commercial_sql import enforce_quota
from app.repositories.base import ConflictError, DuplicateError, identity_key
from app.repositories.organization_policy import (
    OrganizationNotFound, PERMISSIONS, require_permission, validate_membership_change,
)


def lock_identity_writes(session):
    # Share the lock with platform account blocking. Acquire before reading membership or users.
    conn = session.connection()
    if conn.dialect.name == "postgresql":
        conn.exec_driver_sql("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE")
    elif conn.dialect.name == "sqlite":
        conn.exec_driver_sql("BEGIN IMMEDIATE")


def lock_tenant_writes(session, slug=None, *, organization_id=None):
    """Serialize one tenant and still exclude changes to platform-wide identity."""
    conn = session.connection()
    if conn.dialect.name != "postgresql":
        lock_identity_writes(session)
        return
    conn.exec_driver_sql("LOCK TABLE users IN ROW EXCLUSIVE MODE")
    scope = Organization.id == organization_id if organization_id is not None else Organization.slug == slug
    session.scalar(select(Organization.id).where(scope).with_for_update())


def _organization(row, role):
    return {"id": row.id, "name": row.name, "slug": row.slug, "status": row.status,
            "role": role, "permissions": list(PERMISSIONS[role])}


class OrganizationSQL:
    @staticmethod
    def _owner_count(session, organization_id):
        return session.scalar(select(func.count()).select_from(OrganizationMembership).join(User).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.role == "owner", User.is_blocked.is_(False)))

    def ensure_organization_owners_on_block(self, session, user_id):
        owned = session.scalars(select(OrganizationMembership.organization_id).where(
            OrganizationMembership.user_id == user_id, OrganizationMembership.role == "owner"))
        if any(self._owner_count(session, organization_id) < 2 for organization_id in owned):
            raise ConflictError("La cuenta es el último propietario activo de una organización.")

    @staticmethod
    def _scope(user_id):
        return (select(Organization, OrganizationMembership.role)
                .join(OrganizationMembership).join(User, User.id == OrganizationMembership.user_id)
                .where(User.id == user_id, User.is_blocked.is_(False), Organization.status == "active"))

    def _organization_access(self, session, user_id, slug, permission):
        found = session.execute(self._scope(user_id).where(Organization.slug == slug)).one_or_none()
        if found is None:
            raise OrganizationNotFound("Organización no encontrada.")
        org, role = found
        require_permission(role, permission)
        return org, role

    def create_organization(self, user_id, name, slug):
        try:
            with self._Session.begin() as session:
                lock_identity_writes(session)
                user = session.get(User, user_id)
                if user is None or user.is_blocked:
                    raise OrganizationNotFound("Cuenta no disponible.")
                if session.scalar(select(Organization.id).where(Organization.slug == slug)) is not None:
                    raise DuplicateError("El slug ya está en uso.")
                org = Organization(name=name, slug=slug, status="active")
                session.add(org)
                session.flush()
                session.add_all([OrganizationMembership(organization_id=org.id, user_id=user_id, role="owner"),
                                 OrganizationSettings(organization_id=org.id)])
                return _organization(org, "owner")
        except IntegrityError as exc:
            # Recognize only a slug collision. Surface other database faults normally.
            with self._Session() as session:
                if session.scalar(select(Organization.id).where(Organization.slug == slug)) is not None:
                    raise DuplicateError("El slug ya está en uso.") from exc
            raise

    def list_organizations(self, user_id, after, limit):
        with self._Session() as session:
            rows = session.execute(self._scope(user_id).where(Organization.id > after)
                                   .order_by(Organization.id).limit(limit))
            return [_organization(org, role) for org, role in rows]

    def get_organization(self, user_id, slug):
        with self._Session() as session:
            return _organization(*self._organization_access(session, user_id, slug, "read"))

    def list_organization_members(self, user_id, slug, after, limit):
        with self._Session() as session:
            org, _ = self._organization_access(session, user_id, slug, "members")
            return [dict(row) for row in session.execute(
                select(User.id.label("user_id"), User.username, OrganizationMembership.role,
                       User.is_blocked.label("blocked")).join(User)
                .where(OrganizationMembership.organization_id == org.id, User.id > after)
                .order_by(User.id).limit(limit)
            ).mappings()]

    def change_organization_member(self, user_id, slug, role, *, username=None, target_id=None):
        with self._Session.begin() as session:
            lock_tenant_writes(session, slug)
            org, actor_role = self._organization_access(session, user_id, slug, "manage_members")
            adding = username is not None
            if adding:
                target = session.scalar(select(User).where(User.username_key == identity_key(username),
                                                           User.is_blocked.is_(False)))
            else:
                target = session.get(User, target_id)
            member = session.get(OrganizationMembership, (org.id, target.id)) if target else None
            if not target or (not adding and not member):
                raise OrganizationNotFound("Membresía o cuenta no encontrada.")
            if adding and member:
                raise DuplicateError("La cuenta ya pertenece a la organización.")
            previous = member.role if member else None
            active_owner = previous == "owner" and not target.is_blocked
            validate_membership_change(actor_role, previous, role, active_owner=active_owner,
                                       owner_count=self._owner_count(session, org.id) if active_owner else 0)
            if role is None:
                session.delete(member)
                return None
            if target.is_blocked:
                raise ConflictError("La cuenta está bloqueada.")
            if member:
                member.role = role
            else:
                enforce_quota(session, org.id, "users")
                session.add(OrganizationMembership(organization_id=org.id, user_id=target.id, role=role))
            return {"user_id": target.id, "username": target.username, "role": role, "blocked": False}

    def organization_settings(self, user_id, slug, changes=None):
        with self._Session.begin() as session:
            if changes is not None:
                lock_tenant_writes(session, slug)
            org, _ = self._organization_access(session, user_id, slug,
                                               "read" if changes is None else "write_settings")
            settings = session.get(OrganizationSettings, org.id)
            if changes is not None:
                for key, value in changes.items():
                    setattr(settings, key, value)
            return {"timezone": settings.timezone, "currency": settings.currency}
