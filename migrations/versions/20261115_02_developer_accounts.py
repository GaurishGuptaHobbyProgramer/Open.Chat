"""Persist developer access grants.

Revision ID: 20261115_02
Revises: 20261115_01
Create Date: 2026-11-15
"""
from alembic import op
import sqlalchemy as sa


revision = "20261115_02"
down_revision = "20261115_01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "developer_accounts",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "developer_signup_challenges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("challenge_id", sa.Text(), nullable=False, unique=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("otp_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=False),
    )


def downgrade():
    raise RuntimeError(
        "Developer-account tables contain authentication data and cannot be "
        "dropped automatically. Use a reviewed, backed-up recovery procedure."
    )
