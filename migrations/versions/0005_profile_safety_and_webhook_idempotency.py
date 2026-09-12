"""tighten onboarding safety state and webhook idempotency

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("allergies_answered", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("medical_conditions_answered", sa.Boolean(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE users
            SET allergies_answered = CASE WHEN onboarding_complete OR allergies IS NOT NULL THEN true ELSE false END,
                medical_conditions_answered = CASE WHEN onboarding_complete OR medical_conditions IS NOT NULL THEN true ELSE false END
            """
        )
    )
    op.alter_column("users", "allergies_answered", nullable=False, server_default="false")
    op.alter_column("users", "medical_conditions_answered", nullable=False, server_default="false")

    # Replace global event-id uniqueness with the correct source+event-id key.
    op.drop_index("ix_processed_webhook_events_event_id", table_name="processed_webhook_events")
    op.create_index(
        "ix_processed_webhook_events_event_id",
        "processed_webhook_events",
        ["event_id"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_processed_webhook_source_event",
        "processed_webhook_events",
        ["source", "event_id"],
    )
    op.create_index(
        "ix_processed_webhook_source_event",
        "processed_webhook_events",
        ["source", "event_id"],
    )


def downgrade():
    op.drop_index("ix_processed_webhook_source_event", table_name="processed_webhook_events")
    op.drop_constraint(
        "uq_processed_webhook_source_event",
        "processed_webhook_events",
        type_="unique",
    )
    op.drop_index("ix_processed_webhook_events_event_id", table_name="processed_webhook_events")
    op.create_index(
        "ix_processed_webhook_events_event_id",
        "processed_webhook_events",
        ["event_id"],
        unique=True,
    )
    op.drop_column("users", "medical_conditions_answered")
    op.drop_column("users", "allergies_answered")
