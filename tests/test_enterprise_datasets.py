"""Enterprise dataset API, validation and transactional repository tests."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
import os
from threading import Barrier
from uuid import uuid4
from zipfile import ZipFile

import pytest
import sqlalchemy as sa
from sqlalchemy.schema import CreateSchema, DropSchema

from app import create_app
from app.models.orm import Base
from app.repositories.base import ConflictError
from app.repositories.sql import SQLRepository
from app.security import InvalidInput, limiter
from app.services.dataset_imports import import_identity, parse_import, sign_preview, verify_preview


BASE = "/api/me/organizations"
MAPPING = {"date": "fecha", "product": "producto", "quantity": "cantidad", "price": "precio"}
CSV = b"fecha,producto,cantidad,precio\n2026-11-01,A,2,10.25\n2026-11-02,B,3,4\n"


@pytest.fixture(params=("sqlite", "postgresql"))
def repo(request, tmp_path):
    if request.param == "sqlite":
        engine = sa.create_engine(sa.URL.create("sqlite", database=str(tmp_path / "datasets.db")),
                                  hide_parameters=True)

        @sa.event.listens_for(engine, "connect")
        def foreign_keys(conn, _record):
            conn.execute("PRAGMA foreign_keys=ON")

        try:
            Base.metadata.create_all(engine)
            yield SQLRepository(engine)
        finally:
            engine.dispose()
        return
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("requiere TEST_POSTGRES_URL")
    schema = "mp_datasets_" + uuid4().hex
    admin = sa.create_engine(url)
    engine = sa.create_engine(url, connect_args={"options": "-csearch_path=" + schema}, hide_parameters=True)
    try:
        with admin.begin() as conn:
            conn.execute(CreateSchema(schema))
        Base.metadata.create_all(engine)
        yield SQLRepository(engine)
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


@pytest.fixture
def accounts(repo, tmp_path):
    limiter.hits.clear()
    app = create_app({"TESTING": True, "REPOSITORY": repo, "PROJECT_ROOT": tmp_path, "DATASET_MAX_BYTES": 2048})

    def account(name):
        client = app.test_client()
        csrf = client.get("/api/auth/me").json["csrf"]
        response = client.post("/api/auth/register", headers={"X-CSRF-Token": csrf}, json={
            "username": name, "email": name + "@example.test", "password": "Test-password-1",
        })
        assert response.status_code == 201
        return client, {"X-CSRF-Token": response.json["csrf"]}, response.json["user"]["id"]

    return app, account


def _post_file(client, url, headers, data=CSV, filename="test.csv", **fields):
    return client.post(url, headers=headers, data={"file": (BytesIO(data), filename), **fields})


def _setup(client, headers, slug):
    assert client.post(BASE, headers=headers, json={"name": slug, "slug": slug}).status_code == 201
    response = client.post(f"{BASE}/{slug}/datasets", headers=headers, json={"name": "Ventas"})
    assert response.status_code == 201
    return f"{BASE}/{slug}/datasets/{response.json['dataset']['id']}"


def _xlsx(cell="<v>46327</v>", worksheet_prefix=""):
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    types = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '</Types>'
    )
    header = "".join(f'<c r="{col}1" t="inlineStr"><is><t>{name}</t></is></c>'
                     for col, name in zip("ABCD", MAPPING.values()))
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", types)
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="{rel}">'
                         '<sheets><sheet name="Ventas" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels",
                         f'<Relationships><Relationship Id="rId1" Type="{rel}/worksheet" '
                         'Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", worksheet_prefix + f'<worksheet xmlns="{ns}"><sheetData>'
                         f'<row r="1">{header}</row><row r="2"><c r="A2">{cell}</c>'
                         '<c r="B2" t="s"><v>0</v></c><c r="C2"><v>2</v></c><c r="D2"><v>3</v></c>'
                         '</row></sheetData></worksheet>')
        archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}"><si><t>Libro</t></si></sst>')
    return stream.getvalue()


def test_excel_and_csv_validation():
    workbook = parse_import(_xlsx(), ".xlsx", 10, MAPPING)
    assert workbook["sample"][0]["date"] == "2026-11-01"
    assert workbook["sample"][0]["product"] == "Libro"
    assert workbook["sample"][0]["revenue"] == "6"
    for contents, ext in (
        (b"", ".csv"), (b"col1,col2\n", ".csv"), (CSV, ".xlsx"), (_xlsx(), ".csv"),
        (_xlsx("<f>1+1</f><v>2</v>"), ".xlsx"),
        (_xlsx(worksheet_prefix='<!DOCTYPE worksheet [<!ENTITY x "data">]>'), ".xlsx"),
        (b"fecha,fecha\n2026-11-01,x\n", ".csv"),
        (b"fecha,producto\n2026-11-01,A,3\n", ".csv"),
    ):
        with pytest.raises(InvalidInput):
            parse_import(contents, ext, 10)
    with pytest.raises(InvalidInput):
        parse_import(CSV, ".csv", 1, MAPPING)
    with pytest.raises(InvalidInput):
        parse_import(CSV, ".csv", 10, {**MAPPING, "price": "cantidad"})
    malformed = CSV + b"2026-11-01,A,2,10.25\n30/11/2026,C,,1\n2026-11-03,D,1,NaN\n"
    quality = parse_import(malformed, ".csv", 10, MAPPING)["quality"]
    assert quality["invalid_rows"] == 3
    assert quality["duplicate_rows"] == quality["missing_cells"]["quantity"] == 1


def test_preview_token_expires(monkeypatch):
    from itsdangerous import TimestampSigner

    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: 1000)
    token = sign_preview("fixture-secret", {"actor": 1}, MAPPING, "digest")
    assert verify_preview("fixture-secret", token)["mapping"] == MAPPING
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: 1901)
    with pytest.raises(InvalidInput, match="caducó"):
        verify_preview("fixture-secret", token)


def test_two_tenants_import_real_metrics_and_preserve_history(accounts):
    app, account = accounts
    alice, akey, _ = account("alice")
    bob, bkey, _ = account("bobby")
    a = _setup(alice, akey, "alpha")
    b = _setup(bob, bkey, "bravo")
    assert app.test_client().get(a).status_code == 401
    assert bob.get(a).status_code == alice.get(b + "/versions").status_code == 404
    assert alice.get(a.replace("/alpha/", "/bravo/")).status_code == 404
    assert alice.get(a).headers["Cache-Control"] == "no-store"
    assert _post_file(alice, a + "/preview", {}).status_code == 403
    assert _post_file(alice, a + "/preview", akey, data=b"a" * 2049).status_code == 413
    preview = _post_file(alice, a + "/preview", akey, mapping=json.dumps(MAPPING)).json
    token = preview["preview_token"]
    assert alice.get(a).json["metrics"] is None
    assert _post_file(alice, a + "/imports", akey, data=CSV + b"\n", preview_token=token).status_code == 409
    assert _post_file(bob, b + "/imports", bkey, preview_token=token).status_code == 404
    assert _post_file(alice, a + "/imports", akey, preview_token="broken").status_code == 400
    assert _post_file(alice, a + "/imports", akey, preview_token=token).status_code == 201
    assert _post_file(alice, a + "/imports", akey, preview_token=token).status_code == 200
    first = alice.get(a).json
    assert first["metrics"] == {"records": 2, "sales": "32.5", "units": "5", "products": 2}
    assert first["series"][0] == {"date": "2026-11-01", "sales": "20.5", "units": "2"}
    assert bob.get(b).json["metrics"] is None
    assert bob.get(a + "/rows").status_code == 404
    rows = alice.get(a + "/rows?version=1&after=2&limit=1").json
    assert rows["version"] == 1
    assert rows["rows"] == [{"row_number": 3, "date": "2026-11-02", "product": "B", "category": None,
                             "quantity": "3", "price": "4", "revenue": "12"}]

    file = _xlsx()
    second = _post_file(bob, b + "/preview", bkey, data=file, filename="test.xlsx", mapping=json.dumps(MAPPING))
    assert second.status_code == 200, second.json
    assert _post_file(bob, b + "/imports", bkey, data=file, filename="test.xlsx",
                      preview_token=second.json["preview_token"]).status_code == 201
    assert bob.get(b).json["metrics"] == {"records": 1, "sales": "6", "units": "2", "products": 1}
    changed = CSV.replace(b",10.25", b",20")
    new = _post_file(alice, a + "/preview", akey, data=changed, mapping=json.dumps(MAPPING)).json["preview_token"]
    assert _post_file(alice, a + "/imports", akey, data=changed, preview_token=new).status_code == 201
    assert _post_file(alice, a + "/imports", akey, preview_token=token).status_code == 409
    assert alice.get(a + "?version=1").json == first
    history = alice.get(a + "/versions").json["versions"]
    assert [item["version"] for item in history] == [2, 1]
    assert history[0]["contract"] == "sales-daily-v1"
    assert history[0]["quality"]["valid_rows"] == 2
    assert alice.patch(a, headers=akey, json={"name": "Ventas revisadas"}).status_code == 200
    assert alice.delete(a, headers=akey).status_code == 204
    assert alice.get(a).status_code == 404
    assert alice.get(a + "/versions").status_code == 404


def test_revocation_invalidates_preview_and_permissions(accounts):
    _, account = accounts
    alice, akey, _ = account("alice")
    member, mkey, uid = account("member")
    path = _setup(alice, akey, "alpha")
    members = BASE + "/alpha/members"
    assert alice.post(members, headers=akey, json={"username": "member", "role": "member"}).status_code == 201
    assert member.patch(path, headers=mkey, json={"name": "No"}).status_code == 403
    assert member.delete(path, headers=mkey).status_code == 403
    invalid = _post_file(member, path + "/preview", mkey, data=CSV + CSV.split(b"\n")[1] + b"\n",
                         mapping=json.dumps(MAPPING))
    assert invalid.json["quality"]["duplicate_rows"] == 1 and "preview_token" not in invalid.json
    token = _post_file(member, path + "/preview", mkey, mapping=json.dumps(MAPPING)).json["preview_token"]
    assert alice.patch(members + "/" + str(uid), headers=akey, json={"role": "viewer"}).status_code == 200
    assert member.get(path).status_code == 200
    assert _post_file(member, path + "/imports", mkey, preview_token=token).status_code == 403
    assert alice.delete(members + "/" + str(uid), headers=akey).status_code == 204
    assert member.get(path).status_code == 404
    assert alice.get(path + "/versions").json["versions"] == []


def test_import_rolls_back_and_duplicate_concurrent_confirm_is_idempotent(repo):
    user = repo.create_user("owner", "owner@example.test", "unused")["id"]
    repo.create_organization(user, "Example", "example")
    dataset = repo.create_dataset(user, "example", "Ventas")["id"]
    scope = repo.dataset_import_scope(user, "example", dataset)
    parsed = parse_import(CSV, ".csv", 10, MAPPING)
    source, fingerprint = import_identity(CSV, ".csv", MAPPING)
    provenance = {"mapping": MAPPING, "sha256": source, "fingerprint": fingerprint, "format": "csv"}
    original = parsed["records"][1]["quantity"]
    parsed["records"][1]["quantity"] = None
    with pytest.raises(sa.exc.IntegrityError):
        repo.save_dataset_import(user, "example", dataset, scope, provenance, parsed)
    assert repo.dataset_history(user, "example", dataset, None, 50) == []
    parsed["records"][1]["quantity"] = original
    barrier = Barrier(2)

    def confirm():
        barrier.wait(timeout=5)
        return repo.save_dataset_import(user, "example", dataset, scope, provenance, parsed)

    with ThreadPoolExecutor(max_workers=2) as executor:
        a, b = executor.submit(confirm), executor.submit(confirm)
        results = [a.result(timeout=10), b.result(timeout=10)]
    assert {row["version"] for row in results} == {1}
    assert sorted(row["reused"] for row in results) == [False, True]
    assert repo.dataset_summary(user, "example", dataset, None)["metrics"]["records"] == 2
    with pytest.raises(ConflictError):
        repo.save_dataset_import(user, "example", dataset, scope, {**provenance, "fingerprint": "different"}, parsed)


def _forecast_fixture(client, headers, slug):
    from datetime import date, timedelta

    path = _setup(client, headers, slug)
    start = date(2025, 1, 1)
    lines = ["fecha,producto,cantidad,precio,tx,categoria"]
    for i in range(82):
        day = start + timedelta(days=i)
        lines.append(f"{day},A,{1 + i % 7},2,t{i},Retail")
    lines.append(f"{start},A,1,2,extra,Retail")
    data = ("\n".join(lines) + "\n").encode()
    mapping = {**MAPPING, "transaction_id": "tx", "category": "categoria"}
    response = _post_file(client, path + "/preview", headers, data=data, mapping=json.dumps(mapping))
    assert response.status_code == 200, response.json
    preview = response.json
    assert preview["contract"] == "sales-daily-v2"
    assert preview["quality"]["duplicate_rows"] == 0
    assert _post_file(client, path + "/imports", headers, data=data,
                      preview_token=preview["preview_token"]).status_code == 201
    return path, {"version": 1, "target": "revenue", "group_by": "product", "group": "A", "horizon": 3,
                  "start": start.isoformat(), "complete_through": (start + timedelta(days=80)).isoformat()}


def test_forecast_api_persists_accepted_runs_and_revokes_access(accounts):
    app, account = accounts
    app.config["DATASET_MAX_BYTES"] = 4096
    alice, key, _ = account("alice")
    viewer, vkey, viewer_id = account("reader")
    other, other_key, _ = account("other")
    path, params = _forecast_fixture(alice, key, "alpha")
    endpoint = path + "/forecasts"
    other_path = _setup(other, other_key, "beta")
    assert other.get(endpoint).status_code == 404
    assert app.test_client().get(endpoint).status_code == 401
    assert alice.post(endpoint, json=params).status_code == 403
    assert alice.post(endpoint, headers=key, json={**params, "horizon": 15}).status_code == 400
    assert alice.post(endpoint, headers=key, json={**params, "start": "2025-03-01"}).status_code == 400
    members = BASE + "/alpha/members"
    assert alice.post(members, headers=key, json={"username": "reader", "role": "viewer"}).status_code == 201
    assert viewer.post(endpoint, headers=vkey, json=params).status_code == 403

    response = alice.post(endpoint, headers=key, json={**params, "scenario_pct": 10})
    assert response.status_code == 201, response.json
    assert response.headers["Cache-Control"] == "no-store"
    run = response.json["run"]
    assert run["state"] == "ready"
    origin = run["provenance"]
    assert origin["contract"] == "sales-daily-v2"
    assert origin["source_sha256"] == alice.get(path + "/versions").json["versions"][0]["source_sha256"]
    assert origin["observations_excluded_after_cutoff"]
    assert len(origin["series_sha256"]) == 64 and origin["version"] == 1
    result = run["result"]
    assert result["history"][0]["value"] == 4.0
    assert len(result["forecast"]) == 3
    assert len(result["models"]["naive"]["final"]) >= 5
    assert result["scenario"]["kind"] == "hypothetical"
    detail = endpoint + "/" + str(run["id"])
    assert viewer.get(detail).json["run"] == run
    assert alice.get(endpoint).json["runs"][0]["id"] == run["id"]
    assert other.get(other_path + "/forecasts/" + str(run["id"])).status_code == 404
    assert alice.delete(members + "/" + str(viewer_id), headers=key).status_code == 204
    assert viewer.get(detail).status_code == 404
    units = alice.post(endpoint, headers=key, json={**params, "target": "quantity", "group_by": "category",
                                                    "group": "Retail"}).json["run"]
    assert units["result"]["history"][0]["value"] == 2.0


def test_forecast_capacity_is_persistent_and_rechecks_membership_on_finish(repo):
    from datetime import date
    from app.repositories.forecast_sql import ForecastBusy
    from app.repositories.organization_policy import OrganizationNotFound

    actor = repo.create_user("owner", "owner@example.test", "unused")["id"]
    member = repo.create_user("worker", "worker@example.test", "unused")["id"]
    repo.create_organization(actor, "Example", "example")
    repo.change_organization_member(actor, "example", "member", username="worker")
    dataset = repo.create_dataset(actor, "example", "Ventas")["id"]
    scope = repo.dataset_import_scope(actor, "example", dataset)
    parsed = parse_import(CSV, ".csv", 10, MAPPING)
    source, fingerprint = import_identity(CSV, ".csv", MAPPING)
    repo.save_dataset_import(actor, "example", dataset, scope,
                             {"mapping": MAPPING, "sha256": source, "fingerprint": fingerprint, "format": "csv"}, parsed)
    params = {"target": "revenue", "group_by": "date", "version": 1}
    _, origin = repo.forecast_input(member, "example", dataset, params, date(2026, 11, 1), date(2026, 11, 2))
    barrier = Barrier(2)

    def reserve():
        barrier.wait(timeout=5)
        try:
            return repo.reserve_forecast(member, "example", dataset, params, origin, 2)
        except ForecastBusy:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(reserve), pool.submit(reserve)
        results = [a.result(timeout=10), b.result(timeout=10)]
    assert results.count(None) == 1
    first = next(run for run in results if run is not None)
    repo.change_organization_member(actor, "example", None, target_id=member)
    with pytest.raises(OrganizationNotFound):
        repo.finish_forecast(member, "example", dataset, first, {})
    repo.fail_forecast(member, origin["organization_id"], first)
    assert repo.get_forecast(actor, "example", dataset, first)["state"] == "failed"
    for _ in range(3):
        run = repo.reserve_forecast(actor, "example", dataset, params, origin, 2)
        repo.fail_forecast(actor, origin["organization_id"], run)
    run = repo.reserve_forecast(actor, "example", dataset, params, origin, 2)
    repo.fail_forecast(actor, origin["organization_id"], run)
    with pytest.raises(ForecastBusy, match="cuatro"):
        repo.reserve_forecast(actor, "example", dataset, params, origin, 2)
