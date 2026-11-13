"""Registro y autenticación. Contraseñas con hash (werkzeug, scrypt); nunca en texto plano."""
import re

from werkzeug.security import check_password_hash, generate_password_hash

from app.repositories.base import DuplicateError
from app.security import InvalidInput

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
_DUMMY_HASH = generate_password_hash("contraseña-ficticia-1")  # iguala el tiempo cuando el usuario no existe


def validate_registration(username, email, password):
    username, email, password = str(username or "").strip(), str(email or "").strip().lower(), str(password or "")
    if not USERNAME_RE.match(username):
        raise InvalidInput("El usuario debe tener 3–30 caracteres: letras, números y guion bajo.")
    if not EMAIL_RE.match(email):
        raise InvalidInput("Correo electrónico no válido.")
    if not 8 <= len(password) <= 128 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise InvalidInput("La contraseña debe tener 8–128 caracteres, con al menos una letra y un número.")
    return username, email, password


def register(repo, username, email, password):
    username, email, password = validate_registration(username, email, password)
    try:
        return repo.create_user(username, email, generate_password_hash(password))
    except DuplicateError as exc:
        raise InvalidInput("El usuario o el correo ya están en uso.") from exc


def authenticate(repo, identifier, password):
    """Devuelve el usuario o None. Mismo mensaje y tiempo aproximado para usuario inexistente o clave errónea."""
    user = repo.find_user(str(identifier or ""))
    ok = check_password_hash(user["password_hash"] if user else _DUMMY_HASH, str(password or ""))
    if not user or not ok or user.get("is_blocked"):
        return None
    repo.touch_login(user["id"])
    return user


def public_user(user):
    return {"id": user["id"], "username": user["username"], "role": user["role"], "bio": user.get("bio"),
            "created_at": user.get("created_at")}
