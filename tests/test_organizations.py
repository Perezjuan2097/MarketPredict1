"""Exercise the private enterprise API against the normal auth and CSRF flow."""
import pytest

from app import create_app
from app.repositories.memory import MemoryRepository
from app.security import limiter


BASE = "/api/me/organizations"


@pytest.fixture
def accounts(tmp_path):
    limiter.hits.clear()
    repo = MemoryRepository()
    app = create_app({"TESTING": True, "PROJECT_ROOT": tmp_path, "REPOSITORY": repo})

    def account(username):
        browser = app.test_client()
        csrf = browser.get("/api/auth/me").json["csrf"]
        response = browser.post("/api/auth/register", headers={"X-CSRF-Token": csrf}, json={
            "username": username, "email": username + "@example.test", "password": "Fixture-password-1",
        })
        assert response.status_code == 201
        uid = response.json["user"]["id"]

        def request(method, path="", body=None):
            return browser.open(BASE + path, method=method, json=body,
                                headers={"X-CSRF-Token": response.json["csrf"]})

        return browser, uid, request

    return app, repo, account


def test_creation_and_tenant_isolation(accounts):
    app, repo, make = accounts
    browser, alice_id, alice = make("alice")
    _, bob_id, bob = make("bob")
    assert app.test_client().get(BASE).status_code == 401
    assert browser.post(BASE, json={"name": "Alpha", "slug": "alpha"}).status_code == 403
    for slug in ("a", "ñandu", "bad--slug", "../bad"):
        assert alice("POST", body={"name": "Alpha", "slug": slug}).status_code == 400
    alpha = alice("POST", body={"name": "Alpha", "slug": " ALPHA "})
    assert alpha.status_code == 201
    assert alpha.headers["Cache-Control"] == "no-store"
    assert alpha.json["organization"]["role"] == "owner"
    assert alice("POST", body={"name": "Duplicate", "slug": "alpha"}).status_code == 409
    assert bob("POST", body={"name": "Beta", "slug": "beta"}).status_code == 201
    assert [o["slug"] for o in alice("GET").json["organizations"]] == ["alpha"]
    assert alice("GET", "?limit=101").status_code == 400
    assert alice("GET", "/beta").status_code == 404
    assert alice("GET", "/beta/members").status_code == 404
    assert alice("GET", "/beta/settings").status_code == 404
    assert alice("PATCH", "/beta/settings", {"currency": "EUR"}).status_code == 404
    assert alice("POST", "/beta/members", {"username": "alice", "role": "owner"}).status_code == 404
    assert alice("PATCH", f"/alpha/members/{bob_id}", {"role": "admin"}).status_code == 404
    repo.admin_update_user(alice_id, None, role="admin")
    assert alice("GET", "/beta").status_code == 404
    assert bob("GET", "/beta/settings").json["settings"] == {"timezone": "UTC", "currency": "USD"}
    assert browser.post("/api/auth/logout", headers={
        "X-CSRF-Token": browser.get("/api/auth/me").json["csrf"],
    }).status_code == 200
    assert alice("GET", "/alpha").status_code == 401


def test_roles_revocation_and_last_owner(accounts):
    _, repo, make = accounts
    _, owner_id, owner = make("owner")
    _, admin_id, admin = make("admin_member")
    _, participant_id, participant = make("participant")
    _, viewer_id, viewer = make("observer")
    assert owner("POST", body={"name": "Alpha", "slug": "alpha"}).status_code == 201
    members = "/alpha/members"
    assert owner("POST", members, {"username": "ADMIN_MEMBER", "role": "admin"}).status_code == 201
    assert admin("POST", members, {"username": "participant", "role": "admin"}).status_code == 403
    assert admin("POST", members, {"username": "participant", "role": "member"}).status_code == 201
    assert admin("POST", members, {"username": "observer", "role": "viewer"}).status_code == 201
    assert admin("POST", members, {"username": "observer", "role": "viewer"}).status_code == 409
    assert viewer("GET", "/alpha").status_code == 200
    assert viewer("GET", members).status_code == 403
    assert participant("GET", members + "?limit=1&after=" + str(owner_id)).json["members"] == [{
        "user_id": admin_id, "username": "admin_member", "role": "admin", "blocked": False,
    }]
    assert participant("DELETE", f"{members}/{viewer_id}").status_code == 403
    assert admin("DELETE", f"{members}/{owner_id}").status_code == 403
    assert admin("PATCH", f"{members}/{admin_id}", {"role": "viewer"}).status_code == 403
    assert admin("PATCH", "/alpha/settings", {"timezone": "America/Bogota", "currency": "eur"}).status_code == 200
    assert viewer("GET", "/alpha/settings").json["settings"] == {"timezone": "America/Bogota", "currency": "EUR"}
    assert participant("PATCH", "/alpha/settings", {"currency": "COP"}).status_code == 403
    assert admin("PATCH", "/alpha/settings", {"timezone": "../UTC"}).status_code == 400
    assert admin("PATCH", "/alpha/settings", {"organization_id": 2}).status_code == 400
    assert owner("DELETE", f"{members}/{owner_id}").status_code == 409

    assert owner("PATCH", f"{members}/{participant_id}", {"role": "owner"}).status_code == 200
    assert owner("DELETE", f"{members}/{owner_id}").status_code == 204
    assert owner("GET", "/alpha").status_code == 404
    assert participant("DELETE", f"{members}/{viewer_id}").status_code == 204
    assert viewer("GET", "/alpha/settings").status_code == 404
    assert participant("PATCH", f"{members}/{participant_id}", {"role": "member"}).status_code == 409
    assert repo.get_organization(participant_id, "alpha")["role"] == "owner"
