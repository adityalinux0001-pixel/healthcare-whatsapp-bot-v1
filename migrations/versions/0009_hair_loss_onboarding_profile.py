"""Add nullable hair-loss onboarding profile fields to existing users.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-09

No tables are created or removed. Existing profile/payment/message/plan records are retained.
The dedicated completion flag intentionally starts false for all existing accounts so they
can complete the new hair-focused onboarding without rewriting their legacy onboarding data.
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("city", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("hair_wash_frequency", sa.String(length=40), nullable=True))
    op.add_column("users", sa.Column("water_hardness", sa.String(length=40), nullable=True))
    op.add_column("users", sa.Column("sugary_food_drink_intake", sa.String(length=40), nullable=True))
    op.add_column("users", sa.Column("sexually_active", sa.String(length=30), nullable=True))
    op.add_column("users", sa.Column("family_hair_loss", sa.String(length=20), nullable=True))
    op.add_column("users", sa.Column("family_hair_loss_relation", sa.String(length=50), nullable=True))
    op.add_column("users", sa.Column("dairy_intake", sa.String(length=40), nullable=True))
    op.add_column(
        "users",
        sa.Column("hair_onboarding_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    # Downgrading drops the new fields; take a DB backup before migration in production.
    op.drop_column("users", "hair_onboarding_complete")
    op.drop_column("users", "dairy_intake")
    op.drop_column("users", "family_hair_loss_relation")
    op.drop_column("users", "family_hair_loss")
    op.drop_column("users", "sexually_active")
    op.drop_column("users", "sugary_food_drink_intake")
    op.drop_column("users", "water_hardness")
    op.drop_column("users", "hair_wash_frequency")
    op.drop_column("users", "city")
