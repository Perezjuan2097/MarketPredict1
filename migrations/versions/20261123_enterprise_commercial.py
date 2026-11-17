"""Add pilot plans, operational telemetry and commercial controls."""
import sqlalchemy as sa
from alembic import op


revision = "20261123_enterprise_commercial"
down_revision = "20261122_enterprise_automation"
branch_labels = None
depends_on = None

TABLES = ('enterprise_plans', 'enterprise_plan_assignments', 'enterprise_worker_heartbeats', 'enterprise_commercial_events')
DEFAULTS = {'Pilot': {'users': 5,
           'datasets': 3,
           'storage_bytes': 5000000,
           'imports': 20,
           'forecasts': 60,
           'automations': 5,
           'retention_days': 30},
 'Starter': {'users': 15,
             'datasets': 10,
             'storage_bytes': 25000000,
             'imports': 100,
             'forecasts': 250,
             'automations': 15,
             'retention_days': 90},
 'Professional': {'users': 80,
                  'datasets': 50,
                  'storage_bytes': 250000000,
                  'imports': 500,
                  'forecasts': 2000,
                  'automations': 60,
                  'retention_days': 180},
 'Enterprise': {'users': 500,
                'datasets': 200,
                'storage_bytes': 2000000000,
                'imports': 5000,
                'forecasts': 20000,
                'automations': 200,
                'retention_days': 365}}


def _create_tables():
    op.create_table('enterprise_plans',
        sa.Column('name', sa.String(length=20), nullable=False),
        sa.Column('limits', sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint('name')
    )
    op.create_table('enterprise_plan_assignments',
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('plan_name', sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['plan_name'], ['enterprise_plans.name'], ),
        sa.PrimaryKeyConstraint('organization_id')
    )
    op.create_table('enterprise_worker_heartbeats',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('state', sa.String(length=12), nullable=False),
        sa.Column('seen_at', sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_table('enterprise_commercial_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=40), nullable=False),
        sa.Column('actor_id', sa.Integer(), nullable=True),
        sa.Column('organization_id', sa.Integer(), nullable=True),
        sa.Column('reference', sa.String(length=40), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = set(inspector.get_table_names()).intersection(TABLES)
    if existing and existing != set(TABLES):
        raise RuntimeError("El esquema comercial existe parcialmente.")
    if not existing:
        _create_tables()
    for table, columns in (
        ("enterprise_jobs", (("started_at", sa.BigInteger()), ("request_id", sa.String(64)))),
        ("enterprise_automations", (("last_outcome", sa.String(24)), ("last_version", sa.Integer()))),
    ):
        names = {column["name"] for column in inspector.get_columns(table)}
        for name, value_type in columns:
            if name not in names:
                op.add_column(table, sa.Column(name, value_type, nullable=True))
    plans = sa.table("enterprise_plans", sa.column("name", sa.String()), sa.column("limits", sa.JSON()))
    known = set(conn.execute(sa.select(plans.c.name)).scalars())
    rows = [{"name": name, "limits": limits} for name, limits in DEFAULTS.items() if name not in known]
    if rows:
        conn.execute(plans.insert(), rows)
    if conn.dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade():
    raise NotImplementedError("Conserve el historial comercial al revertir el código.")
