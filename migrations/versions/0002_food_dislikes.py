"""add food_dislikes column

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-03

"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("food_dislikes", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("users", "food_dislikes")
