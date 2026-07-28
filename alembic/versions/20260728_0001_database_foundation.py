"""database foundation with legacy imports compatibility

Revision ID: 20260728_0001
Revises:
Create Date: 2026-07-28
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260728_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names())

    if "imports" not in existing_tables:
        op.create_table(
            "imports",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("payload", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if "stores" not in existing_tables:
        op.create_table(
            "stores",
            sa.Column("store_id", sa.String(length=128), nullable=False),
            sa.Column("store_name", sa.String(length=255), nullable=False),
            sa.Column(
                "timezone",
                sa.String(length=64),
                nullable=False,
                server_default="Asia/Ho_Chi_Minh",
            ),
            sa.Column("currency", sa.String(length=3), nullable=False, server_default="VND"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.PrimaryKeyConstraint("store_id"),
        )

    if "idempotency_records" not in existing_tables:
        op.create_table(
            "idempotency_records",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("store_id", sa.String(length=128), nullable=True),
            sa.Column("endpoint", sa.String(length=255), nullable=False),
            sa.Column("http_method", sa.String(length=16), nullable=False),
            sa.Column("idempotency_key", sa.String(length=255), nullable=False),
            sa.Column("request_hash", sa.String(length=128), nullable=True),
            sa.Column("resource_type", sa.String(length=128), nullable=True),
            sa.Column("resource_id", sa.String(length=255), nullable=True),
            sa.Column("response_status", sa.Integer(), nullable=True),
            sa.Column("response_body_json", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["store_id"], ["stores.store_id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "store_id",
                "endpoint",
                "http_method",
                "idempotency_key",
                name="uq_idempotency_scope_key",
            ),
        )
        op.create_index(
            "ix_idempotency_records_store_id",
            "idempotency_records",
            ["store_id"],
            unique=False,
        )

    if "audit_logs" not in existing_tables:
        op.create_table(
            "audit_logs",
            sa.Column("audit_log_id", sa.String(length=36), nullable=False),
            sa.Column("store_id", sa.String(length=128), nullable=True),
            sa.Column("action", sa.String(length=128), nullable=False),
            sa.Column("resource_type", sa.String(length=128), nullable=False),
            sa.Column("resource_id", sa.String(length=255), nullable=False),
            sa.Column("before_json", sa.Text(), nullable=True),
            sa.Column("after_json", sa.Text(), nullable=True),
            sa.Column("source", sa.String(length=128), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(["store_id"], ["stores.store_id"]),
            sa.PrimaryKeyConstraint("audit_log_id"),
        )
        op.create_index("ix_audit_logs_store_id", "audit_logs", ["store_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names())
    if "audit_logs" in existing_tables:
        op.drop_index("ix_audit_logs_store_id", table_name="audit_logs")
        op.drop_table("audit_logs")
    if "idempotency_records" in existing_tables:
        op.drop_index("ix_idempotency_records_store_id", table_name="idempotency_records")
        op.drop_table("idempotency_records")
    if "stores" in existing_tables:
        op.drop_table("stores")
    # Never drop imports: it may predate Alembic and contains legacy user data.
