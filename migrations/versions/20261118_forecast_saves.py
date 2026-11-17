"""Persist forecast emission idempotency without changing historical predictions."""
import sqlalchemy as sa
from alembic import op


revision = "20261118_forecast_saves"
down_revision = "20261117_auth_sessions"
branch_labels = None
depends_on = None


def upgrade():
    # Fresh installs can already have this table via Base.metadata.create_all.
    if not sa.inspect(op.get_bind()).has_table("forecast_emission_saves"):
        op.create_table(
            "forecast_emission_saves",
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("emission_id", sa.String(64), primary_key=True),
            sa.Column("prediction_ids", sa.JSON, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    else:
        inspector = sa.inspect(op.get_bind())
        columns = {column["name"]: column for column in inspector.get_columns("forecast_emission_saves")}
        if (not {"user_id", "emission_id", "prediction_ids", "created_at"} <= columns.keys()
                or any(columns[name]["nullable"] for name in ("prediction_ids", "created_at"))
                or inspector.get_pk_constraint("forecast_emission_saves")["constrained_columns"]
                != ["user_id", "emission_id"]
                or not any(fk["constrained_columns"] == ["user_id"] and fk["referred_table"] == "users"
                           and fk["options"].get("ondelete") == "CASCADE"
                           for fk in inspector.get_foreign_keys("forecast_emission_saves"))):
            raise RuntimeError("La tabla forecast_emission_saves existente tiene un esquema incompatible.")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE forecast_emission_saves ENABLE ROW LEVEL SECURITY")


def downgrade():
    raise NotImplementedError("Conserve el registro de idempotencia al revertir el código de aplicación.")
