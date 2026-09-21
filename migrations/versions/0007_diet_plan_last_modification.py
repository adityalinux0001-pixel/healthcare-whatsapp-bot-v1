"""track last applied plan-modification instruction for duplicate-request detection

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("diet_plans", sa.Column("last_modification_instruction", sa.Text(), nullable=True))
    op.add_column("diet_plans", sa.Column("last_modified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("diet_plans", "last_modified_at")
    op.drop_column("diet_plans", "last_modification_instruction")
