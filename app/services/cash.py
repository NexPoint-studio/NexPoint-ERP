from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import math

from sqlalchemy.exc import IntegrityError
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import AuditEvent, CashCategory, CashMovement
from app.services.transactions import atomic_write
from app.services.authorization import require_active_actor_permission
from app.repositories.cash import (
    CashCategoryListItem,
    CashCategoryRepository,
    CashMovementRepository,
    PaymentMethodRepository,
)
from app.services.cash_validation import (
    CENT,
    CashCategoryInput,
    CashMovementInput,
    Period,
    local_now,
    resolve_period,
    project_zone,
    utc_naive_to_local,
)


ZERO = Decimal("0.00")


class CashValidationError(Exception):
    def __init__(self, data):
        self.data = data


class CashMovementNotFoundError(Exception):
    pass


class CashCategoryNotFoundError(Exception):
    pass


class DuplicateCashCategoryError(Exception):
    pass


class CashStateError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class FinancialTotals:
    entry_gross: Decimal = ZERO
    fees: Decimal = ZERO
    entries: Decimal = ZERO
    exits: Decimal = ZERO
    result: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class CategoryReportRow:
    name: str
    count: int
    total: Decimal


@dataclass(frozen=True, slots=True)
class PaymentReportRow:
    name: str
    count: int
    gross: Decimal
    fees: Decimal
    net: Decimal


@dataclass(frozen=True, slots=True)
class PaymentEntryRow:
    name: str
    total: Decimal


@dataclass(frozen=True, slots=True)
class EvolutionRow:
    key: str
    label: str
    entries: Decimal
    exits: Decimal
    result: Decimal


@dataclass(frozen=True, slots=True)
class PeriodReport:
    period: Period
    previous_balance: Decimal
    totals: FinancialTotals
    final_balance: Decimal
    entry_categories: list[CategoryReportRow]
    exit_categories: list[CategoryReportRow]
    payments: list[PaymentReportRow]
    evolution: list[EvolutionRow]


@dataclass(frozen=True, slots=True)
class CashSummary:
    current_balance: Decimal
    month: PeriodReport
    recent: list[CashMovement]
    payment_entries: list[PaymentEntryRow]


@dataclass(frozen=True, slots=True)
class CashHistoryResult:
    rows: list[CashMovement]
    total: int
    page: int
    per_page: int
    pages: int
    totals: FinancialTotals


@dataclass(frozen=True, slots=True)
class CashOperationsResult:
    rows: list[CashMovement]
    total: int
    page: int
    per_page: int
    pages: int
    start_utc: datetime
    end_utc: datetime


