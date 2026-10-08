"""Add hair-loss onboarding profile fields.

All columns are nullable so existing production users and rows remain valid.
"""

from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("city", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("hair_wash_frequency", sa.String(30), nullable=True))
    op.add_column("users", sa.Column("water_hardness", sa.String(30), nullable=True))
    op.add_column("users", sa.Column("sugary_food_drink_frequency", sa.String(30), nullable=True))
    op.add_column("users", sa.Column("sexually_active", sa.String(30), nullable=True))
    op.add_column("users", sa.Column("family_hair_loss", sa.String(30), nullable=True))
    op.add_column("users", sa.Column("family_hair_loss_relation", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("dairy_intake", sa.String(30), nullable=True))


def downgrade():
    op.drop_column("users", "dairy_intake")
    op.drop_column("users", "family_hair_loss_relation")
    op.drop_column("users", "family_hair_loss")
    op.drop_column("users", "sexually_active")
    op.drop_column("users", "sugary_food_drink_frequency")
    op.drop_column("users", "water_hardness")
    op.drop_column("users", "hair_wash_frequency")
    op.drop_column("users", "city")
