"""Enterprise permissions, independent of platform administrator status."""
from app.repositories.base import ConflictError


class OrganizationNotFound(Exception):
    pass


class OrganizationAccessDenied(Exception):
    pass


ROLES = ("owner", "admin", "member", "viewer")
PERMISSIONS = {
    "owner": ("read", "members", "manage_members", "manage_privileged", "write_settings"),
    "admin": ("read", "members", "manage_members", "write_settings"),
    "member": ("read", "members"),
    "viewer": ("read",),
}


def require_permission(role, permission):
    if role is None:
        raise OrganizationNotFound("Organización no encontrada.")
    if permission not in PERMISSIONS[role]:
        raise OrganizationAccessDenied("Tu rol no permite esta operación.")


def validate_membership_change(actor_role, previous_role, new_role, *, active_owner, owner_count):
    if actor_role != "owner" and {previous_role, new_role}.intersection(("owner", "admin")):
        raise OrganizationAccessDenied("Sólo propietarios pueden administrar propietarios o administradores.")
    if active_owner and new_role != "owner" and owner_count < 2:
        raise ConflictError("Debe quedar al menos un propietario activo.")
