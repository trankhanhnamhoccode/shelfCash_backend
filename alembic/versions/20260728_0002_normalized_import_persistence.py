"""normalized import persistence

Revision ID: 20260728_0002
Revises: 20260728_0001
Create Date: 2026-07-28
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260728_0002"
down_revision: str | None = "20260728_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_jobs",
        sa.Column("import_id", sa.String(length=36), nullable=False),
        sa.Column("store_id", sa.String(length=128), nullable=False),
        sa.Column("forecast_date", sa.Date(), nullable=True),
        sa.Column("forecast_horizon", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("legacy_status", sa.String(length=32), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column("requires_review", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("failure_message", sa.String(length=500), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("validation_summary_json", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["store_id"], ["stores.store_id"]),
        sa.PrimaryKeyConstraint("import_id"),
    )
    op.create_index("ix_import_jobs_store_id", "import_jobs", ["store_id"])

    op.create_table(
        "import_files",
        sa.Column("import_file_id", sa.String(length=36), nullable=False),
        sa.Column("import_id", sa.String(length=36), nullable=False),
        sa.Column("original_file_name", sa.String(length=255), nullable=False),
        sa.Column("stored_file_name", sa.String(length=320), nullable=False),
        sa.Column("stored_path", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("sha256_checksum", sa.String(length=64), nullable=False),
        sa.Column("sheet_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["import_id"], ["import_jobs.import_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("import_file_id"),
        sa.UniqueConstraint("import_id", "stored_file_name", name="uq_import_file_stored_name"),
    )
    op.create_index("ix_import_files_import_id", "import_files", ["import_id"])
    op.create_index("ix_import_files_checksum", "import_files", ["sha256_checksum"])

    op.create_table(
        "import_sheet_profiles",
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("import_id", sa.String(length=36), nullable=False),
        sa.Column("import_file_id", sa.String(length=36), nullable=False),
        sa.Column("compatibility_sheet_id", sa.String(length=700), nullable=False),
        sa.Column("sheet_name", sa.String(length=255), nullable=False),
        sa.Column("header_row_zero_based", sa.Integer(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("column_count", sa.Integer(), nullable=False),
        sa.Column("columns_json", sa.Text(), nullable=False),
        sa.Column("dtypes_json", sa.Text(), nullable=False),
        sa.Column("sample_rows_json", sa.Text(), nullable=False),
        sa.Column("parsed_rows_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["import_file_id"], ["import_files.import_file_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["import_id"], ["import_jobs.import_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("profile_id"),
        sa.UniqueConstraint(
            "import_id",
            "compatibility_sheet_id",
            name="uq_import_profile_compatibility_sheet",
        ),
    )
    op.create_index("ix_import_sheet_profiles_import_id", "import_sheet_profiles", ["import_id"])
    op.create_index(
        "ix_import_sheet_profiles_import_file_id", "import_sheet_profiles", ["import_file_id"]
    )

    op.create_table(
        "import_mappings",
        sa.Column("import_mapping_id", sa.String(length=36), nullable=False),
        sa.Column("import_id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("sheet_type", sa.String(length=64), nullable=False),
        sa.Column("column_mapping_json", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("requires_review", sa.Boolean(), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("warnings_json", sa.Text(), nullable=False),
        sa.Column("errors_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["import_id"], ["import_jobs.import_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["import_sheet_profiles.profile_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("import_mapping_id"),
        sa.UniqueConstraint("profile_id", name="uq_import_mapping_profile"),
    )
    op.create_index("ix_import_mappings_import_id", "import_mappings", ["import_id"])
    op.create_index("ix_import_mappings_profile_id", "import_mappings", ["profile_id"])

    op.create_table(
        "import_issues",
        sa.Column("issue_id", sa.String(length=36), nullable=False),
        sa.Column("import_id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=True),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=128), nullable=False),
        sa.Column("message", sa.String(length=1000), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=True),
        sa.Column("issue_source", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["import_id"], ["import_jobs.import_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["import_sheet_profiles.profile_id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "severity IN ('warning', 'error')",
            name="ck_import_issues_severity",
        ),
        sa.PrimaryKeyConstraint("issue_id"),
    )
    op.create_index("ix_import_issues_import_id", "import_issues", ["import_id"])
    op.create_index(
        "ix_import_issues_import_profile", "import_issues", ["import_id", "profile_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_import_issues_import_profile", table_name="import_issues")
    op.drop_index("ix_import_issues_import_id", table_name="import_issues")
    op.drop_table("import_issues")
    op.drop_index("ix_import_mappings_profile_id", table_name="import_mappings")
    op.drop_index("ix_import_mappings_import_id", table_name="import_mappings")
    op.drop_table("import_mappings")
    op.drop_index("ix_import_sheet_profiles_import_file_id", table_name="import_sheet_profiles")
    op.drop_index("ix_import_sheet_profiles_import_id", table_name="import_sheet_profiles")
    op.drop_table("import_sheet_profiles")
    op.drop_index("ix_import_files_checksum", table_name="import_files")
    op.drop_index("ix_import_files_import_id", table_name="import_files")
    op.drop_table("import_files")
    op.drop_index("ix_import_jobs_store_id", table_name="import_jobs")
    op.drop_table("import_jobs")
