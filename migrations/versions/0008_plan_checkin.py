"""daily plan check-in (done / not done / skipped) columns

All three columns are nullable with no default, so on PostgreSQL this is a
metadata-only change (no table rewrite, no locking of existing rows). Existing
plans keep checkin_status = NULL, which the application treats as "no check-in
required" -- nobody is blocked by historical data.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("diet_plans", sa.Column("checkin_status", sa.String(20), nullable=True))
    op.add_column("diet_plans", sa.Column("checkin_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("diet_plans", sa.Column("checkin_responded_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("diet_plans", "checkin_responded_at")
    op.drop_column("diet_plans", "checkin_sent_at")
    op.drop_column("diet_plans", "checkin_status")
