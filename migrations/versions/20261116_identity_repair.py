"""Reconcile identity constraints on installations already stamped at 1A."""

from importlib import import_module

from alembic import op


revision = "20261116_identity_repair"
down_revision = "20261115_identity_keys"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        import_module("migrations.versions.20261115_identity_keys").upgrade()


def downgrade():
    raise NotImplementedError("Restaure una copia previa para revertir esta revisión.")
