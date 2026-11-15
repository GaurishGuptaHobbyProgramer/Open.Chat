"""Create the Open.Chat relational schema without deleting existing data.

Revision ID: 20261115_01
Revises:
Create Date: 2026-11-15
"""
from alembic import op
import sqlalchemy as sa


revision = "20261115_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("is_suspended", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("suspended_at", sa.Text()),
        sa.Column("suspended_by", sa.Integer()),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("attachment_name", sa.Text()),
        sa.Column("attachment_type", sa.Text()),
        sa.Column("attachment_url", sa.Text()),
        sa.Column("attachment_size", sa.Integer(), server_default="0"),
    )
    op.create_table(
        "presence",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("personality", sa.Text(), nullable=False),
        sa.Column("greeting", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("display_name", sa.Text()),
        sa.Column("bio", sa.Text()),
        sa.Column("status", sa.Text()),
        sa.Column("avatar_url", sa.Text()),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "user_statuses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("media_url", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.Text(), nullable=False, server_default="direct"),
        sa.Column("title", sa.Text()),
        sa.Column("created_by", sa.Integer()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("last_message_at", sa.Text()),
        sa.Column("is_archived", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_muted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_pinned", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_table(
        "conversation_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="member"),
        sa.Column("joined_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("conversation_id", "user_id"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="sent"),
        sa.Column("reply_to", sa.Integer()),
        sa.Column("attachment_name", sa.Text()),
        sa.Column("attachment_type", sa.Text()),
        sa.Column("attachment_url", sa.Text()),
        sa.Column("attachment_size", sa.Integer(), server_default="0"),
        sa.Column("legacy_message_id", sa.Integer()),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sender_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "conversation_read_state",
        sa.Column("conversation_id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("last_read_at", sa.Text()),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "conversation_typing",
        sa.Column("conversation_id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("is_typing", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "user_notifications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="message"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("link", sa.Text()),
        sa.Column("conversation_id", sa.Integer()),
        sa.Column("sender_id", sa.Integer()),
        sa.Column("is_read", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["sender_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_table(
        "call_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("caller_id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer()),
        sa.Column("conversation_id", sa.Integer()),
        sa.Column("mode", sa.Text(), nullable=False, server_default="audio"),
        sa.Column("status", sa.Text(), nullable=False, server_default="ringing"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["caller_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
    )
    op.create_table(
        "call_signals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("call_id", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("signal_type", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["call_id"], ["call_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sender_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "application_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("endpoint", sa.Text()),
        sa.Column("response_status", sa.Integer()),
        sa.Column("user_id", sa.Integer()),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "enormous_space",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("is_official", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_pinned", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_enormous_space_singleton"),
    )

    op.create_index(
        "idx_conversation_messages_conversation_id_id",
        "conversation_messages",
        ["conversation_id", "id"],
    )
    op.create_index(
        "idx_call_signals_recipient",
        "call_signals",
        ["call_id", "target_id", "id"],
    )
    op.create_index("idx_application_events_created_at", "application_events", ["created_at"])


def downgrade():
    raise RuntimeError(
        "The initial schema migration cannot be downgraded automatically because that would delete user data."
    )
