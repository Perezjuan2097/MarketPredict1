"""Revocable authentication grants. Only digests are stored on the server."""

from hashlib import sha256
from hmac import compare_digest, new as hmac_new
from threading import Lock
from time import time

from flask import abort, current_app, g, session
from sqlalchemy import delete, insert, select

from app.models.orm import User, auth_sessions as grants


def _digest(value):
    return sha256(value.encode("utf-8")).hexdigest()


def _admin_key(token):
    secret = current_app.secret_key
    key = secret.encode() if isinstance(secret, str) else secret
    return hmac_new(key, token.encode(), "sha256").hexdigest()


class SessionStore:
    def __init__(self):
        self._data = {}
        self._lock = Lock()

    def _engine(self):
        from app.db import database_url, get_engine
        if database_url() and not current_app.config.get("REPOSITORY"):
            return get_engine()
        return None

    def add(self, key, grant):
        engine = self._engine()
        if engine is not None:
            with engine.begin() as conn:
                if grant["user_id"] is not None:
                    blocked = conn.execute(select(User.is_blocked).where(
                        User.id == grant["user_id"]
                    ).with_for_update()).scalar_one_or_none()
                    if blocked is not False:
                        abort(401)
                conn.execute(delete(grants).where(grants.c.expires_at <= int(time())))
                conn.execute(insert(grants).values(digest=key, **grant))
        else:
            with self._lock:
                self._data = {key: row for key, row in self._data.items() if row["expires_at"] > time()}
                self._data[key] = grant

    def get(self, key):
        engine = self._engine()
        if engine is not None:
            with engine.connect() as conn:
                return conn.execute(select(grants).where(
                    grants.c.digest == key, grants.c.expires_at > int(time())
                )).mappings().one_or_none()
        with self._lock:
            result = self._data.get(key)
            return result if result and result["expires_at"] > time() else None

    def revoke(self, key):
        engine = self._engine()
        if engine is not None:
            with engine.begin() as conn:
                conn.execute(delete(grants).where(grants.c.digest == key))
        else:
            with self._lock:
                self._data.pop(key, None)

    def revoke_user(self, user_id):
        engine = self._engine()
        if engine is not None:
            with engine.begin() as conn:
                conn.execute(delete(grants).where(grants.c.user_id == user_id))
        else:
            with self._lock:
                self._data = {key: row for key, row in self._data.items() if row["user_id"] != user_id}


def _store():
    return current_app.extensions["session_store"]


def end_session():
    if sid := session.get("sid"):
        _store().revoke(_digest(sid))
    session.clear()
    g.auth_grant = None


def start_session(*, user_id=None, admin=False):
    from secrets import token_urlsafe

    end_session()
    sid = token_urlsafe(32)
    lifetime = current_app.permanent_session_lifetime
    if admin:
        lifetime = current_app.config["ADMIN_SESSION_LIFETIME"]
    grant = {"user_id": user_id, "expires_at": int(time() + lifetime.total_seconds()), "admin_key": None}
    if admin:
        grant["admin_key"] = _admin_key(current_app.config["ADMIN_TOKEN"])
    _store().add(_digest(sid), grant)
    session["sid"] = sid
    session.permanent = True
    g.auth_grant = grant


def auth_grant():
    if "auth_grant" not in g:
        sid = session.get("sid")
        g.auth_grant = _store().get(_digest(sid)) if sid else None
    return g.auth_grant


def user_id():
    grant = auth_grant()
    return grant["user_id"] if grant else None


def is_admin():
    token = current_app.config.get("ADMIN_TOKEN")
    return bool(token and (grant := auth_grant()) and grant["admin_key"]
                and compare_digest(grant["admin_key"], _admin_key(token)))
