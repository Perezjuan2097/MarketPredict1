"""Authenticated organization API. Slugs select resources and convey no authority."""
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, jsonify, request

from app.controllers.account_controller import guarded
from app.repositories.base import DuplicateError
from app.repositories.organization_policy import ROLES
from app.security import InvalidInput, audit, json_object

organization_bp = Blueprint("organizations", __name__, url_prefix="/api/me/organizations")
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _page():
    try:
        after = int(request.args.get("after", "0"))
        limit = int(request.args.get("limit", "50"))
    except ValueError as exc:
        raise InvalidInput("Paginación no válida.") from exc
    if not 0 <= after <= 2147483647 or not 1 <= limit <= 100:
        raise InvalidInput("Paginación no válida.")
    return after, limit


def _role(body):
    role = body.get("role")
    if role not in ROLES:
        raise InvalidInput("Rol empresarial no válido.")
    return role


@organization_bp.post("")
@guarded()
def create(repo, user):
    body = json_object()
    name, slug = body.get("name"), body.get("slug")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100 or not name.isprintable():
        raise InvalidInput("El nombre debe tener entre 1 y 100 caracteres imprimibles.")
    if not isinstance(slug, str):
        raise InvalidInput("Slug no válido.")
    slug = slug.strip().lower()
    if not 3 <= len(slug) <= 48 or not SLUG.fullmatch(slug):
        raise InvalidInput("El slug admite de 3 a 48 letras ASCII, números o guiones entre palabras.")
    try:
        org = repo.create_organization(user["id"], name.strip(), slug)
    except DuplicateError as exc:
        return jsonify(error=str(exc)), 409
    audit("organization_created", user["id"], org["id"])
    return jsonify(organization=org), 201


@organization_bp.get("")
@guarded()
def list_own(repo, user):
    return jsonify(organizations=repo.list_organizations(user["id"], *_page()))


@organization_bp.get("/<slug>")
@guarded()
def detail(repo, user, slug):
    return jsonify(organization=repo.get_organization(user["id"], slug))


@organization_bp.get("/<slug>/members")
@guarded()
def members(repo, user, slug):
    return jsonify(members=repo.list_organization_members(user["id"], slug, *_page()))


@organization_bp.post("/<slug>/members")
@guarded()
def add_member(repo, user, slug):
    body = json_object()
    username = body.get("username")
    if not isinstance(username, str) or not 3 <= len(username.strip()) <= 30 or not username.isprintable():
        raise InvalidInput("Nombre de usuario no válido.")
    try:
        member = repo.change_organization_member(user["id"], slug, _role(body), username=username)
    except DuplicateError as exc:
        return jsonify(error=str(exc)), 409
    audit("organization_member_added", user["id"], member["user_id"])
    return jsonify(member=member), 201


@organization_bp.patch("/<slug>/members/<int:target_id>")
@guarded()
def update_member(repo, user, slug, target_id):
    member = repo.change_organization_member(user["id"], slug, _role(json_object()), target_id=target_id)
    audit("organization_member_role_changed", user["id"], target_id)
    return jsonify(member=member)


@organization_bp.delete("/<slug>/members/<int:target_id>")
@guarded()
def remove_member(repo, user, slug, target_id):
    repo.change_organization_member(user["id"], slug, None, target_id=target_id)
    audit("organization_member_removed", user["id"], target_id)
    return "", 204


@organization_bp.get("/<slug>/settings")
@guarded()
def settings(repo, user, slug):
    return jsonify(settings=repo.organization_settings(user["id"], slug))


@organization_bp.patch("/<slug>/settings")
@guarded()
def update_settings(repo, user, slug):
    changes = json_object()
    if not changes or changes.keys() - {"timezone", "currency"}:
        raise InvalidInput("Sólo se admiten timezone y currency.")
    if "timezone" in changes:
        timezone = changes["timezone"]
        if not isinstance(timezone, str) or not 1 <= len(timezone) <= 64 or not timezone.isascii():
            raise InvalidInput("Zona horaria no válida.")
        try:
            ZoneInfo(timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise InvalidInput("Zona horaria no válida.") from exc
    if "currency" in changes:
        currency = changes["currency"]
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Za-z]{3}", currency):
            raise InvalidInput("La moneda debe indicar un código de tres letras.")
        changes["currency"] = currency.upper()
    data = repo.organization_settings(user["id"], slug, changes)
    audit("organization_settings_changed", user["id"])
    return jsonify(settings=data)
