"""Create private, versioned enterprise datasets and typed records."""
import sqlalchemy as sa
from alembic import op


revision = "20261120_enterprise_datasets"
down_revision = "20261119_organizations"
branch_labels = None
depends_on = None

TABLES = ('enterprise_datasets', 'enterprise_dataset_versions', 'enterprise_dataset_imports', 'enterprise_dataset_quality', 'enterprise_dataset_rows', 'enterprise_dataset_schemas')

def _create_tables():
    op.create_table('enterprise_datasets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('archived', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'id')
    )
    op.create_table('enterprise_dataset_versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('dataset_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status = 'ready' AND version > 0", name='ck_enterprise_version_ready'),
        sa.ForeignKeyConstraint(['organization_id', 'dataset_id'], ['enterprise_datasets.organization_id', 'enterprise_datasets.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'dataset_id', 'version'),
        sa.UniqueConstraint('organization_id', 'id')
    )
    op.create_table('enterprise_dataset_imports',
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('version_id', sa.Integer(), nullable=False),
        sa.Column('uploaded_by', sa.Integer(), nullable=False),
        sa.Column('source_format', sa.String(length=5), nullable=False),
        sa.Column('source_sha256', sa.String(length=64), nullable=False),
        sa.Column('fingerprint', sa.String(length=64), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id', 'version_id'], ['enterprise_dataset_versions.organization_id', 'enterprise_dataset_versions.id'], ),
        sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('organization_id', 'version_id')
    )
    op.create_table('enterprise_dataset_quality',
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('version_id', sa.Integer(), nullable=False),
        sa.Column('counts', sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id', 'version_id'], ['enterprise_dataset_versions.organization_id', 'enterprise_dataset_versions.id'], ),
        sa.PrimaryKeyConstraint('organization_id', 'version_id')
    )
    op.create_table('enterprise_dataset_rows',
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('version_id', sa.Integer(), nullable=False),
        sa.Column('row_number', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('product', sa.String(length=160), nullable=False),
        sa.Column('category', sa.String(length=160), nullable=True),
        sa.Column('quantity', sa.BigInteger(), nullable=False),
        sa.Column('price', sa.BigInteger(), nullable=True),
        sa.Column('revenue', sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id', 'version_id'], ['enterprise_dataset_versions.organization_id', 'enterprise_dataset_versions.id'], ),
        sa.PrimaryKeyConstraint('organization_id', 'version_id', 'row_number')
    )
    op.create_index('ix_enterprise_dataset_rows_date', 'enterprise_dataset_rows', ['organization_id', 'version_id', 'date'], unique=False)
    op.create_table('enterprise_dataset_schemas',
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('version_id', sa.Integer(), nullable=False),
        sa.Column('contract', sa.String(length=40), nullable=False),
        sa.Column('mapping', sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id', 'version_id'], ['enterprise_dataset_versions.organization_id', 'enterprise_dataset_versions.id'], ),
        sa.PrimaryKeyConstraint('organization_id', 'version_id')
    )


def upgrade():
    conn = op.get_bind()
    existing = set(sa.inspect(conn).get_table_names()).intersection(TABLES)
    if existing and existing != set(TABLES):
        raise RuntimeError("El esquema de datasets está incompleto.")
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
        raise RuntimeError("Conserve las tablas de datasets mientras tengan datos.")
    for table in reversed(TABLES):
        op.drop_table(table)
