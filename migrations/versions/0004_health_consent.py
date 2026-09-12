"""add explicit health data consent timestamp

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-07
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("health_data_consent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("users", "health_data_consent_at")
