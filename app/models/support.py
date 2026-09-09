from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.auth import User


SUPPORT_GRANT_STATUSES = ("SCHEDULED", "ACTIVE", "EXPIRED", "REVOKED")


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class SupportGrant(Base):
    __tablename__ = "support_grants"
    __table_args__ = (
        CheckConstraint(
            "length(grant_uid) = 36 and grant_uid glob "
            "'[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
            "[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
            "[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
            "[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
            "[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]"
            "[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'",
            name="ck_support_grants_uid",
        ),
        CheckConstraint(
            "expires_at > starts_at",
            name="ck_support_grants_window",
        ),
        CheckConstraint(
            "length(trim(purpose)) between 1 and 500",
            name="ck_support_grants_purpose",
        ),
        CheckConstraint(
            "(revoked_at is null and revoked_by is null and revocation_reason is null) or "
            "(revoked_at is not null and revoked_by is not null and "
            "revocation_reason is not null and "
            "length(trim(revocation_reason)) between 1 and 500)",
            name="ck_support_grants_revocation",
        ),
        UniqueConstraint("grant_uid", name="uq_support_grants_uid"),
        Index("ix_support_grants_support_user_id", "support_user_id"),
        Index("ix_support_grants_authorized_by", "authorized_by"),
        Index("ix_support_grants_revoked_by", "revoked_by"),
        Index("ix_support_grants_window", "starts_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    grant_uid: Mapped[str] = mapped_column(String(36), default=lambda: str(uuid4()))
    support_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    authorized_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    purpose: Mapped[str] = mapped_column(String(500))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    revocation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    support_user: Mapped[User] = relationship(foreign_keys=[support_user_id], lazy="joined")
    authorizer: Mapped[User] = relationship(foreign_keys=[authorized_by], lazy="joined")
    revoker: Mapped[User | None] = relationship(foreign_keys=[revoked_by], lazy="joined")

    def effective_status(self, at: datetime | None = None) -> str:
        instant = _utc_naive(at or datetime.now(timezone.utc))
        if self.revoked_at is not None:
            return "REVOKED"
        if instant < _utc_naive(self.starts_at):
            return "SCHEDULED"
        if instant >= _utc_naive(self.expires_at):
            return "EXPIRED"
        return "ACTIVE"
