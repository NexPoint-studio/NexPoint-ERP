from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AdminLock(Base):
    __tablename__ = "admin_locks"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_admin_locks_singleton"),
        CheckConstraint("version >= 1", name="ck_admin_locks_version"),
        CheckConstraint(
            "timeout_minutes between 1 and 480",
            name="ck_admin_locks_timeout_minutes",
        ),
        CheckConstraint(
            "failed_attempt_count between 0 and 1000000",
            name="ck_admin_locks_failed_attempt_count",
        ),
        CheckConstraint(
            "recovery_failed_attempt_count between 0 and 1000000",
            name="ck_admin_locks_recovery_failed_attempt_count",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    timeout_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=15, server_default=text("15")
    )
    failed_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    lockout_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_failed_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    recovery_lockout_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_recovery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    configured_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class AdminRecoveryCode(Base):
    __tablename__ = "admin_recovery_codes"
    __table_args__ = (
        UniqueConstraint("code_hash", name="uq_admin_recovery_codes_hash"),
        CheckConstraint(
            "length(batch_uid) = 36 and batch_uid = lower(batch_uid)",
            name="ck_admin_recovery_codes_batch_uid",
        ),
        CheckConstraint(
            "status in ('ACTIVE','USED','REVOKED')",
            name="ck_admin_recovery_codes_status",
        ),
        CheckConstraint(
            "(status = 'ACTIVE' and used_at is null and used_by is null and invalidated_at is null) "
            "or (status = 'USED' and used_at is not null and used_by is not null) "
            "or (status = 'REVOKED' and invalidated_at is not null)",
            name="ck_admin_recovery_codes_lifecycle",
        ),
        Index("ix_admin_recovery_codes_batch_status", "batch_uid", "status"),
        Index("ix_admin_recovery_codes_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_uid: Mapped[str] = mapped_column(String(36), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
