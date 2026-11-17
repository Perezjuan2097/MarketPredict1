"""Development and test implementation of the organization repository."""
from itertools import count

from app.repositories.base import ConflictError, DuplicateError, identity_key
from app.repositories.organization_policy import (
    OrganizationNotFound, PERMISSIONS, require_permission, validate_membership_change,
)


class OrganizationMemory:
    def _init_organizations(self):
        self._org_ids = count(1)
        self._orgs = {}
        self._memberships = {}
        self._org_settings = {}

    def _owner_count(self, slug):
        return sum(role == "owner" and not self.users[uid]["is_blocked"]
                   for uid, role in self._memberships[slug].items())

    def ensure_organization_owners_on_block(self, user_id):
        for slug, members in self._memberships.items():
            if members.get(user_id) == "owner" and self._owner_count(slug) < 2:
                raise ConflictError("La cuenta es el último propietario activo de una organización.")

    def _organization_access(self, user_id, slug, permission):
        org = self._orgs.get(slug)
        user = self.users.get(user_id)
        role = (self._memberships[slug].get(user_id)
                if org and org["status"] == "active" and user and not user["is_blocked"] else None)
        require_permission(role, permission)
        return {**org, "role": role, "permissions": list(PERMISSIONS[role])}

    def create_organization(self, user_id, name, slug):
        with self._lock:
            user = self.users.get(user_id)
            if not user or user["is_blocked"]:
                raise OrganizationNotFound("Cuenta no disponible.")
            if slug in self._orgs:
                raise DuplicateError("El slug ya está en uso.")
            self._orgs[slug] = {"id": next(self._org_ids), "name": name, "slug": slug, "status": "active"}
            self._memberships[slug] = {user_id: "owner"}
            self._org_settings[slug] = {"timezone": "UTC", "currency": "USD"}
            return self._organization_access(user_id, slug, "read")

    def list_organizations(self, user_id, after, limit):
        with self._lock:
            user = self.users.get(user_id)
            if not user or user["is_blocked"]:
                return []
            return [self._organization_access(user_id, slug, "read") for slug, org in self._orgs.items()
                    if org["id"] > after and org["status"] == "active" and user_id in self._memberships[slug]][:limit]

    def get_organization(self, user_id, slug):
        with self._lock:
            return self._organization_access(user_id, slug, "read")

    def _member_view(self, user_id, role):
        user = self.users[user_id]
        return {"user_id": user_id, "username": user["username"], "role": role, "blocked": user["is_blocked"]}

    def list_organization_members(self, user_id, slug, after, limit):
        with self._lock:
            self._organization_access(user_id, slug, "members")
            members = self._memberships[slug]
            return [self._member_view(uid, members[uid]) for uid in sorted(members) if uid > after][:limit]

    def change_organization_member(self, user_id, slug, role, *, username=None, target_id=None):
        with self._lock:
            org = self._organization_access(user_id, slug, "manage_members")
            members = self._memberships[slug]
            adding = username is not None
            if adding:
                target_id = next((uid for uid, user in self.users.items()
                                  if not user["is_blocked"] and identity_key(user["username"]) == identity_key(username)), None)
            target = self.users.get(target_id)
            if not target or (not adding and target_id not in members):
                raise OrganizationNotFound("Membresía o cuenta no encontrada.")
            previous = members.get(target_id)
            if adding and previous:
                raise DuplicateError("La cuenta ya pertenece a la organización.")
            active_owner = previous == "owner" and not target["is_blocked"]
            validate_membership_change(org["role"], previous, role, active_owner=active_owner,
                                       owner_count=self._owner_count(slug) if active_owner else 0)
            if role is None:
                del members[target_id]
                return None
            if target["is_blocked"]:
                raise ConflictError("La cuenta está bloqueada.")
            members[target_id] = role
            return self._member_view(target_id, role)

    def organization_settings(self, user_id, slug, changes=None):
        with self._lock:
            self._organization_access(user_id, slug, "read" if changes is None else "write_settings")
            if changes is not None:
                self._org_settings[slug].update(changes)
            return dict(self._org_settings[slug])
