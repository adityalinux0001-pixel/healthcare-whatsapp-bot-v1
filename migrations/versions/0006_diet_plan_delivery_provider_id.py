"""store provider message id for daily plan delivery reconciliation

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("diet_plans", sa.Column("provider_message_id", sa.String(150), nullable=True))
    op.create_index("ix_diet_plans_provider_message_id", "diet_plans", ["provider_message_id"])


def downgrade():
    op.drop_index("ix_diet_plans_provider_message_id", table_name="diet_plans")
    op.drop_column("diet_plans", "provider_message_id")
