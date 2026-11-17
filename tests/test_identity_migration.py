"""Exercise the identity revision on a pre-existing schema with linked records."""

import os
from importlib import import_module
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.schema import CreateSchema, DropSchema


@pytest.fixture(params=("sqlite", "postgresql"))
def connection(request, tmp_path):
    if request.param == "sqlite":
        engine = sa.create_engine(sa.URL.create("sqlite", database=str(tmp_path / "legacy.db")))
        try:
            with engine.begin() as conn:
                yield conn
        finally:
            engine.dispose()
        return
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("requiere TEST_POSTGRES_URL")
    schema = f"mp_test_{uuid4().hex}"
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(CreateSchema(schema))
        with engine.begin() as conn:
            conn.exec_driver_sql(f"SET LOCAL search_path TO {schema}")
            yield conn
        with engine.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
    finally:
        engine.dispose()


def _legacy(conn):
    conn.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, email TEXT UNIQUE NOT NULL)")
    conn.exec_driver_sql(
        "CREATE TABLE portfolios (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), name TEXT NOT NULL)"
    )
    conn.exec_driver_sql("CREATE TABLE trades (id INTEGER PRIMARY KEY, portfolio_id INTEGER NOT NULL REFERENCES portfolios(id))")
    conn.exec_driver_sql("INSERT INTO users VALUES (4, 'Clara', 'Éxito@x.co')")
    conn.exec_driver_sql("INSERT INTO portfolios VALUES (7, 4, 'Índice')")
    conn.exec_driver_sql("INSERT INTO trades VALUES (9, 7)")


def _upgrade(conn):
    revision = import_module("migrations.versions.20261115_identity_keys")
    with Operations.context(MigrationContext.configure(conn)):
        revision.upgrade()


def test_postgres_repairs_stamped_partial_installation(connection, monkeypatch):
    if connection.dialect.name != "postgresql":
        pytest.skip("reparación de revisión PostgreSQL")
    from alembic import command
    from alembic.config import Config

    _legacy(connection)
    connection.exec_driver_sql("ALTER TABLE users ADD COLUMN password_hash TEXT")
    connection.exec_driver_sql("UPDATE users SET password_hash = 'fixture-hash'")
    connection.exec_driver_sql('ALTER TABLE users ADD COLUMN username_key TEXT COLLATE "C"')
    connection.exec_driver_sql("CREATE TABLE predictions (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id))")
    connection.exec_driver_sql("INSERT INTO predictions VALUES (11, 4)")
    oids = connection.exec_driver_sql("SELECT 'users'::regclass::oid, 'portfolios'::regclass::oid").one()
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_POSTGRES_URL"])
    cfg = Config("alembic.ini")
    cfg.attributes["connection"] = connection
    command.stamp(cfg, "20261115_identity_keys")
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")
    assert MigrationContext.configure(connection).get_current_revision() == "20261118_forecast_saves"
    assert sa.inspect(connection).has_table("auth_sessions")
    assert sa.inspect(connection).has_table("forecast_emission_saves")
    assert connection.exec_driver_sql(
        "SELECT relrowsecurity FROM pg_class WHERE oid = 'forecast_emission_saves'::regclass"
    ).scalar_one() is True
    assert connection.exec_driver_sql("SELECT 'users'::regclass::oid, 'portfolios'::regclass::oid").one() == oids
    assert connection.exec_driver_sql("SELECT id, username_key, email_key, password_hash FROM users").one() == (
        4, "clara", "éxito@x.co", "fixture-hash"
    )
    assert connection.exec_driver_sql("SELECT id, name_key FROM portfolios").one() == (7, "índice")
    assert connection.exec_driver_sql("SELECT * FROM predictions").one() == (11, 4)
    assert connection.exec_driver_sql("SELECT * FROM trades").one() == (9, 7)


def test_identity_revision_preserves_data(connection):
    _legacy(connection)
    _upgrade(connection)
    assert connection.exec_driver_sql("SELECT * FROM users").one() == (4, "Clara", "Éxito@x.co", "clara", "éxito@x.co")
    assert connection.exec_driver_sql("SELECT * FROM portfolios").one() == (7, 4, "Índice", "índice")
    assert connection.exec_driver_sql("SELECT * FROM trades").one() == (9, 7)
    assert sa.inspect(connection).get_foreign_keys("trades")[0]["referred_table"] == "portfolios"
    with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
        connection.exec_driver_sql("INSERT INTO users VALUES (5, 'CLARA', 'otro@x.co', 'clara', 'otro@x.co')")


def test_identity_revision_stops_before_ddl_on_collision(connection):
    _legacy(connection)
    connection.exec_driver_sql("INSERT INTO portfolios VALUES (8, 4, 'íNDICE')")
    with pytest.raises(RuntimeError, match=r"portfolios\.name: IDs 7 y 8"):
        _upgrade(connection)
    assert [c["name"] for c in sa.inspect(connection).get_columns("users")] == ["id", "username", "email"]
    assert connection.exec_driver_sql("SELECT count(*) FROM portfolios").scalar_one() == 2

def test_forecast_emission_revision_is_additive_and_reentrant(connection):
    _legacy(connection)
    revision = import_module("migrations.versions.20261118_forecast_saves")
    with Operations.context(MigrationContext.configure(connection)):
        revision.upgrade()
        revision.upgrade()
    inspector = sa.inspect(connection)
    assert inspector.get_pk_constraint("forecast_emission_saves")["constrained_columns"] == ["user_id", "emission_id"]
    assert inspector.get_foreign_keys("forecast_emission_saves")[0]["referred_table"] == "users"
    assert connection.exec_driver_sql("SELECT * FROM users").one() == (4, "Clara", "Éxito@x.co")
    assert connection.exec_driver_sql("SELECT * FROM trades").one() == (9, 7)
