import os
from datetime import timedelta
from uuid import uuid4

import pytest
from flask import jsonify

from app import create_app
from app.repositories.memory import MemoryRepository
from app.security import client_ip, limiter


@pytest.fixture
def app(tmp_path):
    limiter.hits.clear()
    return create_app({"TESTING": True, "PROJECT_ROOT": tmp_path, "REPOSITORY": MemoryRepository(),
                       "ADMIN_TOKEN": "admin-fixture"})


def register(client, name):
    seed = client.get("/api/auth/me").json["csrf"]
    result = client.post("/api/auth/register", json={"username": name, "email": f"{name}@x.test",
                                                          "password": "Example-password-123"},
                         headers={"X-CSRF-Token": seed})
    assert result.status_code == 201
    assert result.json["csrf"] != seed
    return result.json["user"], {"X-CSRF-Token": result.json["csrf"]}


def admin_login(client):
    client.get("/admin/login")
    with client.session_transaction() as session:
        csrf = session["csrf"]
    assert client.post("/admin/login", data={"csrf_token": csrf, "token": "admin-fixture"}).status_code == 302
    with client.session_transaction() as session:
        return {"X-CSRF-Token": session["csrf"]}


def test_logout_revokes_copied_cookie_and_disables_caching(app):
    owner, copy = app.test_client(), app.test_client()
    user, headers = register(owner, "owner")
    copy.set_cookie("session", owner.get_cookie("session").value)
    assert copy.get("/api/me/portfolios").status_code == 200
    assert owner.post("/api/auth/logout", json={}).status_code == 403
    assert owner.get("/api/auth/me").json["user"]["id"] == user["id"]
    response = owner.post("/api/auth/logout", headers=headers)
    assert response.status_code == 200
    assert response.json["csrf"] != headers["X-CSRF-Token"]
    assert owner.get("/api/auth/me").json["user"] is None
    denied = copy.get("/api/me/portfolios")
    assert denied.status_code == 401
    assert denied.headers["Cache-Control"] == "no-store"


def test_broker_permission_uses_the_endpoint_policy_without_contacting_alpaca(app, monkeypatch):
    from app.services import broker_service

    anonymous, allowed, denied, admin = (app.test_client() for _ in range(4))
    allowed_user, allowed_csrf = register(allowed, "allowed")
    register(denied, "denied")
    admin_login(admin)
    monkeypatch.setattr(broker_service, "allowed_users", lambda: {allowed_user["id"]})
    monkeypatch.setattr(broker_service.AlpacaPaper, "configured", property(lambda self: True))
    calls = []

    def snapshot(_self):
        calls.append(True)
        return {"account": {}, "paper": True}

    monkeypatch.setattr(broker_service.AlpacaPaper, "snapshot", snapshot)
    for client, expected in ((anonymous, False), (denied, False), (allowed, True), (admin, True)):
        assert client.get("/api/auth/me").json["can_access_broker"] is expected
    assert calls == []

    for client, status in ((anonymous, 401), (denied, 403)):
        response = client.get("/api/broker/status")
        assert response.status_code == status
        assert response.json["authorized"] is False
        assert "account" not in response.json
    assert calls == []
    for client in (allowed, admin):
        assert client.get("/api/broker/status").json["authorized"] is True
    assert len(calls) == 2

    logout = allowed.post("/api/auth/logout", headers=allowed_csrf)
    login = allowed.post("/api/auth/login", json={"identifier": "allowed", "password": "Example-password-123"},
                         headers={"X-CSRF-Token": logout.json["csrf"]})
    assert login.status_code == 200
    assert login.json["can_access_broker"] is True
    assert len(calls) == 2


def test_session_expiration_and_cookie_flags(app):
    app.config.update(PERMANENT_SESSION_LIFETIME=timedelta(seconds=0), SESSION_COOKIE_SECURE=True)
    client = app.test_client()
    seed = client.get("/api/auth/me", base_url="https://localhost").json["csrf"]
    response = client.post("/api/auth/register", base_url="https://localhost",
                           json={"username": "owner", "email": "owner@x.test", "password": "Password123"},
                           headers={"X-CSRF-Token": seed})
    cookie = response.headers["Set-Cookie"]
    assert response.status_code == 201
    assert all(flag in cookie for flag in ("Secure", "HttpOnly", "SameSite=Lax"))
    assert client.get("/api/me/portfolios", base_url="https://localhost").status_code == 401


def test_block_and_unblock_require_admin_and_revoke_copied_grant(app, caplog):
    member, administrator = app.test_client(), app.test_client()
    user, headers = register(member, "member")
    path = f"/admin/api/users/{user['id']}/block"
    assert member.post(path, headers=headers, json={"blocked": True}).status_code == 403
    admin_headers = admin_login(administrator)
    assert administrator.post(path, json={"blocked": True}).status_code == 403
    with caplog.at_level("INFO", logger="marketpredict"):
        assert administrator.post(path, headers=admin_headers, json={"blocked": True}).status_code == 200
        assert administrator.post(path, headers=admin_headers, json={"blocked": False}).status_code == 200
    assert member.get("/api/me/portfolios").status_code == 401
    assert "security_event=account_blocked" in caplog.text
    assert "security_event=account_unblocked" in caplog.text
    assert "admin-fixture" not in caplog.text


