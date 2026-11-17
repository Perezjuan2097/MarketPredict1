"""Create organization-scoped jobs, schedules and deduplicated alerts."""
import sqlalchemy as sa
from alembic import op


revision = "20261122_enterprise_automation"
down_revision = "20261121_enterprise_forecasts"
branch_labels = None
depends_on = None

TABLES = ('enterprise_automations', 'enterprise_jobs', 'enterprise_alert_rules', 'enterprise_alerts')


def _create_tables():
    op.create_table('enterprise_automations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('dataset_id', sa.Integer(), nullable=False),
        sa.Column('version_id', sa.Integer(), nullable=False),
        sa.Column('actor_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('parameters', sa.JSON(), nullable=False),
        sa.Column('cadence', sa.String(length=8), nullable=False),
        sa.Column('next_at', sa.BigInteger(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['organization_id', 'dataset_id', 'version_id'], ['enterprise_dataset_versions.organization_id', 'enterprise_dataset_versions.dataset_id', 'enterprise_dataset_versions.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_enterprise_automations_due', 'enterprise_automations', ['enabled', 'next_at'], unique=False)
    op.create_table('enterprise_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('dataset_id', sa.Integer(), nullable=False),
        sa.Column('version_id', sa.Integer(), nullable=False),
        sa.Column('actor_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('state', sa.String(length=12), nullable=False),
        sa.Column('parameters', sa.JSON(), nullable=False),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('error_code', sa.String(length=40), nullable=True),
        sa.Column('idempotency_key', sa.String(length=140), nullable=False),
        sa.Column('fingerprint', sa.String(length=64), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('available_at', sa.BigInteger(), nullable=False),
        sa.Column('lease_until', sa.BigInteger(), nullable=True),
        sa.Column('lease_token', sa.String(length=40), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('finished_at', sa.BigInteger(), nullable=True),
        sa.CheckConstraint("state IN ('pending','running','completed','failed','canceled')", name='ck_enterprise_job_state'),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['organization_id', 'dataset_id', 'version_id'], ['enterprise_dataset_versions.organization_id', 'enterprise_dataset_versions.dataset_id', 'enterprise_dataset_versions.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'idempotency_key')
    )
    op.create_index('ix_enterprise_jobs_due', 'enterprise_jobs', ['state', 'available_at'], unique=False)
    op.create_index('ix_enterprise_jobs_lease', 'enterprise_jobs', ['state', 'lease_until'], unique=False)
    op.create_index('ix_enterprise_jobs_scope', 'enterprise_jobs', ['organization_id', 'dataset_id', 'id'], unique=False)
    op.create_table('enterprise_alert_rules',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('dataset_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('comparison', sa.String(length=8), nullable=False),
        sa.Column('threshold', sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column('severity', sa.String(length=8), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id', 'dataset_id'], ['enterprise_datasets.organization_id', 'enterprise_datasets.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'dataset_id', 'id')
    )
    op.create_table('enterprise_alerts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('dataset_id', sa.Integer(), nullable=False),
        sa.Column('rule_id', sa.Integer(), nullable=False),
        sa.Column('event_key', sa.String(length=80), nullable=False),
        sa.Column('state', sa.String(length=12), nullable=False),
        sa.Column('evidence', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id', 'dataset_id', 'rule_id'], ['enterprise_alert_rules.organization_id', 'enterprise_alert_rules.dataset_id', 'enterprise_alert_rules.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('rule_id', 'event_key')
    )
    op.create_index('ix_enterprise_alerts_scope', 'enterprise_alerts', ['organization_id', 'dataset_id', 'state'], unique=False)


def upgrade():
    conn = op.get_bind()
    existing = set(sa.inspect(conn).get_table_names()).intersection(TABLES)
    if existing and existing != set(TABLES):
        raise RuntimeError("El esquema de automatización existe parcialmente.")
    if not existing:
        _create_tables()
    if conn.dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade():
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        conn.exec_driver_sql(f"LOCK TABLE {', '.join(TABLES)} IN ACCESS EXCLUSIVE MODE")
    elif conn.dialect.name == "sqlite" and not conn.connection.driver_connection.in_transaction:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
    if any(conn.exec_driver_sql(f"SELECT 1 FROM {table} LIMIT 1").first() for table in TABLES):
        raise RuntimeError("Conserve el esquema mientras existan trabajos, automatizaciones o alertas.")
    for table in reversed(TABLES):
        op.drop_table(table)
