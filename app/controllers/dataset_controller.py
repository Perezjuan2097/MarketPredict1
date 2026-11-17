"""Private organization dataset API."""
from functools import wraps
import json
from pathlib import PurePosixPath

from flask import Blueprint, current_app, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from app.controllers.account_controller import guarded
from app.controllers.organization_controller import _page
from app.repositories.base import ConflictError
from app.repositories.organization_policy import OrganizationNotFound
from app.security import InvalidInput, json_object
from app.services.dataset_imports import contract_for, import_identity, parse_import, sign_preview, verify_preview


dataset_bp = Blueprint("datasets", __name__, url_prefix="/api/me/organizations/<slug>/datasets")


def _name():
    name = json_object().get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100 or not name.isprintable():
        raise InvalidInput("El nombre debe tener entre 1 y 100 caracteres imprimibles.")
    return name.strip()


def _positive_arg(name):
    value = request.args.get(name)
    if value is None:
        return None
    try:
        number = int(value)
    except ValueError as exc:
        raise InvalidInput("Versión no válida.") from exc
    if not 1 <= number <= 2147483647:
        raise InvalidInput("Versión no válida.")
    return number


def _upload():
    if request.mimetype != "multipart/form-data":
        raise InvalidInput("Envía el archivo como multipart/form-data.")
    files = request.files.getlist("file")
    if len(files) != 1:
        raise InvalidInput("Selecciona exactamente un archivo.")
    limit = current_app.config["DATASET_MAX_BYTES"]
    upload = files[0]
    data = upload.stream.read(limit + 1)
    if len(data) > limit:
        raise RequestEntityTooLarge()
    return data, PurePosixPath(upload.filename or "").suffix.lower()


def dataset_guarded(view):
    @guarded()
    @wraps(view)
    def wrapped(repo, user, **kwargs):
        if not hasattr(repo, "list_datasets"):
            return jsonify(error="La gestión de datasets requiere almacenamiento SQL."), 503
        return view(repo, user["id"], **kwargs)
    return wrapped


@dataset_bp.get("")
@dataset_guarded
def list_datasets(repo, actor, slug):
    return jsonify(datasets=repo.list_datasets(actor, slug, *_page()))


@dataset_bp.post("")
@dataset_guarded
def create(repo, actor, slug):
    return jsonify(dataset=repo.create_dataset(actor, slug, _name())), 201


@dataset_bp.get("/<int:dataset_id>")
@dataset_guarded
def summary(repo, actor, slug, dataset_id):
    return jsonify(repo.dataset_summary(actor, slug, dataset_id, _positive_arg("version")))


@dataset_bp.get("/<int:dataset_id>/rows")
@dataset_guarded
def rows(repo, actor, slug, dataset_id):
    return jsonify(repo.dataset_rows(actor, slug, dataset_id, _positive_arg("version"), *_page()))


@dataset_bp.patch("/<int:dataset_id>")
@dataset_guarded
def rename(repo, actor, slug, dataset_id):
    return jsonify(dataset=repo.update_dataset(actor, slug, dataset_id, name=_name()))


@dataset_bp.delete("/<int:dataset_id>")
@dataset_guarded
def archive(repo, actor, slug, dataset_id):
    repo.update_dataset(actor, slug, dataset_id, archive=True)
    return "", 204


@dataset_bp.post("/<int:dataset_id>/purge")
@dataset_guarded
def purge(repo, actor, slug, dataset_id):
    confirmed = json_object().get("confirm_id")
    if type(confirmed) is not int or confirmed != dataset_id:
        raise InvalidInput("Confirma el identificador exacto del dataset.")
    repo.purge_archived_dataset(actor, slug, dataset_id)
    return "", 204


@dataset_bp.get("/<int:dataset_id>/versions")
@dataset_guarded
def history(repo, actor, slug, dataset_id):
    _, limit = _page()
    return jsonify(versions=repo.dataset_history(actor, slug, dataset_id, _positive_arg("before"), limit))


@dataset_bp.post("/<int:dataset_id>/preview")
@dataset_guarded
def preview(repo, actor, slug, dataset_id):
    scope = repo.dataset_import_scope(actor, slug, dataset_id)
    data, extension = _upload()
    mapping = request.form.get("mapping")
    if mapping is not None:
        try:
            mapping = json.loads(mapping)
        except (ValueError, RecursionError) as exc:
            raise InvalidInput("El mapeo de columnas no es válido.") from exc
        if not isinstance(mapping, dict):
            raise InvalidInput("El mapeo de columnas no es válido.")
    parsed = parse_import(data, extension, current_app.config["DATASET_MAX_ROWS"], mapping)
    parsed.pop("records", None)
    parsed["contract"] = contract_for(mapping)
    if mapping is not None and not parsed["quality"]["invalid_rows"]:
        _, fingerprint = import_identity(data, extension, mapping)
        parsed["preview_token"] = sign_preview(current_app.secret_key, scope, mapping, fingerprint)
    return jsonify(parsed)


@dataset_bp.post("/<int:dataset_id>/imports")
@dataset_guarded
def confirm_import(repo, actor, slug, dataset_id):
    active = repo.dataset_import_scope(actor, slug, dataset_id)
    data, extension = _upload()
    preview = verify_preview(current_app.secret_key, request.form.get("preview_token"))
    scope, mapping = preview["scope"], preview["mapping"]
    if any(scope[key] != active[key] for key in ("organization_id", "dataset_id", "actor")):
        raise OrganizationNotFound("Vista previa no encontrada.")
    sha256, fingerprint = import_identity(data, extension, mapping)
    if preview["contract"] != contract_for(mapping) or preview["digest"] != fingerprint:
        raise ConflictError("El archivo cambió o la vista previa debe volver a generarse.")
    parsed = parse_import(data, extension, current_app.config["DATASET_MAX_ROWS"], mapping)
    if parsed["quality"]["invalid_rows"]:
        raise InvalidInput("La importación contiene registros no válidos.")
    provenance = {"mapping": mapping, "sha256": sha256, "fingerprint": fingerprint, "format": extension[1:]}
    result = repo.save_dataset_import(actor, slug, dataset_id, scope, provenance, parsed)
    return jsonify(imported=result), 200 if result["reused"] else 201