class CashReportService:
    """Fonte única para todos os cálculos financeiros exibidos pelo Caixa."""

    def __init__(self, session: Session, timezone_name: str):
        self.session = session
        self.timezone_name = timezone_name
        self.movements = CashMovementRepository(session)
        self.payment_methods = PaymentMethodRepository(session)

    @staticmethod
    def totals(rows: list[CashMovement]) -> FinancialTotals:
        entry_gross = fees = entries = exits = ZERO
        for row in rows:
            if row.status != "ACTIVE":
                continue
            if row.movement_type == "ENTRY":
                entry_gross += Decimal(row.gross_amount)
                fees += Decimal(row.fee_amount)
                entries += Decimal(row.net_amount)
            else:
                exits += Decimal(row.net_amount)
        values = [value.quantize(CENT) for value in (entry_gross, fees, entries, exits)]
        return FinancialTotals(*values, (values[2] - values[3]).quantize(CENT))

    def balance_before(self, cutoff_utc: datetime) -> Decimal:
        return self.totals(self.movements.active_before(cutoff_utc)).result

    @staticmethod
    def _category_rows(rows: list[CashMovement], movement_type: str) -> list[CategoryReportRow]:
        grouped: dict[str, list[Decimal | int]] = {}
        for row in rows:
            if row.movement_type != movement_type:
                continue
            name = row.category.name if row.category else "Sem categoria"
            bucket = grouped.setdefault(name, [0, ZERO])
            bucket[0] = int(bucket[0]) + 1
            bucket[1] = Decimal(bucket[1]) + Decimal(row.net_amount)
        return sorted(
            [CategoryReportRow(name, int(data[0]), Decimal(data[1]).quantize(CENT)) for name, data in grouped.items()],
            key=lambda item: (-item.total, item.name.casefold()),
        )

    @staticmethod
    def _payment_rows(rows: list[CashMovement]) -> list[PaymentReportRow]:
        grouped: dict[str, list[Decimal | int]] = {}
        for row in rows:
            name = row.payment_method.name if row.payment_method else "Sem forma informada"
            bucket = grouped.setdefault(name, [0, ZERO, ZERO, ZERO])
            bucket[0] = int(bucket[0]) + 1
            bucket[1] = Decimal(bucket[1]) + Decimal(row.gross_amount)
            bucket[2] = Decimal(bucket[2]) + Decimal(row.fee_amount)
            impact = Decimal(row.net_amount) if row.movement_type == "ENTRY" else -Decimal(row.net_amount)
            bucket[3] = Decimal(bucket[3]) + impact
        return sorted(
            [
                PaymentReportRow(
                    name,
                    int(data[0]),
                    Decimal(data[1]).quantize(CENT),
                    Decimal(data[2]).quantize(CENT),
                    Decimal(data[3]).quantize(CENT),
                )
                for name, data in grouped.items()
            ],
            key=lambda item: item.name.casefold(),
        )

    def _evolution(self, rows: list[CashMovement], period: Period) -> list[EvolutionRow]:
        grouped: dict[str, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
        labels: dict[str, str] = {}
        daily = period.days <= 62
        for row in rows:
            local_date = utc_naive_to_local(row.occurred_at, self.timezone_name)
            key = local_date.strftime("%Y-%m-%d" if daily else "%Y-%m")
            labels[key] = local_date.strftime("%d/%m" if daily else "%m/%Y")
            if row.movement_type == "ENTRY":
                grouped[key][0] += Decimal(row.net_amount)
            else:
                grouped[key][1] += Decimal(row.net_amount)
        return [
            EvolutionRow(
                key,
                labels[key],
                grouped[key][0].quantize(CENT),
                grouped[key][1].quantize(CENT),
                (grouped[key][0] - grouped[key][1]).quantize(CENT),
            )
            for key in sorted(grouped)
        ]

    def period_report(self, period: Period) -> PeriodReport:
        rows = self.movements.active_between(period.start_utc, period.end_utc)
        previous = self.balance_before(period.start_utc)
        totals = self.totals(rows)
        return PeriodReport(
            period=period,
            previous_balance=previous,
            totals=totals,
            final_balance=(previous + totals.result).quantize(CENT),
            entry_categories=self._category_rows(rows, "ENTRY"),
            exit_categories=self._category_rows(rows, "EXIT"),
            payments=self._payment_rows(rows),
            evolution=self._evolution(rows, period),
        )

    def summary(self, *, now: datetime | None = None) -> CashSummary:
        local_current = now or local_now(self.timezone_name)
        if local_current.tzinfo is None:
            local_current = local_current.replace(tzinfo=project_zone(self.timezone_name))
        today = local_current.date()
        now_utc = local_current.astimezone(timezone.utc).replace(tzinfo=None)
        month_period = resolve_period("month", None, None, self.timezone_name, today=today)
        effective_end = min(month_period.end_utc, now_utc + timedelta(microseconds=1))
        effective_month = Period(
            month_period.shortcut,
            month_period.start_date,
            month_period.end_date,
            month_period.start_utc,
            effective_end,
        )
        month_report = self.period_report(effective_month)
        current_balance = month_report.final_balance
        month_rows = self.movements.active_between(month_period.start_utc, effective_end)
        entry_by_payment: dict[int | None, Decimal] = defaultdict(lambda: ZERO)
        for row in month_rows:
            if row.movement_type == "ENTRY":
                entry_by_payment[row.payment_method_id] += Decimal(row.net_amount)
        payment_entries = [
            PaymentEntryRow(method.name, entry_by_payment[method.id].quantize(CENT))
            for method in self.payment_methods.all()
        ]
        if entry_by_payment.get(None, ZERO):
            payment_entries.append(PaymentEntryRow("Sem forma informada", entry_by_payment[None].quantize(CENT)))
        return CashSummary(
            current_balance=current_balance,
            month=month_report,
            recent=self.movements.recent(now_utc, 8),
            payment_entries=payment_entries,
        )


class CashService:
    def __init__(self, session: Session, timezone_name: str):
        self.session = session
        self.timezone_name = timezone_name
        self.repository = CashMovementRepository(session)
        self.categories = CashCategoryRepository(session)
        self.payment_methods = PaymentMethodRepository(session)
        self.reports = CashReportService(session, timezone_name)

    def _audit(self, user_id: int, action: str, resource: str, details: dict | None = None) -> None:
        self.session.add(AuditEvent(
            user_id=user_id,
            action=action,
            resource=resource,
            details=json.dumps(details, ensure_ascii=False, default=str) if details else None,
        ))

    def _operator_window(self, now: datetime | None = None) -> tuple[datetime, datetime]:
        local_current = now or local_now(self.timezone_name)
        if local_current.tzinfo is None:
            local_current = local_current.replace(tzinfo=project_zone(self.timezone_name))
        current_utc = local_current.astimezone(timezone.utc).replace(tzinfo=None)
        return current_utc - timedelta(days=7), current_utc + timedelta(microseconds=1)

    def _validate_refs(self, data: CashMovementInput, *, existing: CashMovement | None = None) -> None:
        category = self.categories.get(data.category_id) if data.category_id else None
        if data.category_id and category is None:
            data.errors["category_id"] = "Categoria não encontrada."
        elif category is not None:
            if not category.is_active and (existing is None or existing.category_id != category.id):
                data.errors["category_id"] = "A categoria selecionada está inativa."
            if category.movement_type not in {data.movement_type, "BOTH"}:
                data.errors["category_id"] = "A categoria não aceita este tipo de lançamento."
        method = self.payment_methods.get(data.payment_method_id) if data.payment_method_id else None
        if data.payment_method_id and method is None:
            data.errors["payment_method_id"] = "Forma de pagamento não encontrada."
        elif method is not None and not method.is_active and (existing is None or existing.payment_method_id != method.id):
            data.errors["payment_method_id"] = "A forma de pagamento selecionada está inativa."
        if data.errors:
            raise CashValidationError(data)

    @atomic_write
    def create(self, data: CashMovementInput, user_id: int) -> CashMovement:
        require_active_actor_permission(self.session, user_id, "cash.create")
        self._validate_refs(data)
        movement = CashMovement(
            movement_type=data.movement_type,
            description=data.description,
            category_id=data.category_id,
            payment_method_id=data.payment_method_id,
            gross_amount=data.gross_amount,
            fee_amount=data.fee_amount,
            net_amount=data.net_amount,
            occurred_at=data.occurred_at,
            notes=data.notes,
            status="ACTIVE",
            origin="MANUAL",
            created_by=user_id,
            updated_by=user_id,
        )
        self.session.add(movement)
        self.session.flush()
        self._audit(user_id, "CASH_MOVEMENT_CREATED", f"cash_movements/{movement.id}", {
            "type": movement.movement_type,
            "description": movement.description,
            "gross_amount": movement.gross_amount,
            "fee_amount": movement.fee_amount,
            "net_amount": movement.net_amount,
        })
        self.session.commit()
        return movement

    @staticmethod
    def _money_message(label: str, before: Decimal, after: Decimal) -> str:
        def money_br(value: Decimal) -> str:
            formatted = f"{value.quantize(CENT):,.2f}"
            return "R$ " + formatted.replace(",", "_").replace(".", ",").replace("_", ".")

        participle = "alterada" if label == "Taxa" else "alterado"
        return f"{label} {participle} de {money_br(before)} para {money_br(after)}."

    @atomic_write
    def update(
        self,
        movement_id: int,
        data: CashMovementInput,
        user_id: int,
        *,
        operator_scope: bool = False,
    ) -> CashMovement:
        require_active_actor_permission(self.session, user_id, "cash.edit")
        movement = (
            self.detail_for_operator(movement_id, user_id)
            if operator_scope
            else self.repository.get(movement_id)
        )
        if movement is None:
            raise CashMovementNotFoundError()
        if movement.origin == "SYSTEM":
            raise CashStateError(
                "Lançamentos gerados pelo sistema não podem ser editados diretamente no Caixa."
            )
        if movement.status == "CANCELED":
            raise CashStateError("Lançamentos cancelados não podem ser editados.")
        if data.movement_type != movement.movement_type:
            data.errors["movement_type"] = "O tipo do lançamento não pode ser alterado."
        self._validate_refs(data, existing=movement)

        changes: dict[str, dict[str, object]] = {}
        summaries: list[str] = []
        fields = (
            "description", "category_id", "payment_method_id", "gross_amount",
            "fee_amount", "net_amount", "occurred_at", "notes",
        )
        labels = {
            "description": "Descrição alterada.",
            "category_id": "Categoria alterada.",
            "payment_method_id": "Forma de pagamento alterada.",
            "occurred_at": "Data do lançamento alterada.",
            "notes": "Observações alteradas.",
        }
        for field_name in fields:
            before, after = getattr(movement, field_name), getattr(data, field_name)
            if before != after:
                changes[field_name] = {"from": before, "to": after}
                setattr(movement, field_name, after)
                if field_name == "gross_amount":
                    summaries.append(self._money_message("Valor bruto", Decimal(before), Decimal(after)))
                elif field_name == "fee_amount":
                    summaries.append(self._money_message("Taxa", Decimal(before), Decimal(after)))
                elif field_name in labels:
                    summaries.append(labels[field_name])
        movement.updated_by = user_id
        if changes:
            self._audit(user_id, "CASH_MOVEMENT_UPDATED", f"cash_movements/{movement.id}", {
                "changes": changes,
                "summary": summaries,
            })
        self.session.commit()
        return movement

    @atomic_write
    def cancel(
        self,
        movement_id: int,
        reason: str,
        user_id: int,
        *,
        operator_scope: bool = False,
    ) -> CashMovement:
        require_active_actor_permission(self.session, user_id, "cash.cancel")
        movement = (
            self.detail_for_operator(movement_id, user_id)
            if operator_scope
            else self.repository.get(movement_id)
        )
        if movement is None:
            raise CashMovementNotFoundError()
        if movement.origin == "SYSTEM":
            raise CashStateError(
                "Lançamentos gerados pelo sistema não podem ser cancelados diretamente no Caixa."
            )
        if movement.status == "CANCELED":
            raise CashStateError("Este lançamento já está cancelado.")
        cleaned = reason.strip()[:500]
        if not cleaned:
            raise ValueError("Informe o motivo do cancelamento.")
        movement.status = "CANCELED"
        movement.canceled_at = datetime.now(timezone.utc).replace(tzinfo=None)
        movement.canceled_by = user_id
        movement.cancellation_reason = cleaned
        movement.updated_by = user_id
        self._audit(user_id, "CASH_MOVEMENT_CANCELED", f"cash_movements/{movement.id}", {
            "reason": cleaned,
            "net_amount": movement.net_amount,
        })
        self.session.commit()
        return movement

    def history(self, period: Period, **filters) -> CashHistoryResult:
        page = max(1, int(filters.pop("page", 1)))
        per_page = int(filters.pop("per_page", 25))
        if per_page not in {25, 50, 100}:
            per_page = 25
        rows, total, page = self.repository.list(
            start_utc=period.start_utc,
            end_utc=period.end_utc,
            page=page,
            per_page=per_page,
            **filters,
        )
        return CashHistoryResult(
            rows,
            total,
            page,
            per_page,
            max(1, math.ceil(total / per_page)),
            self.reports.totals(self.repository.active_between(period.start_utc, period.end_utc)),
        )

    def operations(
        self,
        user_id: int,
        *,
        page: int = 1,
        per_page: int = 25,
        now: datetime | None = None,
    ) -> CashOperationsResult:
        """Histórico recente do operador, deliberadamente sem totais financeiros."""
        effective_page = max(1, int(page))
        effective_per_page = int(per_page)
        if effective_per_page not in {10, 25, 50, 100}:
            effective_per_page = 25
        start_utc, end_utc = self._operator_window(now)
        rows, total, effective_page = self.repository.list_for_creator(
            creator_id=user_id,
            start_utc=start_utc,
            end_utc=end_utc,
            page=effective_page,
            per_page=effective_per_page,
        )
        return CashOperationsResult(
            rows=rows,
            total=total,
            page=effective_page,
            per_page=effective_per_page,
            pages=max(1, math.ceil(total / effective_per_page)),
            start_utc=start_utc,
            end_utc=end_utc,
        )

    def detail_for_operator(
        self,
        movement_id: int,
        user_id: int,
        *,
        now: datetime | None = None,
    ) -> CashMovement:
        start_utc, end_utc = self._operator_window(now)
        movement = self.repository.get_for_creator(
            movement_id,
            creator_id=user_id,
            start_utc=start_utc,
            end_utc=end_utc,
        )
        if movement is None:
            raise CashMovementNotFoundError()
        return movement

    def detail(self, movement_id: int) -> CashMovement:
        movement = self.repository.get(movement_id)
        if movement is None:
            raise CashMovementNotFoundError()
        return movement

    @atomic_write
    def category_create(self, data: CashCategoryInput, user_id: int) -> CashCategory:
        require_active_actor_permission(self.session, user_id, "cash.categories.manage")
        if data.errors:
            raise CashValidationError(data)
        self.session.execute(update(CashCategory).where(CashCategory.id == -1).values(sort_order=0))
        if self.categories.by_name(data.name):
            raise DuplicateCashCategoryError()
        category = CashCategory(
            name=data.name,
            movement_type=data.movement_type,
            description=data.description,
            sort_order=data.sort_order,
            is_active=data.is_active,
            created_by=user_id,
            updated_by=user_id,
        )
        self.session.add(category)
        try:
            self.session.flush()
            self._audit(user_id, "CASH_CATEGORY_CREATED", f"cash_categories/{category.id}", {"name": category.name})
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            if "cash_categories.name" in str(exc.orig).casefold():
                raise DuplicateCashCategoryError() from None
            raise
        return category

    @atomic_write
    def category_update(self, category_id: int, data: CashCategoryInput, user_id: int) -> CashCategory:
        require_active_actor_permission(self.session, user_id, "cash.categories.manage")
        self.session.execute(update(CashCategory).where(CashCategory.id == -1).values(sort_order=0))
        category = self.categories.get(category_id)
        if category is None:
            raise CashCategoryNotFoundError()
        if data.errors:
            raise CashValidationError(data)
        if self.categories.by_name(data.name, exclude_id=category_id):
            raise DuplicateCashCategoryError()
        linked_types = self.categories.linked_movement_types(category_id)
        if data.movement_type != "BOTH" and linked_types - {data.movement_type}:
            data.errors["movement_type"] = (
                "O tipo não é compatível com os lançamentos já vinculados a esta categoria."
            )
            raise CashValidationError(data)
        changes = {}
        was_active = category.is_active
        for field_name in ("name", "movement_type", "description", "sort_order", "is_active"):
            before, after = getattr(category, field_name), getattr(data, field_name)
            if before != after:
                changes[field_name] = {"from": before, "to": after}
                setattr(category, field_name, after)
        if changes:
            category.updated_by = user_id
            self._audit(user_id, "CASH_CATEGORY_UPDATED", f"cash_categories/{category.id}", changes)
            if was_active and not category.is_active:
                self._audit(
                    user_id,
                    "CASH_CATEGORY_DEACTIVATED",
                    f"cash_categories/{category.id}",
                    {"is_active": False},
                )
            try:
                self.session.commit()
            except IntegrityError as exc:
                self.session.rollback()
                if "cash_categories.name" in str(exc.orig).casefold():
                    raise DuplicateCashCategoryError() from None
                raise
        return category

    @atomic_write
    def category_set_active(self, category_id: int, active: bool, user_id: int) -> CashCategory:
        require_active_actor_permission(self.session, user_id, "cash.categories.manage")
        category = self.categories.get(category_id)
        if category is None:
            raise CashCategoryNotFoundError()
        if category.is_active != active:
            category.is_active = active
            category.updated_by = user_id
            action = "CASH_CATEGORY_UPDATED" if active else "CASH_CATEGORY_DEACTIVATED"
            self._audit(user_id, action, f"cash_categories/{category.id}", {"is_active": active})
            self.session.commit()
        return category