def test_history_and_community_are_bound_to_the_authenticated_actor(app):
    alice, bob = app.test_client(), app.test_client()
    _, headers = register(alice, "alice")
    register(bob, "bobby")
    legacy = {"symbol": "AAPL", "fecha": "2026-11-08", "horizonte": "1", "prediccion": 1,
              "modelo": "test", "version": "1"}
    assert app.test_client().get("/api/history").status_code == 401
    assert alice.post("/api/save", json=legacy).status_code == 403
    assert alice.post("/api/save", json=legacy, headers=headers).status_code == 200
    assert len(alice.get("/api/history").json) == 1
    assert bob.get("/api/history").json == []
    assert alice.post("/api/community/AAPL/post", json={"text": "test"}).status_code == 403
    result = alice.post("/api/community/AAPL/post", json={"author": "bobby", "text": "test"}, headers=headers)
    assert result.status_code == 201
    assert result.json["post"]["author"] == "alice"
    for _ in range(2):
        vote = alice.post("/api/community/AAPL/vote", json={"direction": "bullish"}, headers=headers)
        assert vote.json["sentiment"]["votes"] == 1
    assert alice.post("/api/report", json={"symbol": "AAPL"}).status_code == 403


def test_proxy_chain_and_spoofed_rate_limit(app):
    app.config["RATE_LIMIT_PER_MINUTE"] = 2
    client = app.test_client()
    for index in range(3):
        resp = client.get("/api/search", headers={"X-Forwarded-For": f"198.51.100.{index}"})
        assert resp.status_code == (200 if index < 2 else 429)
    behind_proxy = create_app({"TESTING": True, "TRUSTED_PROXY_CIDRS": "10.0.0.0/24"})
    behind_proxy.add_url_rule("/peer", view_func=lambda: jsonify(ip=client_ip()))
    resp = behind_proxy.test_client().get("/peer", environ_overrides={"REMOTE_ADDR": "10.0.0.1"},
                                         headers={"X-Forwarded-For": "203.0.113.9, 198.51.100.4, 10.0.0.2"})
    assert resp.json["ip"] == "198.51.100.4"


def test_unexpected_exception_is_redacted(app, caplog):
    def raise_error():
        raise RuntimeError("secret-fixture")

    app.add_url_rule("/error-fixture", view_func=raise_error)
    response = app.test_client().get("/error-fixture")
    assert response.status_code == 500
    assert "secret-fixture" not in response.get_data(as_text=True)
    assert "secret-fixture" not in caplog.text


def test_payload_limits_and_report_escaping(app):
    from app.services.report_service import InformeEmpresarial

    client = app.test_client()
    assert client.post("/api/analyze", json=[]).status_code == 400
    assert client.post("/api/analyze", json={"symbol": "x" * (1024 * 1024)}).status_code == 413
    content, name = InformeEmpresarial().generar("../<script>", [{"fecha": "2026-11-01"}],
                                                [], [], {"nivel": "N", "fundamento": "<img>"}, "estable")
    assert "/" not in name and "\\" not in name
    assert b"<img>" not in content.getvalue() and b"&lt;img&gt;" in content.getvalue()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_shared_sql_grants_are_revoked_across_app_instances(backend, tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.schema import CreateSchema, DropSchema
    from app.models.orm import Base
    from app.repositories import factory
    from app import db

    limiter.hits.clear()
    admin = schema = None
    if backend == "sqlite":
        url = f"sqlite:///{(tmp_path / 'security.db').as_posix()}"
        engine = create_engine(url)
    else:
        url = os.getenv("TEST_POSTGRES_URL")
        if not url:
            pytest.skip("requiere TEST_POSTGRES_URL")
        schema = f"mp_security_{uuid4().hex}"
        admin = create_engine(url)
        with admin.begin() as conn:
            conn.execute(CreateSchema(schema))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine)
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setattr(db, "get_engine", lambda: engine)
        monkeypatch.setattr(factory, "get_engine", lambda: engine)
        config = {"TESTING": True, "SECRET_KEY": "same-secret-for-both-test-processes"}
        first, second = create_app(config).test_client(), create_app(config).test_client()
        _, headers = register(first, "owner")
        second.set_cookie("session", first.get_cookie("session").value)
        assert second.get("/api/me/portfolios").status_code == 200
        assert first.post("/api/auth/logout", headers=headers).status_code == 200
        assert second.get("/api/me/portfolios").status_code == 401
    finally:
        engine.dispose()
        if admin is not None:
            with admin.begin() as conn:
                conn.execute(DropSchema(schema, cascade=True))
            admin.dispose()
