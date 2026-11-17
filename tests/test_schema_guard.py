"""Schema safeguards: production schema belongs to Alembic, and RLS activation fails closed."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.repositories.factory import auto_create_allowed, enable_rls


def _engine(dialect, begin=None):
    return SimpleNamespace(dialect=SimpleNamespace(name=dialect), begin=begin)


def _app(**config):
    return SimpleNamespace(config=config)


@pytest.mark.parametrize(("env", "dialect", "flag", "expected"), [
    ("production", "postgresql", True, False),   # Alembic owns the production schema.
    ("development", "postgresql", True, True),
    ("production", "sqlite", True, True),
    ("development", "sqlite", False, False),
    ("production", "postgresql", False, False),
])
def test_auto_create_is_refused_for_production_postgres(env, dialect, flag, expected):
    assert auto_create_allowed(_app(ENV=env, AUTO_CREATE_TABLES=flag), _engine(dialect)) is expected


def test_enable_rls_propagates_failures_instead_of_leaving_tables_exposed():
    @contextmanager
    def failing_begin():
        raise RuntimeError("permission denied")
        yield

    with pytest.raises(RuntimeError):
        enable_rls(_engine("postgresql", failing_begin))


def test_enable_rls_targets_every_model_table():
    from app.models.orm import Base

    statements = []

    class Connection:
        def execute(self, statement):
            statements.append(str(statement))

    @contextmanager
    def begin():
        yield Connection()

    enable_rls(_engine("postgresql", begin))
    assert len(statements) == len(Base.metadata.tables)
    assert all(sql.endswith("ENABLE ROW LEVEL SECURITY") for sql in statements)
    assert any('"enterprise_plans"' in sql for sql in statements)
    enable_rls(_engine("sqlite"))  # No-op outside PostgreSQL.
