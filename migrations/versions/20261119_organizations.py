"""Add private organization directories and settings."""
import sqlalchemy as sa
from alembic import op


revision = "20261119_organizations"
down_revision = "20261118_forecast_saves"
branch_labels = None
depends_on = None

TABLES = ("organizations", "organization_memberships", "organization_settings")


def _dates():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = {table for table in TABLES if inspector.has_table(table)}
    if not existing:
        op.create_table(
            "organizations",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("slug", sa.String(48), nullable=False, unique=True),
            sa.Column("status", sa.String(12), nullable=False, server_default="active"),
            *_dates(), sa.CheckConstraint("status IN ('active','suspended')", name="ck_organizations_status"),
        )
        op.create_table(
            "organization_memberships",
            sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                      primary_key=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True),
            sa.Column("role", sa.String(8), nullable=False),
            *_dates(),
            sa.CheckConstraint("role IN ('owner','admin','member','viewer')", name="ck_organization_memberships_role"),
        )
        op.create_index("ix_organization_memberships_user", "organization_memberships", ["user_id", "organization_id"])
        op.create_table(
            "organization_settings",
            sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                      primary_key=True),
            sa.Column("timezone", sa.String(64), server_default="UTC", nullable=False),
            sa.Column("currency", sa.String(3), server_default="USD", nullable=False),
            *_dates(),
        )
    elif (existing != set(TABLES)
          or inspector.get_pk_constraint("organization_memberships")["constrained_columns"]
          != ["organization_id", "user_id"]
          or any(not required <= {col["name"] for col in inspector.get_columns(table)}
                 for table, required in {
                     "organizations": {"id", "name", "slug", "status", "created_at", "updated_at"},
                     "organization_memberships": {"organization_id", "user_id", "role", "created_at", "updated_at"},
                     "organization_settings": {"organization_id", "timezone", "currency", "created_at", "updated_at"},
                 }.items())
          or not any(unique["column_names"] == ["slug"] for unique in inspector.get_unique_constraints("organizations"))
          or any(not any(fk["referred_table"] == parent and fk["constrained_columns"] == [column]
                         and fk["options"].get("ondelete") == action for fk in inspector.get_foreign_keys(table))
                 for table, column, parent, action in (
                     ("organization_memberships", "organization_id", "organizations", "CASCADE"),
                     ("organization_memberships", "user_id", "users", "RESTRICT"),
                     ("organization_settings", "organization_id", "organizations", "CASCADE"),
                 ))):
        raise RuntimeError("El esquema empresarial existente está incompleto o es incompatible.")
    if conn.dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade():
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        conn.exec_driver_sql("LOCK TABLE organizations, organization_memberships, organization_settings "
                             "IN ACCESS EXCLUSIVE MODE")
    elif conn.dialect.name == "sqlite" and not conn.connection.driver_connection.in_transaction:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
    for table in TABLES:
        if conn.exec_driver_sql(f"SELECT 1 FROM {table} LIMIT 1").first():
            raise RuntimeError("Conserve las tablas empresariales mientras tengan datos.")
    for table in reversed(TABLES):
        op.drop_table(table)
