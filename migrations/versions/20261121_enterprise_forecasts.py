"""Add private forecast runs and optional transaction identifiers."""
import sqlalchemy as sa
from alembic import op


revision = "20261121_enterprise_forecasts"
down_revision = "20261120_enterprise_datasets"
branch_labels = None
depends_on = None

SCOPE_INDEX = "ux_enterprise_version_scope"


def _create_run_table():
    op.create_table(
        "enterprise_forecast_runs",
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('dataset_id', sa.Integer(), nullable=False),
        sa.Column('version_id', sa.Integer(), nullable=False),
        sa.Column('requested_by', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(length=12), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('created_epoch', sa.BigInteger(), nullable=False),
        sa.Column('lease_until', sa.BigInteger(), nullable=False),
        sa.Column('parameters', sa.JSON(), nullable=False),
        sa.Column('provenance', sa.JSON(), nullable=False),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.CheckConstraint("state IN ('running','ready','failed')", name='ck_enterprise_forecast_state'),
        sa.ForeignKeyConstraint(
            ["organization_id", "dataset_id", "version_id"],
            ["enterprise_dataset_versions.organization_id", "enterprise_dataset_versions.dataset_id",
             "enterprise_dataset_versions.id"],
        ),
        sa.ForeignKeyConstraint(['requested_by'], ['users.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_enterprise_forecast_actor', 'enterprise_forecast_runs', ['requested_by', 'created_epoch'], unique=False)
    op.create_index('ix_enterprise_forecast_capacity', 'enterprise_forecast_runs', ['state', 'lease_until'], unique=False)
    op.create_index('ix_enterprise_forecast_dataset', 'enterprise_forecast_runs', ['organization_id', 'dataset_id', 'id'], unique=False)


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    transaction = next((column for column in inspector.get_columns("enterprise_dataset_rows")
                        if column["name"] == "transaction_id"), None)
    if transaction is None:
        op.add_column("enterprise_dataset_rows", sa.Column("transaction_id", sa.String(160), nullable=True))
    elif not transaction["nullable"] or getattr(transaction["type"], "length", None) != 160:
        raise RuntimeError("La columna de transacción existente tiene un esquema incompatible.")
    scope_columns = ["organization_id", "dataset_id", "id"]
    scope = next((index for index in inspector.get_indexes("enterprise_dataset_versions")
                  if index["name"] == SCOPE_INDEX), None)
    if scope is None:
        op.create_index(SCOPE_INDEX, "enterprise_dataset_versions", scope_columns, unique=True)
    elif not scope["unique"] or scope["column_names"] != scope_columns:
        raise RuntimeError("El índice de alcance existente es incompatible.")
    if not inspector.has_table("enterprise_forecast_runs"):
        _create_run_table()
    elif not any(fk["constrained_columns"] == ["organization_id", "dataset_id", "version_id"]
                 and fk["referred_table"] == "enterprise_dataset_versions"
                 and fk["referred_columns"] == ["organization_id", "dataset_id", "id"]
                 for fk in inspector.get_foreign_keys("enterprise_forecast_runs")):
        raise RuntimeError("La tabla de pronósticos existente no tiene el alcance requerido.")
    if conn.dialect.name == "postgresql":
        op.execute("ALTER TABLE enterprise_forecast_runs ENABLE ROW LEVEL SECURITY")


def downgrade():
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        conn.exec_driver_sql("LOCK TABLE enterprise_forecast_runs, enterprise_dataset_rows IN ACCESS EXCLUSIVE MODE")
    elif conn.dialect.name == "sqlite" and not conn.connection.driver_connection.in_transaction:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
    if conn.exec_driver_sql("SELECT 1 FROM enterprise_forecast_runs LIMIT 1").first() or conn.exec_driver_sql(
            "SELECT 1 FROM enterprise_dataset_rows WHERE transaction_id IS NOT NULL LIMIT 1").first():
        raise RuntimeError("Conserve el esquema mientras haya pronósticos o identificadores de transacción.")
    op.drop_table("enterprise_forecast_runs")
    op.drop_index(SCOPE_INDEX, table_name="enterprise_dataset_versions")
    op.drop_column("enterprise_dataset_rows", "transaction_id")
