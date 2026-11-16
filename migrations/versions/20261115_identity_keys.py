"""Add portable identity keys to an existing fase3 database."""

import sqlalchemy as sa
from alembic import op

from app.repositories.base import identity_key

revision = "20261115_identity_keys"
down_revision = None
branch_labels = None
depends_on = None


def _backfill_values(bind, table, fields, scope=None):
    columns = [sa.column("id", sa.Integer), *(sa.column(field, sa.Text) for field in fields)]
    if scope:
        columns.append(sa.column(scope, sa.Integer))
    source = sa.table(table, *columns)
    seen = {field: {} for field in fields}
    values = []
    for row in bind.execute(sa.select(source).order_by(source.c.id)).mappings():
        keys = {}
        for field in fields:
            key = identity_key(row[field])
            scoped_key = (row[scope], key) if scope else key
            previous = seen[field].get(scoped_key)
            if previous is not None:
                raise RuntimeError(f"Identidades duplicadas en {table}.{field}: IDs {previous} y {row['id']}.")
            seen[field][scoped_key] = row["id"]
            keys[f"{field}_key"] = key
        values.append((row["id"], keys))
    return values


def upgrade():
    if op.get_context().as_sql:
        raise RuntimeError("La revisión requiere acceso a los datos para verificar y calcular las claves.")
    bind = op.get_bind()
    dialect = bind.dialect.name
    if not all(sa.inspect(bind).has_table(table) for table in ("users", "portfolios")):
        raise RuntimeError("Se requiere el esquema existente de fase3. Consulte docs/BASE_DE_DATOS.md.")
    if dialect == "postgresql":
        bind.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
        bind.execute(sa.text("LOCK TABLE users, portfolios IN ACCESS EXCLUSIVE MODE"))
    if dialect == "sqlite" and bind.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError("La copia SQLite de desarrollo debe migrarse con foreign_keys desactivado.")

    users = _backfill_values(bind, "users", ("username", "email"))
    portfolios = _backfill_values(bind, "portfolios", ("name",), scope="user_id")
    key_type = sa.Text(collation="C") if dialect == "postgresql" else sa.Text()
    for table, names, values in (
        ("users", ("username_key", "email_key"), users),
        ("portfolios", ("name_key",), portfolios),
    ):
        existing = {column["name"]: column for column in sa.inspect(bind).get_columns(table)}
        for name in names:
            if name not in existing:
                op.add_column(table, sa.Column(name, key_type, nullable=True))
            elif dialect == "postgresql":
                correct_type = bind.execute(sa.text("""
                    SELECT a.atttypid = 'text'::regtype AND c.collname = 'C'
                    FROM pg_attribute a JOIN pg_collation c ON c.oid = a.attcollation
                    WHERE a.attrelid = to_regclass(:table) AND a.attname = :column
                """), {"table": table, "column": name}).scalar()
                if not correct_type:
                    raise RuntimeError(f"Definición inesperada en {table}.{name}; revise el esquema.")
        target = sa.table(table, sa.column("id", sa.Integer), *(sa.column(name, key_type) for name in names))
        actual = {row["id"]: row for row in bind.execute(sa.select(target)).mappings()}
        for row_id, keys in values:
            if any(actual[row_id][key] != value for key, value in keys.items()):
                bind.execute(target.update().where(target.c.id == row_id).values(**keys))
        missing_not_null = [name for name in names if name not in existing or existing[name]["nullable"]]
        if dialect == "postgresql":
            for name in missing_not_null:
                op.alter_column(table, name, existing_type=key_type, nullable=False)
        elif missing_not_null:
            with op.batch_alter_table(table) as batch:
                for name in missing_not_null:
                    batch.alter_column(name, existing_type=key_type, nullable=False)

    for table, name, columns in (
        ("users", "ux_users_username_key", ["username_key"]),
        ("users", "ux_users_email_key", ["email_key"]),
        ("portfolios", "ux_portfolios_owner_name_key", ["user_id", "name_key"]),
    ):
        indexes = {index["name"]: index for index in sa.inspect(bind).get_indexes(table)}
        index = indexes.get(name)
        if index is None:
            op.create_index(name, table, columns, unique=True)
        elif (not index["unique"] or index["column_names"] != columns
              or any(key.endswith("_where") for key in index.get("dialect_options", {}))):
            raise RuntimeError(f"Índice inesperado {name}; revise el esquema.")


def downgrade():
    raise NotImplementedError("Restaure una copia previa para revertir esta revisión.")
