"""Add portable identity keys to an existing fase3 database."""

import sqlalchemy as sa
from alembic import op


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
            key = row[field].strip().lower()
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
        bind.execute(sa.text("LOCK TABLE users, portfolios IN SHARE ROW EXCLUSIVE MODE"))
    if dialect == "sqlite" and bind.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError("La copia SQLite de desarrollo debe migrarse con foreign_keys desactivado.")

    users = _backfill_values(bind, "users", ("username", "email"))
    portfolios = _backfill_values(bind, "portfolios", ("name",), scope="user_id")
    key_type = sa.Text(collation="C") if dialect == "postgresql" else sa.Text()
    for table, names, values in (
        ("users", ("username_key", "email_key"), users),
        ("portfolios", ("name_key",), portfolios),
    ):
        for name in names:
            op.add_column(table, sa.Column(name, key_type, nullable=True))
        target = sa.table(table, sa.column("id", sa.Integer), *(sa.column(name, key_type) for name in names))
        for row_id, keys in values:
            bind.execute(target.update().where(target.c.id == row_id).values(**keys))
        with op.batch_alter_table(table) as batch:
            for name in names:
                batch.alter_column(name, existing_type=key_type, nullable=False)

    op.create_index("ux_users_username_key", "users", ["username_key"], unique=True)
    op.create_index("ux_users_email_key", "users", ["email_key"], unique=True)
    op.create_index("ux_portfolios_owner_name_key", "portfolios", ["user_id", "name_key"], unique=True)


def downgrade():
    raise NotImplementedError("Restaure una copia previa para revertir esta revisión.")
