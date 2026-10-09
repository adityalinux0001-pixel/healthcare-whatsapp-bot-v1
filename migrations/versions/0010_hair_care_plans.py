"""Add isolated hair-care plan lifecycle table.

Revision ID: 0010
Revises: 0009

The legacy diet_plans table and all historical rows remain untouched. Hair-care plan
content, delivery state, and check-ins live in their own table to keep old constraints
and historical diet data isolated.
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hair_care_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("subscription_id", sa.Integer(), sa.ForeignKey("subscriptions.id"), nullable=True),
        sa.Column("plan_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("day_number", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), server_default="", nullable=False),
        sa.Column("delivery_status", sa.String(length=24), server_default="generating", nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("send_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_send_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_send_error", sa.Text(), nullable=True),
        sa.Column("provider_message_id", sa.String(length=150), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_modification_instruction", sa.Text(), nullable=True),
        sa.Column("last_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkin_status", sa.String(length=20), nullable=True),
        sa.Column("checkin_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkin_responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkin_prompt_status", sa.String(length=20), server_default="not_sent", nullable=False),
        sa.Column("checkin_prompt_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("checkin_prompt_last_error", sa.Text(), nullable=True),
        sa.UniqueConstraint("user_id", "plan_date", name="uq_hair_care_plan_user_date"),
        sa.UniqueConstraint("user_id", "day_number", name="uq_hair_care_plan_user_day"),
    )
    op.create_index("ix_hair_care_plans_user_id", "hair_care_plans", ["user_id"])
    op.create_index("ix_hair_care_plans_subscription_id", "hair_care_plans", ["subscription_id"])
    op.create_index("ix_hair_care_plans_provider_message_id", "hair_care_plans", ["provider_message_id"])
    op.create_index("ix_hair_care_plan_user_date", "hair_care_plans", ["user_id", "plan_date"])
    op.create_index("ix_hair_care_plan_user_day", "hair_care_plans", ["user_id", "day_number"])
    op.create_index(
        "ix_hair_care_plan_checkin_retry", "hair_care_plans",
        ["delivery_status", "checkin_status", "checkin_prompt_status", "checkin_sent_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_hair_care_plan_checkin_retry", table_name="hair_care_plans")
    op.drop_index("ix_hair_care_plan_user_day", table_name="hair_care_plans")
    op.drop_index("ix_hair_care_plan_user_date", table_name="hair_care_plans")
    op.drop_index("ix_hair_care_plans_provider_message_id", table_name="hair_care_plans")
    op.drop_index("ix_hair_care_plans_subscription_id", table_name="hair_care_plans")
    op.drop_index("ix_hair_care_plans_user_id", table_name="hair_care_plans")
    op.drop_table("hair_care_plans")
