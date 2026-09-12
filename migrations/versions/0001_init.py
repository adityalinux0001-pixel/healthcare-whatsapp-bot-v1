"""init

Revision ID: 0001
Revises: 
Create Date: 2026-09-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("phone_number", sa.String(20), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=True),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("gender", sa.String(20), nullable=True),
        sa.Column("height_cm", sa.Float(), nullable=True),
        sa.Column("weight_kg", sa.Float(), nullable=True),
        sa.Column("activity_level", sa.String(30), nullable=True),
        sa.Column("goal", sa.String(30), nullable=True),
        sa.Column("diet_preference", sa.String(30), nullable=True),
        sa.Column("allergies", sa.Text(), nullable=True),
        sa.Column("medical_conditions", sa.Text(), nullable=True),
        sa.Column("onboarding_complete", sa.Boolean(), server_default="false"),
        sa.Column("conversation_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_phone_number", "users", ["phone_number"], unique=True)

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("razorpay_payment_id", sa.String(100), nullable=True, unique=True),
        sa.Column("amount_inr", sa.Float(), nullable=False),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])
    op.create_index("ix_subscriptions_razorpay_payment_id", "subscriptions", ["razorpay_payment_id"])

    op.create_table(
        "payment_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("razorpay_link_id", sa.String(100), nullable=False, unique=True),
        sa.Column("short_url", sa.String(300), nullable=False),
        sa.Column("amount_inr", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("razorpay_payment_id", sa.String(100), nullable=True),
    )
    op.create_index("ix_payment_links_user_id", "payment_links", ["user_id"])
    op.create_index("ix_payment_links_razorpay_link_id", "payment_links", ["razorpay_link_id"], unique=True)

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("whatsapp_message_id", sa.String(100), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_messages_user_id", "messages", ["user_id"])
    op.create_index("ix_messages_whatsapp_message_id", "messages", ["whatsapp_message_id"], unique=True)

    op.create_table(
        "diet_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("plan_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "plan_date", name="uq_diet_plan_user_date"),
    )
    op.create_index("ix_diet_plan_user_date", "diet_plans", ["user_id", "plan_date"])

    op.create_table(
        "processed_webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("event_id", sa.String(150), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_processed_webhook_events_event_id", "processed_webhook_events", ["event_id"], unique=True)


def downgrade():
    op.drop_table("processed_webhook_events")
    op.drop_table("diet_plans")
    op.drop_table("messages")
    op.drop_table("payment_links")
    op.drop_table("subscriptions")
    op.drop_table("users")
