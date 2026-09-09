from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import ROOT_DIR
from app.core.security import hash_password
from app.models import AuditEvent, BillingUnit, Permission, Role, Service, Setting, User
from app.services.cash_validation import project_zone


OWNER_ROLE_CODE = "admin"
OWNER_ESSENTIAL_PERMISSIONS = frozenset(
    {"admin.overview.view", "admin.users", "admin.permissions", "admin.settings"}
)
COMPANY_SETTING_KEYS = (
    "company.name",
    "company.trade_name",
    "company.document",
    "company.phone",
    "company.email",
    "company.address.street",
    "company.address.number",
    "company.address.complement",
    "company.address.neighborhood",
    "company.address.city",
    "company.address.state",
    "company.address.cep",
    "company.logo",
    "company.timezone",
)


class AdminValidationError(ValueError):
    def __init__(self, message: str, *, errors: dict[str, str] | None = None):
        super().__init__(message)
        self.errors = errors or {"form": message}


class AdminConflictError(RuntimeError):
    pass


class AdminNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class BillingUnitRow:
    unit: BillingUnit
    service_count: int


def _text(value: object, maximum: int) -> str:
    return str(value or "").strip()[:maximum]


class AdminService:
    def __init__(self, session: Session):
        self.session = session

    def _begin_immediate(self) -> None:
        if self.session.in_transaction():
            # Rotas de escrita constroem o service antes de qualquer consulta.
            # Uma transação preexistente indica uso incorreto do contrato.
            raise RuntimeError("A operação administrativa deve iniciar uma transação exclusiva.")
        self.session.connection().exec_driver_sql("begin immediate")

    def _audit(self, actor_id: int, action: str, resource: str, details: dict | None = None) -> None:
        self.session.add(
            AuditEvent(
                user_id=actor_id,
                action=action,
                resource=resource,
                details=json.dumps(details, ensure_ascii=False, sort_keys=True) if details else None,
            )
        )

    def roles(self) -> list[Role]:
        return list(
            self.session.scalars(
                select(Role).options(selectinload(Role.permissions)).order_by(Role.name, Role.id)
            )
        )

    def users(self) -> list[User]:
        return list(
            self.session.scalars(
                select(User).options(selectinload(User.roles)).order_by(User.display_name, User.id)
            )
        )

    def user(self, user_id: int) -> User:
        user = self.session.scalar(
            select(User).where(User.id == user_id).options(selectinload(User.roles))
        )
        if user is None:
            raise AdminNotFoundError("Usuário não encontrado.")
        return user

    @staticmethod
    def _user_fields(raw: dict[str, object], *, require_password: bool) -> tuple[str, str, str | None]:
        errors: dict[str, str] = {}
        email = _text(raw.get("email"), 180).lower()
        display_name = _text(raw.get("display_name"), 120)
        password = str(raw.get("password") or "")
        if not re.fullmatch(r"[^\s]{3,180}", email):
            errors["email"] = "Informe um login ou e-mail válido, sem espaços."
        if len(display_name) < 2:
            errors["display_name"] = "Informe um nome com pelo menos 2 caracteres."
        if require_password and len(password) < 8:
            errors["password"] = "A senha deve ter pelo menos 8 caracteres."
        if len(password) > 256:
            errors["password"] = "A senha excede o limite permitido."
        if errors:
            raise AdminValidationError("Revise os campos informados.", errors=errors)
        return email, display_name, password or None

    def _resolve_roles(self, role_codes: set[str]) -> list[Role]:
        if not role_codes:
            raise AdminValidationError("Selecione ao menos um papel.", errors={"roles": "Selecione ao menos um papel."})
        roles = list(self.session.scalars(
            select(Role)
            .where(Role.code.in_(role_codes))
            .options(selectinload(Role.permissions))
        ))
        if {role.code for role in roles} != role_codes:
            raise AdminValidationError("Um dos papéis informados não existe.")
        return roles

    def _actor_access(self, actor_id: int) -> tuple[bool, frozenset[str]]:
        actor = self.session.scalar(
            select(User)
            .where(User.id == actor_id, User.active.is_(True))
            .options(selectinload(User.roles).selectinload(Role.permissions))
        )
        if actor is None:
            raise AdminConflictError("O autor da operação não possui um acesso ativo.")
        role_codes = {role.code for role in actor.roles}
        permissions = frozenset(
            permission.code
            for role in actor.roles
            for permission in role.permissions
        )
        return OWNER_ROLE_CODE in role_codes, permissions

    @staticmethod
    def _role_permissions(roles: list[Role]) -> frozenset[str]:
        return frozenset(
            permission.code
            for role in roles
            for permission in role.permissions
        )

    def _authorize_role_assignment(
        self,
        roles: list[Role],
        *,
        actor_is_owner: bool,
        actor_permissions: frozenset[str],
    ) -> None:
        if actor_is_owner:
            return
        if any(role.code == OWNER_ROLE_CODE for role in roles):
            raise AdminConflictError(
                "Somente um Proprietário pode atribuir o papel Proprietário."
            )
        if not self._role_permissions(roles) <= actor_permissions:
            raise AdminConflictError(
                "Você não pode atribuir um papel com permissões superiores às suas."
            )

    def _authorize_user_target(
        self,
        user: User,
        *,
        actor_is_owner: bool,
        actor_permissions: frozenset[str],
    ) -> None:
        if actor_is_owner:
            return
        if any(role.code == OWNER_ROLE_CODE for role in user.roles):
            raise AdminConflictError(
                "Somente um Proprietário pode alterar outro Proprietário."
            )
        if not self._role_permissions(list(user.roles)) <= actor_permissions:
            raise AdminConflictError(
                "Você não pode alterar um usuário com permissões superiores às suas."
            )

    def _active_owner_count(self) -> int:
        return int(
            self.session.scalar(
                select(func.count(func.distinct(User.id)))
                .join(User.roles)
                .where(User.active.is_(True), Role.code == OWNER_ROLE_CODE)
            )
            or 0
        )

    def create_user(
        self, raw: dict[str, object], role_codes: set[str], actor_id: int
    ) -> User:
        self._begin_immediate()
        try:
            actor_is_owner, actor_permissions = self._actor_access(actor_id)
            email, display_name, password = self._user_fields(raw, require_password=True)
            roles = self._resolve_roles(role_codes)
            self._authorize_role_assignment(
                roles,
                actor_is_owner=actor_is_owner,
                actor_permissions=actor_permissions,
            )
            if self.session.scalar(select(User.id).where(func.lower(User.email) == email)) is not None:
                raise AdminConflictError("Já existe um usuário com este login ou e-mail.")
            user = User(
                email=email,
                display_name=display_name,
                password_hash=hash_password(password or ""),
                active=True,
                roles=roles,
            )
            self.session.add(user)
            self.session.flush()
            self._audit(actor_id, "admin.user_created", f"users/{user.id}", {"roles": sorted(role_codes)})
            self.session.commit()
            return user
        except Exception:
            self.session.rollback()
            raise

    def update_user(
        self, user_id: int, raw: dict[str, object], role_codes: set[str], actor_id: int
    ) -> User:
        self._begin_immediate()
        try:
            actor_is_owner, actor_permissions = self._actor_access(actor_id)
            user = self.user(user_id)
            self._authorize_user_target(
                user,
                actor_is_owner=actor_is_owner,
                actor_permissions=actor_permissions,
            )
            email, display_name, _password = self._user_fields(raw, require_password=False)
            roles = self._resolve_roles(role_codes)
            self._authorize_role_assignment(
                roles,
                actor_is_owner=actor_is_owner,
                actor_permissions=actor_permissions,
            )
            duplicate = self.session.scalar(
                select(User.id).where(func.lower(User.email) == email, User.id != user_id)
            )
            if duplicate is not None:
                raise AdminConflictError("Já existe um usuário com este login ou e-mail.")
            before_roles = {role.code for role in user.roles}
            after_roles = {role.code for role in roles}
            removing_owner = OWNER_ROLE_CODE in before_roles and OWNER_ROLE_CODE not in after_roles
            if removing_owner and (actor_id == user.id or self._active_owner_count() <= 1):
                raise AdminConflictError("Não é permitido retirar o último acesso de Proprietário.")
            changes = {
                "email": [user.email, email],
                "display_name": [user.display_name, display_name],
                "roles": [sorted(before_roles), sorted(after_roles)],
            }
            user.email = email
            user.display_name = display_name
            user.roles = roles
            self._audit(actor_id, "admin.user_updated", f"users/{user.id}", changes)
            self.session.commit()
            return user
        except Exception:
            self.session.rollback()
            raise

    def set_user_active(self, user_id: int, active: bool, actor_id: int) -> User:
        self._begin_immediate()
        try:
            actor_is_owner, actor_permissions = self._actor_access(actor_id)
            user = self.user(user_id)
            self._authorize_user_target(
                user,
                actor_is_owner=actor_is_owner,
                actor_permissions=actor_permissions,
            )
            is_owner = OWNER_ROLE_CODE in {role.code for role in user.roles}
            if not active and user.active and is_owner:
                if actor_id == user.id:
                    raise AdminConflictError("Você não pode inativar o próprio acesso de Proprietário.")
                if self._active_owner_count() <= 1:
                    raise AdminConflictError("O último Proprietário ativo não pode ser inativado.")
            user.active = active
            self._audit(
                actor_id,
                "admin.user_reactivated" if active else "admin.user_deactivated",
                f"users/{user.id}",
            )
            self.session.commit()
            return user
        except Exception:
            self.session.rollback()
            raise

    def reset_password(self, user_id: int, password: str, actor_id: int) -> None:
        self._begin_immediate()
        try:
            actor_is_owner, actor_permissions = self._actor_access(actor_id)
            user = self.user(user_id)
            self._authorize_user_target(
                user,
                actor_is_owner=actor_is_owner,
                actor_permissions=actor_permissions,
            )
            if not 8 <= len(password) <= 256:
                raise AdminValidationError("A nova senha deve ter entre 8 e 256 caracteres.")
            user.password_hash = hash_password(password)
            self._audit(actor_id, "admin.user_password_reset", f"users/{user.id}")
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def update_role_permissions(
        self, role_id: int, permission_codes: set[str], actor_id: int
    ) -> Role:
        self._begin_immediate()
        try:
            actor_is_owner, actor_permissions = self._actor_access(actor_id)
            role = self.session.scalar(
                select(Role).where(Role.id == role_id).options(selectinload(Role.permissions))
            )
            if role is None:
                raise AdminNotFoundError("Papel não encontrado.")
            permissions = list(
                self.session.scalars(select(Permission).where(Permission.code.in_(permission_codes)))
            ) if permission_codes else []
            if {item.code for item in permissions} != permission_codes:
                raise AdminValidationError("Uma das permissões informadas não existe.")
            if not actor_is_owner:
                if role.code == OWNER_ROLE_CODE:
                    raise AdminConflictError(
                        "Somente um Proprietário pode alterar as permissões de Proprietário."
                    )
                current_permissions = {item.code for item in role.permissions}
                if not current_permissions <= actor_permissions:
                    raise AdminConflictError(
                        "Você não pode alterar um papel com permissões superiores às suas."
                    )
                if not permission_codes <= actor_permissions:
                    raise AdminConflictError(
                        "Você não pode conceder permissões superiores às suas."
                    )
            if role.code == OWNER_ROLE_CODE and not OWNER_ESSENTIAL_PERMISSIONS <= permission_codes:
                raise AdminConflictError(
                    "O papel Proprietário deve manter as permissões administrativas essenciais."
                )
            before = sorted(item.code for item in role.permissions)
            role.permissions = permissions
            self._audit(
                actor_id,
                "admin.role_permissions_updated",
                f"roles/{role.id}",
                {"before": before, "after": sorted(permission_codes)},
            )
            self.session.commit()
            return role
        except Exception:
            self.session.rollback()
            raise

    def billing_units(self) -> list[BillingUnitRow]:
        rows = self.session.execute(
            select(BillingUnit, func.count(Service.id))
            .outerjoin(Service, Service.billing_unit_id == BillingUnit.id)
            .group_by(BillingUnit.id)
            .order_by(BillingUnit.display_order, BillingUnit.name, BillingUnit.id)
        ).all()
        return [BillingUnitRow(unit=row[0], service_count=int(row[1])) for row in rows]

    @staticmethod
    def _billing_unit_fields(raw: dict[str, object], *, creating: bool) -> dict[str, object]:
        errors: dict[str, str] = {}
        code = _text(raw.get("code"), 40).upper()
        name = _text(raw.get("name"), 120)
        symbol = _text(raw.get("symbol"), 20)
        behavior = _text(raw.get("quantity_behavior"), 16).upper()
        try:
            places = int(str(raw.get("decimal_places", "0")))
        except ValueError:
            places = -1
        try:
            order = int(str(raw.get("display_order", "0")))
        except ValueError:
            order = -1
        if creating and not re.fullmatch(r"[A-Z0-9_]{1,40}", code):
            errors["code"] = "Use 1 a 40 letras maiúsculas, números ou sublinhado."
        if not name:
            errors["name"] = "Informe o nome da unidade."
        if not symbol:
            errors["symbol"] = "Informe o símbolo da unidade."
        if behavior not in {"INTEGER", "DECIMAL", "FIXED_ONE"}:
            errors["quantity_behavior"] = "Selecione um comportamento válido."
        if (behavior in {"INTEGER", "FIXED_ONE"} and places != 0) or (
            behavior == "DECIMAL" and not 1 <= places <= 6
        ):
            errors["decimal_places"] = "A precisão deve corresponder ao comportamento escolhido."
        if not 0 <= order <= 9999:
            errors["display_order"] = "Informe uma ordem entre 0 e 9999."
        if errors:
            raise AdminValidationError("Revise os campos da unidade.", errors=errors)
        return {
            "code": code,
            "name": name,
            "symbol": symbol,
            "quantity_behavior": behavior,
            "decimal_places": places,
            "display_order": order,
            "is_active": str(raw.get("is_active", "")) == "1",
        }

    def create_billing_unit(self, raw: dict[str, object], actor_id: int) -> BillingUnit:
        fields = self._billing_unit_fields(raw, creating=True)
        self._begin_immediate()
        try:
            if self.session.scalar(select(BillingUnit.id).where(BillingUnit.code == fields["code"])):
                raise AdminConflictError("Já existe uma unidade com este código.")
            unit = BillingUnit(**fields)
            self.session.add(unit)
            self.session.flush()
            self._audit(actor_id, "admin.billing_unit_created", f"billing_units/{unit.id}", {"code": unit.code})
            self.session.commit()
            return unit
        except IntegrityError:
            self.session.rollback()
            raise AdminConflictError("Não foi possível criar a unidade com esses dados.") from None
        except Exception:
            self.session.rollback()
            raise

    def update_billing_unit(
        self, unit_id: int, raw: dict[str, object], actor_id: int
    ) -> BillingUnit:
        fields = self._billing_unit_fields(raw, creating=False)
        fields.pop("code")
        self._begin_immediate()
        try:
            unit = self.session.get(BillingUnit, unit_id)
            if unit is None:
                raise AdminNotFoundError("Unidade de cobrança não encontrada.")
            before = {key: getattr(unit, key) for key in fields}
            for key, value in fields.items():
                setattr(unit, key, value)
            unit.updated_at = datetime.now(timezone.utc)
            self._audit(
                actor_id,
                "admin.billing_unit_updated",
                f"billing_units/{unit.id}",
                {"code": unit.code, "before": before, "after": fields},
            )
            self.session.commit()
            return unit
        except Exception:
            self.session.rollback()
            raise

    def set_billing_unit_active(self, unit_id: int, active: bool, actor_id: int) -> BillingUnit:
        self._begin_immediate()
        try:
            unit = self.session.get(BillingUnit, unit_id)
            if unit is None:
                raise AdminNotFoundError("Unidade de cobrança não encontrada.")
            unit.is_active = active
            unit.updated_at = datetime.now(timezone.utc)
            self._audit(
                actor_id,
                "admin.billing_unit_reactivated" if active else "admin.billing_unit_deactivated",
                f"billing_units/{unit.id}",
                {"code": unit.code},
            )
            self.session.commit()
            return unit
        except Exception:
            self.session.rollback()
            raise

    def company_settings(self) -> dict[str, str]:
        persisted = {
            row.key: row.value
            for row in self.session.scalars(select(Setting).where(Setting.key.in_(COMPANY_SETTING_KEYS)))
        }
        return {key: persisted.get(key, "") for key in COMPANY_SETTING_KEYS}

    @staticmethod
    def _company_fields(raw: dict[str, object]) -> dict[str, str]:
        limits = {
            "company.name": 180,
            "company.trade_name": 180,
            "company.document": 18,
            "company.phone": 20,
            "company.email": 180,
            "company.address.street": 180,
            "company.address.number": 30,
            "company.address.complement": 100,
            "company.address.neighborhood": 100,
            "company.address.city": 100,
            "company.address.state": 2,
            "company.address.cep": 9,
            "company.logo": 240,
            "company.timezone": 80,
        }
        values = {key: _text(raw.get(key), limit) for key, limit in limits.items()}
        errors: dict[str, str] = {}
        if not values["company.name"]:
            errors["company.name"] = "Informe a razão social ou nome da empresa."
        document = re.sub(r"\D", "", values["company.document"])
        if document and len(document) not in {11, 14}:
            errors["company.document"] = "Informe CPF ou CNPJ com 11 ou 14 dígitos."
        values["company.document"] = document
        email = values["company.email"]
        if email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            errors["company.email"] = "Informe um e-mail válido."
        state = values["company.address.state"].upper()
        if state and not re.fullmatch(r"[A-Z]{2}", state):
            errors["company.address.state"] = "Use a sigla da UF com duas letras."
        values["company.address.state"] = state
        logo = values["company.logo"] or "/static/img/logo-placeholder.svg"
        logo_parts = Path(logo).parts
        if (
            not logo.startswith("/static/")
            or "\\" in logo
            or "://" in logo
            or "?" in logo
            or "#" in logo
            or ".." in logo_parts
        ):
            errors["company.logo"] = "O logo deve apontar para um arquivo local em /static/."
        else:
            target = (ROOT_DIR / logo.lstrip("/")).resolve()
            static_root = (ROOT_DIR / "static").resolve()
            if static_root not in target.parents or not target.is_file():
                errors["company.logo"] = "O arquivo de logo local não foi encontrado."
        values["company.logo"] = logo
        timezone_name = values["company.timezone"] or "America/Sao_Paulo"
        try:
            project_zone(timezone_name)
        except (RuntimeError, ValueError):
            errors["company.timezone"] = "Informe um fuso horário IANA válido."
        values["company.timezone"] = timezone_name
        if errors:
            raise AdminValidationError("Revise os dados da empresa.", errors=errors)
        return values

    def update_company(self, raw: dict[str, object], actor_id: int) -> dict[str, str]:
        fields = self._company_fields(raw)
        self._begin_immediate()
        try:
            before = {}
            for key, value in fields.items():
                setting = self.session.get(Setting, key)
                before[key] = setting.value if setting else ""
                if setting is None:
                    self.session.add(Setting(key=key, value=value))
                else:
                    setting.value = value
            changed = sorted(key for key in fields if before.get(key) != fields[key])
            self._audit(actor_id, "admin.company_updated", "settings/company", {"keys": changed})
            self.session.commit()
            return fields
        except Exception:
            self.session.rollback()
            raise
