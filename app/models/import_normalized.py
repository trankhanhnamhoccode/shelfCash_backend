from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.store import utc_now


class ImportJobModel(Base):
    __tablename__ = "import_jobs"

    import_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("stores.store_id"), nullable=False, index=True
    )
    forecast_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    forecast_horizon: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    legacy_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requires_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class ImportFileModel(Base):
    __tablename__ = "import_files"
    __table_args__ = (
        UniqueConstraint("import_id", "stored_file_name", name="uq_import_file_stored_name"),
        Index("ix_import_files_checksum", "sha256_checksum"),
    )

    import_file_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    import_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("import_jobs.import_id", ondelete="CASCADE"), nullable=False, index=True
    )
    original_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_file_name: Mapped[str] = mapped_column(String(320), nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    sheet_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class ImportSheetProfileModel(Base):
    __tablename__ = "import_sheet_profiles"
    __table_args__ = (
        UniqueConstraint(
            "import_id", "compatibility_sheet_id", name="uq_import_profile_compatibility_sheet"
        ),
    )

    profile_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    import_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("import_jobs.import_id", ondelete="CASCADE"), nullable=False, index=True
    )
    import_file_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("import_files.import_file_id", ondelete="CASCADE"), nullable=False, index=True
    )
    compatibility_sheet_id: Mapped[str] = mapped_column(String(700), nullable=False)
    sheet_name: Mapped[str] = mapped_column(String(255), nullable=False)
    header_row_zero_based: Mapped[int] = mapped_column(Integer, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False)
    columns_json: Mapped[str] = mapped_column(Text, nullable=False)
    dtypes_json: Mapped[str] = mapped_column(Text, nullable=False)
    sample_rows_json: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_rows_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class ImportMappingModel(Base):
    __tablename__ = "import_mappings"
    __table_args__ = (UniqueConstraint("profile_id", name="uq_import_mapping_profile"),)

    import_mapping_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    import_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("import_jobs.import_id", ondelete="CASCADE"), nullable=False, index=True
    )
    profile_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("import_sheet_profiles.profile_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sheet_type: Mapped[str] = mapped_column(String(64), nullable=False)
    column_mapping_json: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    requires_review: Mapped[bool] = mapped_column(Boolean, nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False)
    errors_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ImportIssueModel(Base):
    __tablename__ = "import_issues"
    __table_args__ = (
        Index("ix_import_issues_import_profile", "import_id", "profile_id"),
        CheckConstraint(
            "severity IN ('warning', 'error')",
            name="ck_import_issues_severity",
        ),
    )

    issue_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    import_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("import_jobs.import_id", ondelete="CASCADE"), nullable=False, index=True
    )
    profile_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("import_sheet_profiles.profile_id", ondelete="CASCADE"),
        nullable=True,
    )
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    issue_source: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
