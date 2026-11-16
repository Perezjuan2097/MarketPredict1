"""Add revocable sessions without changing existing account records."""

import sqlalchemy as sa
from alembic import op


revision = "20261117_auth_sessions"
down_revision = "20261116_identity_repair"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("auth_sessions"):
        columns = {column["name"]: column for column in inspector.get_columns("auth_sessions")}
        if (not {"digest", "user_id", "admin_key", "expires_at"} <= columns.keys()
                or columns["expires_at"]["nullable"]
                or inspector.get_pk_constraint("auth_sessions")["constrained_columns"] != ["digest"]
                or not any(fk["constrained_columns"] == ["user_id"] and fk["referred_table"] == "users"
                           and fk["options"].get("ondelete") == "CASCADE"
                           for fk in inspector.get_foreign_keys("auth_sessions"))):
            raise RuntimeError("La tabla auth_sessions existente tiene un esquema incompatible.")
    else:
        op.create_table(
            "auth_sessions",
            sa.Column("digest", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE")),
            sa.Column("admin_key", sa.String(64)),
            sa.Column("expires_at", sa.BigInteger, nullable=False),
        )
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("auth_sessions")}
    for column in ("user_id", "expires_at"):
        name = f"ix_auth_sessions_{column}"
        if name not in indexes:
            op.create_index(name, "auth_sessions", [column])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE auth_sessions ENABLE ROW LEVEL SECURITY")


def downgrade():
    raise NotImplementedError("Restaure un respaldo si debe retirar esta revisión.")
