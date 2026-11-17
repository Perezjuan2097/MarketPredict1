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
    assert MigrationContext.configure(connection).get_current_revision() == "20261121_enterprise_forecasts"
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

def test_organization_revision_adds_rls_and_only_downgrades_empty_tables(connection):
    _legacy(connection)
    migration = import_module("migrations.versions.20261119_organizations")
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        migration.upgrade()
        for table in migration.TABLES:
            assert sa.inspect(connection).has_table(table)
            if connection.dialect.name == "postgresql":
                assert connection.exec_driver_sql(
                    f"SELECT relrowsecurity FROM pg_class WHERE oid = '{table}'::regclass"
                ).scalar_one() is True
                assert not connection.exec_driver_sql(
                    f"SELECT 1 FROM pg_policy WHERE polrelid = '{table}'::regclass"
                ).first()
        connection.exec_driver_sql("INSERT INTO organizations (id, name, slug) VALUES (1, 'Fixture', 'fixture')")
        with pytest.raises(RuntimeError, match="mientras tengan datos"):
            migration.downgrade()
        connection.exec_driver_sql("DELETE FROM organizations")
        migration.downgrade()
    assert not sa.inspect(connection).has_table("organizations")
    assert connection.exec_driver_sql("SELECT id FROM users").scalar_one() == 4

