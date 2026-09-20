from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class RememberSession(Base):
    """Revocable persistent sign-in token stored only as a one-way hash."""

    __tablename__ = "remember_sessions"
    __table_args__ = (
        CheckConstraint(
            "length(id) = 32 and id = lower(id) and id not glob '*[^0-9a-f]*'",
            name="ck_remember_sessions_id",
        ),
        CheckConstraint(
            "length(token_hash) = 64 and token_hash = lower(token_hash) "
            "and token_hash not glob '*[^0-9a-f]*'",
            name="ck_remember_sessions_token_hash",
        ),
        CheckConstraint(
            "length(session_generation_hash) = 64 "
            "and session_generation_hash = lower(session_generation_hash) "
            "and session_generation_hash not glob '*[^0-9a-f]*'",
            name="ck_remember_sessions_generation_hash",
        ),
        CheckConstraint(
            "typeof(auth_version) = 'integer' and auth_version >= 1",
            name="ck_remember_sessions_auth_version",
        ),
        CheckConstraint(
            "status in ('ACTIVE','REVOKED','EXPIRED')",
            name="ck_remember_sessions_status",
        ),
        CheckConstraint(
            "(status = 'ACTIVE' and revoked_at is null) "
            "or (status in ('REVOKED','EXPIRED') and revoked_at is not null)",
            name="ck_remember_sessions_lifecycle",
        ),
        Index(
            "ix_remember_sessions_user_status_expiry",
            "user_id",
            "status",
            "expires_at",
        ),
        Index(
            "ix_remember_sessions_status_expiry",
            "status",
            "expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    installation_id: Mapped[str] = mapped_column(String(80), nullable=False)
    auth_version: Mapped[int] = mapped_column(Integer, nullable=False)
    session_generation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default="ACTIVE"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rotated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
