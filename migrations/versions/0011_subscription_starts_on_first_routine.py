"""Defer paid subscription term until first successful hair routine delivery.

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Pending paid subscriptions have no dates until the first routine is confirmed
    # delivered. Existing active/history records retain their original dates.
    op.alter_column(
        "subscriptions", "start_date",
        existing_type=sa.DateTime(timezone=True), nullable=True,
    )
    op.alter_column(
        "subscriptions", "end_date",
        existing_type=sa.DateTime(timezone=True), nullable=True,
    )
    op.add_column("subscriptions", sa.Column("term_days", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    pending_unstarted = bind.execute(sa.text(
        "SELECT COUNT(*) FROM subscriptions "
        "WHERE status = 'pending' AND (start_date IS NULL OR end_date IS NULL)"
    )).scalar_one()
    if pending_unstarted:
        raise RuntimeError(
            "Cannot downgrade while paid subscriptions are awaiting first routine delivery; "
            "resolve/activate those subscriptions before retrying the downgrade."
        )
    op.drop_column("subscriptions", "term_days")
    op.alter_column(
        "subscriptions", "start_date",
        existing_type=sa.DateTime(timezone=True), nullable=False,
    )
    op.alter_column(
        "subscriptions", "end_date",
        existing_type=sa.DateTime(timezone=True), nullable=False,
    )
