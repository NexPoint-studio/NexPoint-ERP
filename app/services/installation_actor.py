"""Non-login technical authorship, preserving the historical users foreign keys."""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.permissions import CurrentUser, INSTALLATION_CAPABILITIES
from app.models.auth import Setting, User

ACTOR_SETTING = "system.installation_actor_id"
DISABLED_PASSWORD = "!installation-actor-no-login"


def ensure_installation_actor(session: Session) -> User:
    """Run in the serialized bootstrap transaction; never reuse a human account."""
    binding = session.get(Setting, ACTOR_SETTING)
    if binding is not None:
        return installation_actor(session)
    actor = User(
        email=f"installation-{uuid4().hex}@local.invalid",
        display_name="Instalação local",
        password_hash=DISABLED_PASSWORD,
        active=True,
        roles=[],
    )
    session.add(actor)
    session.flush()
    session.add(Setting(key=ACTOR_SETTING, value=str(actor.id)))
    session.flush()
    return actor


def installation_actor(session: Session) -> User:
    binding = session.get(Setting, ACTOR_SETTING)
    if binding is None or not binding.value.isdecimal():
        raise RuntimeError("A identidade técnica local não está configurada.")
    actor = session.get(User, int(binding.value), populate_existing=True)
    if actor is None or not actor.active or actor.password_hash != DISABLED_PASSWORD or actor.roles:
        raise RuntimeError("A identidade técnica local está inconsistente.")
    return actor


def is_installation_actor(session: Session, actor_id: int) -> bool:
    binding = session.get(Setting, ACTOR_SETTING)
    if binding is None or binding.value != str(actor_id):
        return False
    return installation_actor(session).id == actor_id


def current_installation_actor(session: Session) -> CurrentUser:
    actor = installation_actor(session)
    return CurrentUser(
        id=actor.id, email="", display_name=actor.display_name,
        roles=frozenset(), permissions=INSTALLATION_CAPABILITIES,
        auth_version=actor.auth_version, installation_actor=True,
    )
