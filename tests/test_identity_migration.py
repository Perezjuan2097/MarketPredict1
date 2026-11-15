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
