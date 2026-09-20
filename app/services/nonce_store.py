from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models.sync import NonceReceipt


_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class NonceStore:
    """SQLite replay protection. A unique peer/nonce pair survives restart."""

    def __init__(self, session_factory: sessionmaker[Session], *, ttl: timedelta = timedelta(minutes=10)):
        self.session_factory = session_factory
        self.ttl = max(timedelta(minutes=1), min(ttl, timedelta(days=1)))

    def accept(self, peer_id: str, nonce: str, *, issued_at: datetime,
               now: datetime | None = None, allowed_skew: timedelta = timedelta(minutes=5)) -> bool:
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=timezone.utc)
        if not _TOKEN.fullmatch(peer_id) or not _TOKEN.fullmatch(nonce):
            return False
        if abs((instant - issued_at).total_seconds()) > allowed_skew.total_seconds():
            return False
        with self.session_factory() as session:
            session.execute(delete(NonceReceipt).where(NonceReceipt.expires_at <= instant))
            session.add(NonceReceipt(peer_id=peer_id, nonce=nonce, received_at=instant,
                                     expires_at=instant + self.ttl))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return False
        return True

    def contains(self, peer_id: str, nonce: str, *, now: datetime | None = None) -> bool:
        instant = now or datetime.now(timezone.utc)
        with self.session_factory() as session:
            return session.scalar(select(NonceReceipt.id).where(
                NonceReceipt.peer_id == peer_id, NonceReceipt.nonce == nonce,
                NonceReceipt.expires_at > instant)) is not None
