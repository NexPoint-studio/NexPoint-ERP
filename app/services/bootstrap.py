from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.database import Base
from app.core.permissions import ALL_PERMISSIONS
from app.core.security import hash_password
from app.migrations import run_schema_migrations
from app.models import FeatureFlag, Permission, Role, Setting, User


ROLE_PERMISSIONS = {
    "admin": set(ALL_PERMISSIONS),
    "user": {
        "cash.view", "cash.create", "customers.view", "customers.create", "customers.edit",
        "customers.deactivate", "customers.activity.create", "services.view", "reports.view",
    },
    "delivery": set(),
}

FEATURE_DEFAULTS = {"cash": True, "customers": True, "services": True}


def initialize_database(
    engine,
    factory: sessionmaker[Session],
    credentials: dict[str, str],
    settings: Settings,
) -> None:
    domain_tables = {
        "customers", "customer_addresses", "customer_activities",
        "service_categories", "services", "service_prices",
        "cash_categories", "cash_payment_methods", "cash_movements",
    }
    infrastructure = [table for name, table in Base.metadata.tables.items() if name not in domain_tables]
    Base.metadata.create_all(engine, tables=infrastructure)
    run_schema_migrations(engine)
    with factory() as session:
        permissions = {}
        new_permission_codes: set[str] = set()
        for code in ALL_PERMISSIONS:
            row = session.scalar(select(Permission).where(Permission.code == code))
            if row is None:
                row = Permission(code=code)
                session.add(row)
                new_permission_codes.add(code)
            permissions[code] = row
        session.flush()

        roles = {}
        for code, label in {"admin": "Administrador", "user": "Usuário", "delivery": "Entrega"}.items():
            role = session.scalar(select(Role).where(Role.code == code))
            role_is_new = role is None
            if role is None:
                role = Role(code=code, name=label)
                session.add(role)
                session.flush()
            assigned = {item.code for item in role.permissions}
            # Um papel novo recebe sua matriz inicial completa. Em papéis já
            # existentes, apenas permissões criadas nesta mesma evolução são
            # acrescentadas. Assim, reiniciar o ERP nunca desfaz uma remoção
            # personalizada feita pelo administrador.
            defaults_to_add = ROLE_PERMISSIONS[code] if role_is_new else (
                ROLE_PERMISSIONS[code] & new_permission_codes
            )
            role.permissions.extend(
                permissions[item]
                for item in sorted(defaults_to_add)
                if item not in assigned
            )
            roles[code] = role
        session.flush()

        for login, password in credentials.items():
            role_code = "admin" if login in {"adm", "admin@local"} else ("delivery" if login.startswith("delivery") else "user")
            display_name = "Administrador local" if role_code == "admin" else ("Entrega local" if role_code == "delivery" else "Usuário local")
            user = session.scalar(select(User).where(User.email == login))
            if user is None:
                user = User(
                    email=login,
                    display_name=display_name,
                    active=True,
                    password_hash=hash_password(password),
                    roles=[roles[role_code]],
                )
                session.add(user)
            # Usuários existentes são dados persistentes, não seed. Hash,
            # status, nome e papéis devem sobreviver intactos a todo restart.

        defaults = {
            "app.name": settings.app_name,
            "app.version": settings.version,
            "company.name": settings.company_name,
            "company.logo": settings.logo_path,
            "company.timezone": settings.timezone,
            "company.currency": settings.currency,
            "customers.inactivity.recent_days": "30",
            "customers.inactivity.attention_days": "60",
            "customers.inactivity.distant_days": "90",
        }
        for key, value in defaults.items():
            if session.get(Setting, key) is None:
                session.add(Setting(key=key, value=value))
        for key, enabled in FEATURE_DEFAULTS.items():
            if session.get(FeatureFlag, key) is None:
                session.add(FeatureFlag(key=key, enabled=enabled))
        session.commit()

# DONE: seed contém somente metadados de infraestrutura e usuários locais genéricos.
