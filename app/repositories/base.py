"""Contrato de persistencia. Los servicios solo conocen esta interfaz (diccionarios simples):
hay una implementación en memoria (pruebas/desarrollo) y una con SQLAlchemy (Supabase/PostgreSQL).
Las dos deben pasar las mismas pruebas de contrato (tests/test_repository_contract.py)."""
from decimal import ROUND_HALF_UP, Decimal


class DuplicateError(Exception):
    """Violación de unicidad (usuario, correo, nombre de portafolio...)."""


class ConflictError(Exception):
    """Cambio concurrente o estado incompatible."""


class NotFoundError(Exception):
    pass


class AdminAccessDenied(Exception):
    pass


def has_admin_role(user):
    return bool(user and user["role"] == "admin" and not user["is_blocked"])


def validate_admin_change(actor, target, *, role, blocked, active_admins):
    if role is not None and role not in ("user", "admin"):
        raise ValueError("Rol no válido.")
    if actor and actor["id"] == target["id"] and (
            (role is not None and role != target["role"]) or blocked is True):
        raise ConflictError("No puedes cambiar tu propio rol ni bloquear tu cuenta.")
    if has_admin_role(target) and active_admins < 2 and (role == "user" or blocked is True):
        raise ConflictError("Debe quedar al menos un administrador activo.")


def identity_key(value):
    """Clave Unicode uniforme para memoria y SQL. El campo visible conserva su grafía."""
    return value.strip().lower()


def q2(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


DUE_STATUSES = ("pendiente", "en_evaluacion")

METHODS = [
    "create_user", "get_user", "find_user", "touch_login", "get_or_create_asset",
    "add_prediction", "list_predictions", "get_prediction", "set_prediction_status", "due_predictions",
    "save_evaluation", "create_portfolio", "list_portfolios", "get_portfolio", "apply_trade", "list_trades",
]
