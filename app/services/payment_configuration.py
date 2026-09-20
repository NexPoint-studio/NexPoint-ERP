from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.money import decimal_to_cents
from app.models import (
    AuditEvent,
    CashPaymentMethod,
    PaymentFeeRule,
    PaymentTerminal,
)
from app.services.cash_validation import (
    local_to_utc_naive,
    parse_money,
    utc_naive_to_local,
)
from app.services.authorization import require_active_actor_permission


METHOD_KINDS = (
    ("CASH", "Dinheiro"),
    ("PIX", "Pix"),
    ("CARD", "Cartão"),
    ("BOLETO", "Boleto"),
    ("OTHER", "Outro"),
)
METHOD_KIND_CODES = frozenset(code for code, _label in METHOD_KINDS)
CARD_MODES = (("DEBIT", "Débito"), ("CREDIT", "Crédito"))
CARD_MODE_CODES = frozenset(code for code, _label in CARD_MODES)
MAX_SORT_ORDER = 9999
MAX_INSTALLMENTS = 999
MAX_FEE_PERCENTAGE_SCALED = 1_000_000
_PERCENTAGE_PATTERN = re.compile(r"^(?:0|[1-9][0-9]{0,2})(?:[,.][0-9]{1,4})?$")
_TERMINAL_CODE_PATTERN = re.compile(r"^[A-Z0-9_]{1,40}$")


class PaymentConfigurationValidationError(ValueError):
    def __init__(self, message: str, *, errors: dict[str, str] | None = None):
        super().__init__(message)
        self.errors = errors or {"form": message}


class PaymentConfigurationConflictError(RuntimeError):
    pass


class PaymentConfigurationNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class FeeRuleInput:
    payment_method_id: int
    terminal_id: int | None
    card_mode: str | None
    installments: int | None
    fee_percentage_scaled: int
    fixed_fee_cents: int
    valid_from: datetime
    valid_until: datetime | None
    is_active: bool


def _required_text(value: object, maximum: int, field: str, label: str, errors: dict[str, str]) -> str:
    raw = str(value or "").strip()
    if not raw:
        errors[field] = f"Informe {label}."
        return ""
    if len(raw) > maximum:
        errors[field] = f"{label.capitalize()} excede {maximum} caracteres."
    return raw[:maximum]


def _integer(
    value: object,
    field: str,
    label: str,
    errors: dict[str, str],
    *,
    minimum: int,
    maximum: int,
    optional: bool = False,
) -> int | None:
    raw = str(value or "").strip()
    if optional and not raw:
        return None
    if not re.fullmatch(r"[0-9]+", raw):
        errors[field] = f"Informe {label} como número inteiro."
        return None
    parsed = int(raw)
    if not minimum <= parsed <= maximum:
        errors[field] = f"{label.capitalize()} deve ficar entre {minimum} e {maximum}."
        return None
    return parsed


def _checkbox(value: object, field: str, errors: dict[str, str]) -> bool:
    if value in (None, "", "0", 0, False):
        return False
    if value in ("1", 1, True):
        return True
    errors[field] = "Status inválido."
    return False


def _positive_id(value: object, field: str, label: str, errors: dict[str, str], *, optional: bool = False) -> int | None:
    return _integer(
        value,
        field,
        label,
        errors,
        minimum=1,
        maximum=2**63 - 1,
        optional=optional,
    )


def _parse_percentage(value: object, errors: dict[str, str]) -> int:
    raw = str(value or "").strip()
    if not _PERCENTAGE_PATTERN.fullmatch(raw):
        errors["fee_percentage"] = "Use um percentual entre 0 e 100 com até quatro casas decimais."
        return 0
    try:
        amount = Decimal(raw.replace(",", "."))
        scaled_decimal = amount * Decimal(10_000)
        scaled = int(scaled_decimal)
    except (InvalidOperation, ValueError, OverflowError):
        errors["fee_percentage"] = "Informe um percentual válido."
        return 0
    if scaled_decimal != Decimal(scaled) or not 0 <= scaled <= MAX_FEE_PERCENTAGE_SCALED:
        errors["fee_percentage"] = "Use um percentual entre 0 e 100 com até quatro casas decimais."
        return 0
    return scaled


def _parse_datetime(
    value: object,
    field: str,
    label: str,
    errors: dict[str, str],
    *,
    timezone_name: str,
    optional: bool = False,
) -> datetime | None:
    raw = str(value or "").strip()
    if optional and not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        errors[field] = f"Informe {label} válida."
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    try:
        return local_to_utc_naive(parsed, timezone_name)
    except (RuntimeError, ValueError):
        errors[field] = f"Informe {label} válida."
        return None