def test_postgres_rls_denies_unprivileged_role(connection):
    if connection.dialect.name != "postgresql":
        pytest.skip("RLS PostgreSQL")
    configured = os.getenv("TEST_RLS_PROBE_ROLE")
    if configured is None:
        can_create = connection.exec_driver_sql(
            "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        ).scalar_one()
        if not can_create:
            pytest.skip("requiere rol de prueba RLS o superusuario en clúster de prueba")
    with connection.begin_nested() as savepoint:
        role = connection.dialect.identifier_preparer.quote_identifier(configured or f"mp_probe_{uuid4().hex}")
        if configured is None:
            connection.exec_driver_sql(f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS")
        _legacy(connection)
        with Operations.context(MigrationContext.configure(connection)):
            import_module("migrations.versions.20261118_forecast_saves").upgrade()
            import_module("migrations.versions.20261119_organizations").upgrade()
            import_module("migrations.versions.20261120_enterprise_datasets").upgrade()
            import_module("migrations.versions.20261121_enterprise_forecasts").upgrade()
        connection.exec_driver_sql("INSERT INTO organizations (id, name, slug) VALUES (1, 'Fixture', 'fixture')")
        connection.exec_driver_sql(
            "INSERT INTO enterprise_datasets (id, organization_id, owner_id, name, currency) VALUES (10, 1, 4, 'Ventas', 'USD')"
        )
        connection.exec_driver_sql(
            "INSERT INTO enterprise_dataset_versions (id, organization_id, dataset_id, version, status) VALUES (20, 1, 10, 1, 'ready')"
        )
        connection.exec_driver_sql(
            "INSERT INTO enterprise_forecast_runs (organization_id, dataset_id, version_id, requested_by, state, "
            "created_epoch, lease_until, parameters, provenance) VALUES (1, 10, 20, 4, 'ready', 1, 2, '{}', '{}')"
        )
        connection.exec_driver_sql(
            "INSERT INTO forecast_emission_saves (user_id, emission_id, prediction_ids) VALUES (4, 'test', '[]')"
        )
        schema = connection.dialect.identifier_preparer.quote_identifier(
            connection.exec_driver_sql("SELECT current_schema()").scalar_one())
        connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA {schema} TO {role}")
        for table in ("organizations", "forecast_emission_saves", "enterprise_forecast_runs"):
            assert connection.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar_one() == 1
            connection.exec_driver_sql(f"GRANT SELECT, UPDATE ON {table} TO {role}")
        connection.exec_driver_sql(f"SET LOCAL ROLE {role}")
        for table, column in (("organizations", "name"), ("forecast_emission_saves", "emission_id"),
                              ("enterprise_forecast_runs", "state")):
            assert connection.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar_one() == 0
            assert connection.exec_driver_sql(f"UPDATE {table} SET {column} = 'changed'").rowcount == 0
        savepoint.rollback()

def test_dataset_schema_guards_tenant_relations_and_preserves_rows(connection):
    _legacy(connection)
    migration = import_module("migrations.versions.20261120_enterprise_datasets")
    with Operations.context(MigrationContext.configure(connection)):
        import_module("migrations.versions.20261119_organizations").upgrade()
        migration.upgrade()
        migration.upgrade()
        assert set(migration.TABLES) <= set(sa.inspect(connection).get_table_names())
        if connection.dialect.name == "postgresql":
            for table in migration.TABLES:
                assert connection.exec_driver_sql(
                    f"SELECT relrowsecurity FROM pg_class WHERE oid = '{table}'::regclass"
                ).scalar_one()
        connection.exec_driver_sql(
            "INSERT INTO organizations (id, name, slug) VALUES (1, 'Alpha', 'alpha'), (2, 'Beta', 'beta')"
        )
        connection.exec_driver_sql(
            "INSERT INTO enterprise_datasets (id, organization_id, owner_id, name, currency) "
            "VALUES (10, 1, 4, 'Ventas', 'USD')"
        )
        fks = sa.inspect(connection).get_foreign_keys("enterprise_dataset_versions")
        assert fks[0]["constrained_columns"] == ["organization_id", "dataset_id"]
        if connection.dialect.name == "postgresql":
            with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
                connection.exec_driver_sql(
                    "INSERT INTO enterprise_dataset_versions (organization_id, dataset_id, version, status) "
                    "VALUES (2, 10, 1, 'ready')"
                )
        with pytest.raises(RuntimeError, match="mientras tengan datos"):
            migration.downgrade()
        connection.exec_driver_sql("DELETE FROM enterprise_datasets")
        migration.downgrade()
    assert sa.inspect(connection).has_table("organizations")
    assert connection.exec_driver_sql("SELECT id FROM users").scalar_one() == 4


def test_forecast_migration_preserves_sales_rows_and_enforces_version_scope(connection):
    _legacy(connection)
    revision = import_module("migrations.versions.20261121_enterprise_forecasts")
    with Operations.context(MigrationContext.configure(connection)):
        import_module("migrations.versions.20261119_organizations").upgrade()
        import_module("migrations.versions.20261120_enterprise_datasets").upgrade()
        connection.exec_driver_sql("INSERT INTO organizations (id, name, slug) VALUES (1, 'Fixture', 'fixture')")
        connection.exec_driver_sql(
            "INSERT INTO enterprise_datasets (id, organization_id, owner_id, name, currency) "
            "VALUES (10, 1, 4, 'Ventas', 'USD'), (11, 1, 4, 'Otros', 'USD')"
        )
        connection.exec_driver_sql(
            "INSERT INTO enterprise_dataset_versions (id, organization_id, dataset_id, version, status) "
            "VALUES (20, 1, 10, 1, 'ready')"
        )
        connection.exec_driver_sql(
            "INSERT INTO enterprise_dataset_rows (organization_id, version_id, row_number, date, product, quantity, revenue) "
            "VALUES (1, 20, 2, '2025-01-01', 'A', 20000, 40000)"
        )
        revision.upgrade()
        revision.upgrade()
        assert connection.exec_driver_sql(
            "SELECT quantity, revenue, transaction_id FROM enterprise_dataset_rows"
        ).one() == (20000, 40000, None)
        keys = sa.inspect(connection).get_foreign_keys("enterprise_forecast_runs")
        assert any(k["constrained_columns"] == ["organization_id", "dataset_id", "version_id"]
                   for k in keys)
        if connection.dialect.name == "postgresql":
            assert connection.exec_driver_sql(
                "SELECT relrowsecurity FROM pg_class WHERE oid = 'enterprise_forecast_runs'::regclass"
            ).scalar_one()
            with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
                connection.exec_driver_sql(
                    "INSERT INTO enterprise_forecast_runs (organization_id, dataset_id, version_id, requested_by, state, "
                    "created_epoch, lease_until, parameters, provenance) VALUES (1, 11, 20, 4, 'running', 1, 2, '{}', '{}')"
                )
        connection.exec_driver_sql("UPDATE enterprise_dataset_rows SET transaction_id = 'TX1'")
        with pytest.raises(RuntimeError, match="Conserve el esquema"):
            revision.downgrade()
        connection.exec_driver_sql("UPDATE enterprise_dataset_rows SET transaction_id = NULL")
        revision.downgrade()
    assert connection.exec_driver_sql("SELECT product FROM enterprise_dataset_rows").scalar_one() == "A"
