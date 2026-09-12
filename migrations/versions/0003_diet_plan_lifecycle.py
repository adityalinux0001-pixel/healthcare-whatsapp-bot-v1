"""add durable diet-plan lifecycle and service-day numbering

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "diet_plans",
        sa.Column("subscription_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_diet_plans_subscription_id",
        "diet_plans",
        "subscriptions",
        ["subscription_id"],
        ["id"],
    )
    op.create_index(
        "ix_diet_plans_subscription_id",
        "diet_plans",
        ["subscription_id"],
    )

    # Day number is nullable only during the migration so existing rows can be
    # backfilled deterministically before the NOT NULL constraint is introduced.
    op.add_column("diet_plans", sa.Column("day_number", sa.Integer(), nullable=True))
    op.add_column(
        "diet_plans",
        sa.Column("delivery_status", sa.String(20), nullable=True),
    )
    op.add_column(
        "diet_plans",
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "diet_plans",
        sa.Column("send_attempts", sa.Integer(), nullable=True),
    )
    op.add_column(
        "diet_plans",
        sa.Column("last_send_error", sa.Text(), nullable=True),
    )

    # Older code stored plan_date as UTC midnight. Normalize it to the canonical
    # Asia/Kolkata midnight-in-UTC representation used by the new service.
    op.execute(
        sa.text(
            """
            UPDATE diet_plans
            SET plan_date = plan_date - INTERVAL '5 hours 30 minutes'
            """
        )
    )

    # Backfill monotonic service day per user from the existing chronological plans.
    op.execute(
        sa.text(
            """
            WITH numbered AS (
                SELECT id,
                       ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY plan_date, id) AS rn
                FROM diet_plans
            )
            UPDATE diet_plans d
            SET day_number = numbered.rn
            FROM numbered
            WHERE d.id = numbered.id
            """
        )
    )

    # Existing plans have already been created by the old system, so treat them as
    # delivered. New rows are created as pending and become sent after WhatsApp succeeds.
    op.execute(
        sa.text(
            """
            UPDATE diet_plans
            SET delivery_status = 'sent',
                send_attempts = 1
            WHERE content IS NOT NULL AND content <> ''
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE diet_plans
            SET delivery_status = COALESCE(delivery_status, 'pending'),
                send_attempts = COALESCE(send_attempts, 0)
            """
        )
    )

    # Existing rows intentionally keep subscription_id NULL. Early renewals can
    # create overlapping subscription periods, so guessing ownership here could
    # attach an old plan to the wrong payment. New plans always store the exact
    # active subscription that authorized them.

    op.alter_column("diet_plans", "day_number", nullable=False)
    op.alter_column(
        "diet_plans",
        "delivery_status",
        nullable=False,
        server_default="pending",
    )
    op.alter_column(
        "diet_plans",
        "send_attempts",
        nullable=False,
        server_default="0",
    )

    op.create_unique_constraint(
        "uq_diet_plan_user_day",
        "diet_plans",
        ["user_id", "day_number"],
    )
    op.create_index(
        "ix_diet_plan_user_day",
        "diet_plans",
        ["user_id", "day_number"],
    )


def downgrade():
    op.drop_index("ix_diet_plan_user_day", table_name="diet_plans")
    op.drop_constraint("uq_diet_plan_user_day", "diet_plans", type_="unique")
    op.drop_column("diet_plans", "last_send_error")
    op.drop_column("diet_plans", "send_attempts")
    op.drop_column("diet_plans", "sent_at")
    op.drop_column("diet_plans", "delivery_status")
    op.drop_column("diet_plans", "day_number")
    op.drop_index("ix_diet_plans_subscription_id", table_name="diet_plans")
    op.drop_constraint("fk_diet_plans_subscription_id", "diet_plans", type_="foreignkey")
    op.drop_column("diet_plans", "subscription_id")
