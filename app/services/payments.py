"""Liquidação integral e atômica de Notas de Serviço."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import json
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.money import cents_to_decimal, decimal_to_cents
from app.models import AuditEvent, CashMovement, Payment, ServiceNote, ServiceNoteEvent
from app.repositories.payments import PaymentRepository
from app.services.payment_validation import PaymentInput
from app.services.transactions import atomic_write
from app.services.authorization import require_active_actor_permission


MAX_CASH_CENTS = 99_999_999_999_999
FEE_PERCENT_DENOMINATOR = Decimal(1_000_000)


class PaymentValidationError(ValueError):
    def __init__(self, message: str, *, errors: dict[str, str] | None = None):
        super().__init__(message)
        self.errors = errors or {"form": message}


class PaymentNoteNotFoundError(LookupError):
    pass


class PaymentStateError(RuntimeError):
    pass


class PaymentConflictError(RuntimeError):
    pass


class PaymentConsistencyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PaymentResult:
    payment: Payment
    cash_movement: CashMovement
    idempotent: bool


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class PaymentService:
    def __init__(self, session: Session):
        self.session = session
        self.repository = PaymentRepository(session)

    def _begin_immediate(self) -> None:
        if self.session.in_transaction():
            raise RuntimeError("O pagamento deve iniciar uma transação exclusiva.")
        self.session.connection().exec_driver_sql("begin immediate")

    def _checkpoint(self, _name: str) -> None:
        """Ponto de injeção usado para provar rollback em testes."""

    def _event(
        self,
        note_id: int,
        event_type: str,
        actor_id: int,
        details: dict[str, object],
        occurred_at: datetime,
    ) -> None:
        self.session.add(ServiceNoteEvent(
            event_uid=str(uuid4()),
            note_id=note_id,
            event_type=event_type,
            occurred_at=occurred_at,
            created_by=actor_id,
            details_json=json.dumps(details, ensure_ascii=False, sort_keys=True),
        ))

    def _audit(
        self,
        actor_id: int,
        action: str,
        resource: str,
        details: dict[str, object],
    ) -> None:
        self.session.add(AuditEvent(
            user_id=actor_id,
            action=action,
            resource=resource,
            details=json.dumps(details, ensure_ascii=False, sort_keys=True),
        ))

    @staticmethod
    def _fingerprint(note_id: int, data: PaymentInput, deliver: bool) -> dict[str, object]:
        return {
            "service_note_id": note_id,
            "payment_method_id": data.payment_method_id,
            "terminal_id": data.terminal_id,
            "card_mode": data.card_mode,
            "installments": data.installments,
            "paid_at": _utc_naive(data.paid_at).isoformat(timespec="seconds"),
            "revision": data.revision,
            "deliver": deliver,
        }

    def _existing_result(
        self,
        existing: Payment,
        requested_fingerprint: dict[str, object],
    ) -> PaymentResult:
        persisted = self.repository.request_fingerprint(existing.id)
        if persisted != requested_fingerprint:
            raise PaymentConflictError(
                "Esta identificação de pagamento já foi usada com dados diferentes."
            )
        movement = self.repository.cash_for_payment(existing.id)
        if movement is None:
            raise PaymentConsistencyError(
                "O pagamento existente não possui sua movimentação financeira vinculada."
            )
        self.session.commit()
        return PaymentResult(existing, movement, True)

    @staticmethod
    def _validate_card_fields(data: PaymentInput, method_kind: str) -> None:
        errors: dict[str, str] = {}
        if method_kind == "CARD":
            if data.terminal_id is None:
                errors["terminal_id"] = "Selecione o terminal usado no cartão."
            if data.card_mode not in {"DEBIT", "CREDIT"}:
                errors["card_mode"] = "Selecione Débito ou Crédito."
            if data.card_mode == "DEBIT" and data.installments != 1:
                errors["installments"] = "No débito, informe uma parcela."
            if data.card_mode == "CREDIT" and not (
                isinstance(data.installments, int)
                and not isinstance(data.installments, bool)
                and 1 <= data.installments <= 999
            ):
                errors["installments"] = "No crédito, informe de 1 a 999 parcelas."
        elif any(value is not None for value in (
            data.terminal_id, data.card_mode, data.installments
        )):
            errors["form"] = (
                "Terminal, modalidade e parcelas somente podem ser informados para cartão."
            )
        if errors:
            raise PaymentValidationError("Revise os dados do pagamento.", errors=errors)

    @staticmethod
    def _fee_amount(gross_cents: int, percentage_scaled: int, fixed_cents: int) -> int:
        context = Context(prec=40, rounding=ROUND_HALF_UP, traps=[InvalidOperation])
        with localcontext(context):
            proportional = (
                Decimal(gross_cents) * Decimal(percentage_scaled) / FEE_PERCENT_DENOMINATOR
            ).quantize(Decimal(1), rounding=ROUND_HALF_UP)
            total = int(proportional) + int(fixed_cents)
        if total < 0 or total > gross_cents:
            raise PaymentStateError(
                "A taxa configurada supera o valor integral da Nota. Revise a regra de taxa."
            )
        return total

    @staticmethod
    def _verify_cash_bridge(movement: CashMovement, expected: tuple[int, int, int]) -> None:
        actual = (
            decimal_to_cents(Decimal(movement.gross_amount)),
            decimal_to_cents(Decimal(movement.fee_amount)),
            decimal_to_cents(Decimal(movement.net_amount)),
        )
        if actual != expected:
            raise PaymentConsistencyError(
                "A persistência legada do Caixa não preservou os centavos calculados."
            )

    @atomic_write
    def receive(
        self,
        note_id: int,
        data: PaymentInput,
        actor_id: int,
        *,
        deliver: bool = False,
    ) -> PaymentResult:
        self._begin_immediate()
        require_active_actor_permission(self.session, actor_id, "payments.receive")
        if deliver:
            require_active_actor_permission(self.session, actor_id, "notes.change_status")
        if data.errors:
            raise PaymentValidationError("Revise os dados do pagamento.", errors=data.errors)
        if not isinstance(deliver, bool):
            raise PaymentValidationError("A ação de entrega é inválida.")
        fingerprint = self._fingerprint(note_id, data, deliver)
        existing = self.repository.by_request_uid(data.request_uid)
        if existing is not None:
            return self._existing_result(existing, fingerprint)

        note = self.repository.note(note_id)
        if note is None:
            raise PaymentNoteNotFoundError("Nota não encontrada.")
        if note.total_cents == 0:
            raise PaymentStateError(
                "Notas com total zero já são quitadas sem Pagamento ou Caixa."
            )
        if note.total_cents < 0 or note.total_cents > MAX_CASH_CENTS:
            raise PaymentStateError("O total da Nota excede o limite monetário do Caixa.")
        if note.financial_status != "PENDENTE" or note.financial_settlement_reason is not None:
            raise PaymentConflictError("Esta Nota já foi quitada.")
        if self.repository.confirmed_for_note(note.id) is not None:
            raise PaymentConflictError("Esta Nota já possui um pagamento confirmado.")
        if note.operational_status == "CANCELADO":
            raise PaymentStateError("Notas canceladas não podem receber pagamento.")
        if deliver and note.operational_status != "PRONTO":
            raise PaymentStateError(
                "Entregar e receber somente está disponível para uma Nota pronta."
            )
        if not deliver and note.operational_status not in {
            "RECEBIDO", "EM_ANDAMENTO", "PRONTO", "ENTREGUE"
        }:
            raise PaymentStateError("O estado atual da Nota não permite recebimento.")
        if data.revision != note.revision:
            raise PaymentConflictError(
                "A Nota foi alterada. Atualize a página antes de registrar o pagamento."
            )
        paid_at = _utc_naive(data.paid_at)
        if paid_at < _utc_naive(note.received_at):
            raise PaymentValidationError(
                "A data do pagamento não pode ser anterior ao recebimento da Nota.",
                errors={"paid_at": "Informe uma data igual ou posterior ao recebimento da Nota."},
            )

        method = self.repository.payment_method(data.payment_method_id)
        if method is None or not method.is_active:
            raise PaymentValidationError(
                "Selecione uma forma de pagamento ativa.",
                errors={"payment_method_id": "Selecione uma forma de pagamento ativa."},
            )
        self._validate_card_fields(data, method.method_kind)
        terminal = self.repository.terminal(data.terminal_id)
        if method.method_kind == "CARD" and (terminal is None or not terminal.is_active):
            raise PaymentValidationError(
                "Selecione um terminal ativo.",
                errors={"terminal_id": "Selecione um terminal ativo."},
            )

        fee_rule = self.repository.resolve_fee_rule(
            payment_method_id=method.id,
            terminal_id=terminal.id if terminal else None,
            card_mode=data.card_mode,
            installments=data.installments,
            paid_at=paid_at,
        )
        percentage_scaled = fee_rule.fee_percentage_scaled if fee_rule else 0
        fixed_fee_cents = fee_rule.fixed_fee_cents if fee_rule else 0
        gross_cents = int(note.total_cents)
        fee_cents = self._fee_amount(gross_cents, percentage_scaled, fixed_fee_cents)
        net_cents = gross_cents - fee_cents
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        payment = Payment(
            request_uid=data.request_uid,
            service_note_id=note.id,
            customer_id=note.customer_id,
            payment_method_id=method.id,
            terminal_id=terminal.id if terminal else None,
            fee_rule_id=fee_rule.id if fee_rule else None,
            status="CONFIRMED",
            method_name_snapshot=method.name,
            method_kind_snapshot=method.method_kind,
            terminal_name_snapshot=terminal.name if terminal else None,
            card_mode_snapshot=data.card_mode if method.method_kind == "CARD" else None,
            installments=data.installments if method.method_kind == "CARD" else None,
            gross_amount_cents=gross_cents,
            fee_percentage_scaled=percentage_scaled,
            fixed_fee_cents=fixed_fee_cents,
            fee_amount_cents=fee_cents,
            net_amount_cents=net_cents,
            paid_at=paid_at,
            created_by=actor_id,
            created_at=now,
        )
        self.session.add(payment)
        self.session.flush()
        self._checkpoint("after_payment")

        values: dict[str, object] = {
            "financial_status": "PAGO",
            "financial_settlement_reason": "PAYMENT",
            "revision": note.revision + 1,
            "updated_by": actor_id,
            "updated_at": now,
        }
        if deliver:
            values["operational_status"] = "ENTREGUE"
        note_conditions = [
            ServiceNote.id == note.id,
            ServiceNote.revision == note.revision,
            ServiceNote.financial_status == "PENDENTE",
        ]
        if deliver:
            note_conditions.append(ServiceNote.operational_status == "PRONTO")
        changed = self.session.execute(
            update(ServiceNote)
            .where(*note_conditions)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            raise PaymentConflictError(
                "A Nota foi alterada durante o recebimento. Atualize a página."
            )

        reference = f"Nota {note.number_original}"
        if note.series_original:
            reference += f" · Série {note.series_original}"
        movement = CashMovement(
            movement_type="ENTRY",
            description=f"Pagamento da {reference}"[:180],
            category_id=None,
            payment_method_id=method.id,
            gross_amount=cents_to_decimal(gross_cents),
            fee_amount=cents_to_decimal(fee_cents),
            net_amount=cents_to_decimal(net_cents),
            occurred_at=paid_at,
            notes=None,
            status="ACTIVE",
            origin="SYSTEM",
            source_reference=reference[:180],
            source_type="PAYMENT",
            source_id=str(payment.id),
            created_at=now,
            updated_at=now,
            created_by=actor_id,
            updated_by=actor_id,
        )
        self.session.add(movement)
        self.session.flush()
        self._checkpoint("after_cash")
        self.session.expire(movement, ["gross_amount", "fee_amount", "net_amount"])
        self._verify_cash_bridge(movement, (gross_cents, fee_cents, net_cents))

        self._event(note.id, "PAYMENT_RECEIVED", actor_id, {
            "payment_id": payment.id,
            "cash_movement_id": movement.id,
            "method": method.name,
            "gross_amount_cents": gross_cents,
            "fee_amount_cents": fee_cents,
            "net_amount_cents": net_cents,
        }, now)
        if deliver:
            self._event(note.id, "STATUS_CHANGED", actor_id, {
                "from": "PRONTO", "to": "ENTREGUE", "payment_id": payment.id,
            }, now)
        self._audit(actor_id, "payment.confirmed", f"payments/{payment.id}", {
            "request_fingerprint": fingerprint,
            "cash_movement_id": movement.id,
            "gross_amount_cents": gross_cents,
            "fee_amount_cents": fee_cents,
            "net_amount_cents": net_cents,
            "fee_rule_id": fee_rule.id if fee_rule else None,
        })
        self._audit(actor_id, "cash_movement.system_created", f"cash_movements/{movement.id}", {
            "payment_id": payment.id,
            "service_note_id": note.id,
        })
        try:
            self.session.commit()
        except IntegrityError as exc:
            text = str(getattr(exc, "orig", exc)).casefold()
            if "request_uid" in text or "service_note" in text or "source_type" in text:
                raise PaymentConflictError(
                    "O pagamento já foi processado por outra solicitação."
                ) from None
            raise
        return PaymentResult(payment, movement, False)