def format_fee_percentage(scaled: int) -> str:
    value = Decimal(int(scaled)) / Decimal(10_000)
    rendered = f"{value:.4f}".rstrip("0").rstrip(".")
    return rendered.replace(".", ",")


def datetime_local_value(value: datetime | None, timezone_name: str) -> str:
    if value is None:
        return ""
    return utc_naive_to_local(value, timezone_name).strftime("%Y-%m-%dT%H:%M")


class PaymentConfigurationService:
    def __init__(self, session: Session, timezone_name: str = "America/Sao_Paulo"):
        self.session = session
        self.timezone_name = timezone_name

    def _begin_immediate(self) -> None:
        if self.session.in_transaction():
            raise RuntimeError("A configuração financeira deve iniciar uma transação exclusiva.")
        self.session.connection().exec_driver_sql("begin immediate")

    def _begin_authorized_write(self, actor_id: int) -> None:
        self._begin_immediate()
        try:
            require_active_actor_permission(self.session, actor_id, "finance.config.manage")
        except Exception:
            self.session.rollback()
            raise

    def _audit(self, actor_id: int, action: str, resource: str, details: dict | None = None) -> None:
        self.session.add(
            AuditEvent(
                user_id=actor_id,
                action=action,
                resource=resource,
                details=json.dumps(details, ensure_ascii=False, sort_keys=True) if details else None,
            )
        )

    def payment_methods(self) -> list[CashPaymentMethod]:
        return list(
            self.session.scalars(
                select(CashPaymentMethod).order_by(
                    CashPaymentMethod.sort_order, CashPaymentMethod.name, CashPaymentMethod.id
                )
            )
        )

    def terminals(self) -> list[PaymentTerminal]:
        return list(
            self.session.scalars(
                select(PaymentTerminal).order_by(
                    PaymentTerminal.sort_order, PaymentTerminal.name, PaymentTerminal.id
                )
            )
        )

    def fee_rules(self) -> list[PaymentFeeRule]:
        return list(
            self.session.scalars(
                select(PaymentFeeRule)
                .options(
                    selectinload(PaymentFeeRule.payment_method),
                    selectinload(PaymentFeeRule.terminal),
                )
                .order_by(PaymentFeeRule.valid_from.desc(), PaymentFeeRule.id.desc())
            )
        )

    def payment_method(self, method_id: int) -> CashPaymentMethod:
        row = self.session.get(CashPaymentMethod, method_id)
        if row is None:
            raise PaymentConfigurationNotFoundError("Forma de pagamento não encontrada.")
        return row

    def terminal(self, terminal_id: int) -> PaymentTerminal:
        row = self.session.get(PaymentTerminal, terminal_id)
        if row is None:
            raise PaymentConfigurationNotFoundError("Terminal não encontrado.")
        return row

    def fee_rule(self, rule_id: int) -> PaymentFeeRule:
        row = self.session.scalar(
            select(PaymentFeeRule)
            .where(PaymentFeeRule.id == rule_id)
            .options(
                selectinload(PaymentFeeRule.payment_method),
                selectinload(PaymentFeeRule.terminal),
            )
        )
        if row is None:
            raise PaymentConfigurationNotFoundError("Regra de taxa não encontrada.")
        return row

    @staticmethod
    def _method_values(raw: dict[str, object]) -> tuple[str, str, int, bool]:
        errors: dict[str, str] = {}
        name = _required_text(raw.get("name"), 80, "name", "o nome", errors)
        method_kind = str(raw.get("method_kind") or "").strip().upper()
        if method_kind not in METHOD_KIND_CODES:
            errors["method_kind"] = "Selecione um tipo de forma permitido."
            method_kind = "OTHER"
        sort_order = _integer(
            raw.get("sort_order"), "sort_order", "a ordem", errors,
            minimum=0, maximum=MAX_SORT_ORDER,
        )
        active = _checkbox(raw.get("is_active"), "is_active", errors)
        if errors:
            raise PaymentConfigurationValidationError("Revise a forma de pagamento.", errors=errors)
        return name, method_kind, int(sort_order), active

    def _method_name_conflict(self, name: str, *, exclude_id: int | None = None) -> bool:
        query = select(CashPaymentMethod.id).where(func.casefold(CashPaymentMethod.name) == name.casefold())
        if exclude_id is not None:
            query = query.where(CashPaymentMethod.id != exclude_id)
        return self.session.scalar(query) is not None

    def create_payment_method(self, raw: dict[str, object], actor_id: int) -> CashPaymentMethod:
        self._begin_authorized_write(actor_id)
        try:
            name, method_kind, sort_order, active = self._method_values(raw)
            if self._method_name_conflict(name):
                raise PaymentConfigurationConflictError("Já existe uma forma de pagamento com esse nome.")
            row = CashPaymentMethod(
                name=name,
                method_kind=method_kind,
                sort_order=sort_order,
                is_active=active,
            )
            self.session.add(row)
            self.session.flush()
            self._audit(actor_id, "finance.payment_method_created", f"cash_payment_methods/{row.id}", {
                "name": name, "method_kind": method_kind, "sort_order": sort_order,
                "is_active": active,
            })
            self.session.commit()
            return row
        except IntegrityError as exc:
            self.session.rollback()
            raise PaymentConfigurationConflictError("Não foi possível criar a forma de pagamento.") from exc
        except Exception:
            self.session.rollback()
            raise

    def update_payment_method(self, method_id: int, raw: dict[str, object], actor_id: int) -> CashPaymentMethod:
        self._begin_authorized_write(actor_id)
        try:
            row = self.payment_method(method_id)
            name, method_kind, sort_order, active = self._method_values(raw)
            if self._method_name_conflict(name, exclude_id=row.id):
                raise PaymentConfigurationConflictError("Já existe uma forma de pagamento com esse nome.")
            before = {
                "name": row.name,
                "method_kind": row.method_kind,
                "sort_order": row.sort_order,
                "is_active": row.is_active,
            }
            row.name = name
            row.method_kind = method_kind
            row.sort_order = sort_order
            row.is_active = active
            self._audit(actor_id, "finance.payment_method_updated", f"cash_payment_methods/{row.id}", {
                "before": before,
                "after": {
                    "name": name, "method_kind": method_kind,
                    "sort_order": sort_order, "is_active": active,
                },
            })
            self.session.commit()
            return row
        except IntegrityError as exc:
            self.session.rollback()
            raise PaymentConfigurationConflictError("Não foi possível atualizar a forma de pagamento.") from exc
        except Exception:
            self.session.rollback()
            raise

    def set_payment_method_active(self, method_id: int, active: bool, actor_id: int) -> CashPaymentMethod:
        self._begin_authorized_write(actor_id)
        try:
            row = self.payment_method(method_id)
            if row.is_active != active:
                row.is_active = active
                self._audit(
                    actor_id,
                    "finance.payment_method_activated" if active else "finance.payment_method_deactivated",
                    f"cash_payment_methods/{row.id}",
                    {"is_active": active},
                )
            self.session.commit()
            return row
        except Exception:
            self.session.rollback()
            raise

    @staticmethod
    def _terminal_values(raw: dict[str, object]) -> tuple[str, str, str | None, int, bool]:
        errors: dict[str, str] = {}
        code = str(raw.get("code") or "").strip().upper()
        if not _TERMINAL_CODE_PATTERN.fullmatch(code):
            errors["code"] = "Use de 1 a 40 caracteres: A-Z, 0-9 ou sublinhado."
        name = _required_text(raw.get("name"), 120, "name", "o nome", errors)
        raw_description = str(raw.get("description") or "").strip()
        if len(raw_description) > 300:
            errors["description"] = "A descrição excede 300 caracteres."
        description = raw_description[:300] or None
        sort_order = _integer(
            raw.get("sort_order"), "sort_order", "a ordem", errors,
            minimum=0, maximum=MAX_SORT_ORDER,
        )
        active = _checkbox(raw.get("is_active"), "is_active", errors)
        if errors:
            raise PaymentConfigurationValidationError("Revise o terminal.", errors=errors)
        return code, name, description, int(sort_order), active

    def _terminal_code_conflict(self, code: str, *, exclude_id: int | None = None) -> bool:
        query = select(PaymentTerminal.id).where(PaymentTerminal.code == code)
        if exclude_id is not None:
            query = query.where(PaymentTerminal.id != exclude_id)
        return self.session.scalar(query) is not None

    def create_terminal(self, raw: dict[str, object], actor_id: int) -> PaymentTerminal:
        self._begin_authorized_write(actor_id)
        try:
            code, name, description, sort_order, active = self._terminal_values(raw)
            if self._terminal_code_conflict(code):
                raise PaymentConfigurationConflictError("Já existe um terminal com esse código.")
            row = PaymentTerminal(
                code=code,
                name=name,
                description=description,
                sort_order=sort_order,
                is_active=active,
                created_by=actor_id,
                updated_by=actor_id,
            )
            self.session.add(row)
            self.session.flush()
            self._audit(actor_id, "finance.payment_terminal_created", f"payment_terminals/{row.id}", {
                "code": code, "name": name, "sort_order": sort_order, "is_active": active,
            })
            self.session.commit()
            return row
        except IntegrityError as exc:
            self.session.rollback()
            raise PaymentConfigurationConflictError("Não foi possível criar o terminal.") from exc
        except Exception:
            self.session.rollback()
            raise

    def update_terminal(self, terminal_id: int, raw: dict[str, object], actor_id: int) -> PaymentTerminal:
        self._begin_authorized_write(actor_id)
        try:
            row = self.terminal(terminal_id)
            code, name, description, sort_order, active = self._terminal_values(raw)
            if self._terminal_code_conflict(code, exclude_id=row.id):
                raise PaymentConfigurationConflictError("Já existe um terminal com esse código.")
            before = {
                "code": row.code, "name": row.name, "description": row.description,
                "sort_order": row.sort_order, "is_active": row.is_active,
            }
            row.code = code
            row.name = name
            row.description = description
            row.sort_order = sort_order
            row.is_active = active
            row.updated_by = actor_id
            self._audit(actor_id, "finance.payment_terminal_updated", f"payment_terminals/{row.id}", {
                "before": before,
                "after": {
                    "code": code, "name": name, "description": description,
                    "sort_order": sort_order, "is_active": active,
                },
            })
            self.session.commit()
            return row
        except IntegrityError as exc:
            self.session.rollback()
            raise PaymentConfigurationConflictError("Não foi possível atualizar o terminal.") from exc
        except Exception:
            self.session.rollback()
            raise

    def set_terminal_active(self, terminal_id: int, active: bool, actor_id: int) -> PaymentTerminal:
        self._begin_authorized_write(actor_id)
        try:
            row = self.terminal(terminal_id)
            if row.is_active != active:
                row.is_active = active
                row.updated_by = actor_id
                self._audit(
                    actor_id,
                    "finance.payment_terminal_activated" if active else "finance.payment_terminal_deactivated",
                    f"payment_terminals/{row.id}",
                    {"is_active": active},
                )
            self.session.commit()
            return row
        except Exception:
            self.session.rollback()
            raise

    def _fee_rule_values(
        self,
        raw: dict[str, object],
        *,
        forced_valid_from: datetime | None = None,
    ) -> FeeRuleInput:
        errors: dict[str, str] = {}
        method_id = _positive_id(raw.get("payment_method_id"), "payment_method_id", "a forma de pagamento", errors)
        terminal_id = _positive_id(raw.get("terminal_id"), "terminal_id", "o terminal", errors, optional=True)
        card_mode_raw = str(raw.get("card_mode") or "").strip().upper()
        card_mode = card_mode_raw or None
        if card_mode is not None and card_mode not in CARD_MODE_CODES:
            errors["card_mode"] = "Selecione Débito ou Crédito."
            card_mode = None
        installments = _integer(
            raw.get("installments"), "installments", "as parcelas", errors,
            minimum=1, maximum=MAX_INSTALLMENTS, optional=True,
        )
        percentage = _parse_percentage(raw.get("fee_percentage"), errors)
        try:
            fixed_fee = parse_money(
                raw.get("fixed_fee") or "0", allow_zero=True, label="valor fixo da taxa"
            )
            fixed_fee_cents = decimal_to_cents(fixed_fee)
        except (TypeError, ValueError, OverflowError) as exc:
            errors["fixed_fee"] = str(exc)
            fixed_fee_cents = 0
        valid_from = forced_valid_from or _parse_datetime(
            raw.get("valid_from"), "valid_from", "uma vigência inicial", errors,
            timezone_name=self.timezone_name,
        )
        valid_until = _parse_datetime(
            raw.get("valid_until"), "valid_until", "uma vigência final", errors,
            timezone_name=self.timezone_name, optional=True,
        )
        active = _checkbox(raw.get("is_active"), "is_active", errors)

        method = self.session.get(CashPaymentMethod, method_id) if method_id is not None else None
        if method_id is not None and method is None:
            errors["payment_method_id"] = "A forma de pagamento selecionada não existe."
        terminal = self.session.get(PaymentTerminal, terminal_id) if terminal_id is not None else None
        if terminal_id is not None and terminal is None:
            errors["terminal_id"] = "O terminal selecionado não existe."

        if method is not None and method.method_kind == "CARD":
            if card_mode is None:
                errors["card_mode"] = "Informe a modalidade do cartão."
            elif card_mode == "DEBIT" and installments is not None:
                errors["installments"] = "Débito não aceita quantidade de parcelas na regra."
        elif method is not None:
            if terminal_id is not None:
                errors["terminal_id"] = "Terminal somente pode ser usado em forma do tipo Cartão."
            if card_mode is not None:
                errors["card_mode"] = "Modalidade somente pode ser usada em forma do tipo Cartão."
            if installments is not None:
                errors["installments"] = "Parcelas somente podem ser usadas em forma do tipo Cartão."

        if valid_from is not None and valid_until is not None and valid_until <= valid_from:
            errors["valid_until"] = "A vigência final deve ser posterior à inicial."
        if not active and valid_until is not None:
            errors["valid_until"] = (
                "Uma regra criada inativa não pode ter vigência final; ative-a quando for usar."
            )
        if errors:
            raise PaymentConfigurationValidationError("Revise a regra de taxa.", errors=errors)
        return FeeRuleInput(
            payment_method_id=int(method_id),
            terminal_id=terminal_id,
            card_mode=card_mode,
            installments=installments,
            fee_percentage_scaled=percentage,
            fixed_fee_cents=fixed_fee_cents,
            valid_from=valid_from,
            valid_until=valid_until,
            is_active=active,
        )

    @staticmethod
    def _identity_conditions(values: FeeRuleInput):
        return (
            PaymentFeeRule.payment_method_id == values.payment_method_id,
            PaymentFeeRule.terminal_id.is_(None) if values.terminal_id is None else PaymentFeeRule.terminal_id == values.terminal_id,
            PaymentFeeRule.card_mode.is_(None) if values.card_mode is None else PaymentFeeRule.card_mode == values.card_mode,
            PaymentFeeRule.installments.is_(None) if values.installments is None else PaymentFeeRule.installments == values.installments,
        )

    def _active_overlap(self, values: FeeRuleInput, *, exclude_id: int | None = None) -> PaymentFeeRule | None:
        if not values.is_active:
            return None
        conditions = [
            *self._identity_conditions(values),
            or_(
                PaymentFeeRule.is_active.is_(True),
                PaymentFeeRule.valid_until.is_not(None),
            ),
            or_(PaymentFeeRule.valid_until.is_(None), PaymentFeeRule.valid_until > values.valid_from),
        ]
        if values.valid_until is not None:
            conditions.append(PaymentFeeRule.valid_from < values.valid_until)
        query = select(PaymentFeeRule).where(*conditions)
        if exclude_id is not None:
            query = query.where(PaymentFeeRule.id != exclude_id)
        return self.session.scalar(query.limit(1))

    @staticmethod
    def _new_rule(values: FeeRuleInput, actor_id: int) -> PaymentFeeRule:
        return PaymentFeeRule(
            payment_method_id=values.payment_method_id,
            terminal_id=values.terminal_id,
            card_mode=values.card_mode,
            installments=values.installments,
            fee_percentage_scaled=values.fee_percentage_scaled,
            fixed_fee_cents=values.fixed_fee_cents,
            valid_from=values.valid_from,
            valid_until=values.valid_until,
            is_active=values.is_active,
            created_by=actor_id,
            updated_by=actor_id,
        )

    def create_fee_rule(self, raw: dict[str, object], actor_id: int) -> PaymentFeeRule:
        self._begin_authorized_write(actor_id)
        try:
            values = self._fee_rule_values(raw)
            if self._active_overlap(values) is not None:
                raise PaymentConfigurationConflictError(
                    "Já existe uma regra ativa sobreposta para essa forma, terminal, modalidade e parcelas."
                )
            row = self._new_rule(values, actor_id)
            self.session.add(row)
            self.session.flush()
            self._audit(actor_id, "finance.payment_fee_rule_created", f"payment_fee_rules/{row.id}", {
                "payment_method_id": values.payment_method_id,
                "terminal_id": values.terminal_id,
                "card_mode": values.card_mode,
                "installments": values.installments,
                "fee_percentage_scaled": values.fee_percentage_scaled,
                "fixed_fee_cents": values.fixed_fee_cents,
                "valid_from": values.valid_from.isoformat(),
                "valid_until": values.valid_until.isoformat() if values.valid_until else None,
                "is_active": values.is_active,
            })
            self.session.commit()
            return row
        except IntegrityError as exc:
            self.session.rollback()
            raise PaymentConfigurationConflictError("Não foi possível criar a regra de taxa.") from exc
        except Exception:
            self.session.rollback()
            raise

    def replace_fee_rule(self, rule_id: int, raw: dict[str, object], actor_id: int) -> PaymentFeeRule:
        self._begin_authorized_write(actor_id)
        try:
            previous = self.fee_rule(rule_id)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            previous_start = (
                previous.valid_from.replace(tzinfo=None)
                if previous.valid_from.tzinfo
                else previous.valid_from
            )
            boundary = max(now, previous_start)
            values = self._fee_rule_values(raw, forced_valid_from=boundary)
            previous.is_active = False
            if boundary <= previous_start:
                # Uma regra futura substituída nunca chegou a vigorar.
                previous.valid_until = None
            elif (
                previous.valid_until is None or previous.valid_until > boundary
            ):
                previous.valid_until = boundary
            previous.updated_by = actor_id
            self.session.flush()
            if self._active_overlap(values, exclude_id=previous.id) is not None:
                raise PaymentConfigurationConflictError(
                    "A nova vigência se sobrepõe a outra regra ativa com a mesma identidade."
                )
            replacement = self._new_rule(values, actor_id)
            self.session.add(replacement)
            self.session.flush()
            self._audit(actor_id, "finance.payment_fee_rule_replaced", f"payment_fee_rules/{replacement.id}", {
                "previous_rule_id": previous.id,
                "boundary": boundary.isoformat(),
                "fee_percentage_scaled": values.fee_percentage_scaled,
                "fixed_fee_cents": values.fixed_fee_cents,
                "is_active": values.is_active,
            })
            self.session.commit()
            return replacement
        except IntegrityError as exc:
            self.session.rollback()
            raise PaymentConfigurationConflictError("Não foi possível substituir a regra de taxa.") from exc
        except Exception:
            self.session.rollback()
            raise

    def set_fee_rule_active(self, rule_id: int, active: bool, actor_id: int) -> PaymentFeeRule:
        self._begin_authorized_write(actor_id)
        try:
            previous = self.fee_rule(rule_id)
            if previous.is_active == active:
                self.session.commit()
                return previous
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if not active:
                previous_start = (
                    previous.valid_from.replace(tzinfo=None)
                    if previous.valid_from.tzinfo
                    else previous.valid_from
                )
                boundary = max(now, previous_start)
                previous.is_active = False
                if boundary <= previous_start:
                    # Sem intervalo efetivo, ela não deve ser aplicada nem em
                    # um pagamento lançado posteriormente com data retroativa.
                    previous.valid_until = None
                elif (
                    previous.valid_until is None or previous.valid_until > boundary
                ):
                    previous.valid_until = boundary
                previous.updated_by = actor_id
                self._audit(actor_id, "finance.payment_fee_rule_deactivated", f"payment_fee_rules/{previous.id}", {
                    "valid_until": previous.valid_until.isoformat() if previous.valid_until else None,
                })
                self.session.commit()
                return previous

            values = FeeRuleInput(
                payment_method_id=previous.payment_method_id,
                terminal_id=previous.terminal_id,
                card_mode=previous.card_mode,
                installments=previous.installments,
                fee_percentage_scaled=previous.fee_percentage_scaled,
                fixed_fee_cents=previous.fixed_fee_cents,
                valid_from=max(
                    now,
                    previous.valid_from.replace(tzinfo=None)
                    if previous.valid_from.tzinfo
                    else previous.valid_from,
                ),
                valid_until=None,
                is_active=True,
            )
            if self._active_overlap(values, exclude_id=previous.id) is not None:
                raise PaymentConfigurationConflictError(
                    "Existe outra regra ativa para essa identidade; ajuste a vigência antes de reativar."
                )
            replacement = self._new_rule(values, actor_id)
            self.session.add(replacement)
            self.session.flush()
            self._audit(actor_id, "finance.payment_fee_rule_reactivated", f"payment_fee_rules/{replacement.id}", {
                "previous_rule_id": previous.id,
            })
            self.session.commit()
            return replacement
        except IntegrityError as exc:
            self.session.rollback()
            raise PaymentConfigurationConflictError("Não foi possível alterar o status da regra.") from exc
        except Exception:
            self.session.rollback()
            raise
