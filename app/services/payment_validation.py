from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.services.cash_validation import local_now, local_to_utc_naive, project_zone
from app.core.money import decimal_to_cents
from app.services.cash_validation import parse_money
from app.services.payment_configuration import _parse_percentage, format_fee_percentage


@dataclass(slots=True)
class PaymentInput:
    request_uid: str
    payment_method_id: int | None
    terminal_id: int | None
    card_mode: str | None
    installments: int | None
    paid_at: datetime
    revision: int | None
    notes: str | None = None
    errors: dict[str, str] = field(default_factory=dict)
    override_fee_percentage_scaled: int | None = None
    override_fixed_fee_cents: int | None = None
    fee_override_reason: str | None = None
    amount: str = ""
    customer_surcharge_cents: int = 0

    @classmethod
    def from_form(
        cls,
        form: dict[str, object],
        timezone_name: str,
        *,
        now: datetime | None = None,
    ) -> PaymentInput:
        errors: dict[str, str] = {}
        raw_uid = str(form.get("request_uid") or "").strip().lower()
        try:
            parsed_uid = UUID(raw_uid)
            if str(parsed_uid) != raw_uid:
                raise ValueError
        except (AttributeError, TypeError, ValueError):
            errors["request_uid"] = "Atualize a página para gerar uma identificação de pagamento válida."

        def identifier(key: str, *, optional: bool) -> int | None:
            raw = str(form.get(key) or "").strip()
            if optional and not raw:
                return None
            if not raw.isascii() or not raw.isdecimal() or len(raw) > 19:
                errors[key] = "Selecione uma opção válida."
                return None
            value = int(raw)
            if not 0 < value < 2**63:
                errors[key] = "Selecione uma opção válida."
                return None
            return value

        method_id = identifier("payment_method_id", optional=False)
        terminal_id = identifier("terminal_id", optional=True)
        revision = identifier("revision", optional=False)
        card_mode = str(form.get("card_mode") or "").strip().upper() or None
        if card_mode not in {None, "DEBIT", "CREDIT"}:
            errors["card_mode"] = "Selecione débito ou crédito."
            card_mode = None
        raw_installments = str(form.get("installments") or "").strip()
        installments = None
        if raw_installments:
            try:
                installments = int(raw_installments)
                if not 1 <= installments <= 999:
                    raise ValueError
            except ValueError:
                installments = None
                errors["installments"] = "Informe de 1 a 999 parcelas."

        notes = str(form.get("notes") or "").strip()
        if len(notes) > 1000:
            errors["notes"] = "A observação deve ter no máximo 1000 caracteres."
            notes = notes[:1000]

        raw_percentage = str(form.get("override_fee_percentage") or "").strip()
        raw_fixed = str(form.get("override_fixed_fee") or "").strip()
        override_reason = str(form.get("fee_override_reason") or "").strip()
        override_percentage = override_fixed = None
        if raw_percentage or raw_fixed:
            percentage_errors: dict[str, str] = {}
            override_percentage = _parse_percentage(raw_percentage or "0", percentage_errors)
            if percentage_errors:
                errors["override_fee_percentage"] = percentage_errors["fee_percentage"]
            try:
                override_fixed = decimal_to_cents(parse_money(
                    raw_fixed or "0", allow_zero=True, label="custo fixo da operadora"
                ))
            except (TypeError, ValueError, OverflowError) as exc:
                errors["override_fixed_fee"] = str(exc)
            if not 5 <= len(override_reason) <= 300:
                errors["fee_override_reason"] = "Explique o ajuste em 5 a 300 caracteres."
        elif override_reason:
            errors["fee_override_reason"] = "Informe o percentual ou valor fixo do ajuste."
        try:
            surcharge = decimal_to_cents(parse_money(
                form.get("customer_surcharge") or "0", allow_zero=True, label="acréscimo ao cliente"
            ))
        except (TypeError, ValueError, OverflowError) as exc:
            errors["customer_surcharge"] = str(exc)
            surcharge = 0

        zone = project_zone(timezone_name)
        current = now or local_now(timezone_name)
        if current.tzinfo is None:
            current = current.replace(tzinfo=zone)
        raw_date = str(form.get("paid_at") or "").strip()
        try:
            local_value = datetime.fromisoformat(raw_date)
            if local_value.tzinfo is not None:
                local_value = local_value.astimezone(zone).replace(tzinfo=None)
            aware_value = local_value.replace(tzinfo=zone)
            if aware_value > current + timedelta(days=1):
                errors["paid_at"] = "A data do pagamento não pode estar distante no futuro."
            paid_at = local_to_utc_naive(local_value, timezone_name)
        except (ValueError, OverflowError, OSError):
            paid_at = current.astimezone(timezone.utc).replace(tzinfo=None)
            errors["paid_at"] = "Informe uma data e hora válidas."

        return cls(
            request_uid=raw_uid,
            payment_method_id=method_id,
            terminal_id=terminal_id,
            card_mode=card_mode,
            installments=installments,
            paid_at=paid_at,
            revision=revision,
            notes=notes or None,
            errors=errors,
            override_fee_percentage_scaled=override_percentage,
            override_fixed_fee_cents=override_fixed,
            fee_override_reason=override_reason or None,
            amount=str(form.get("amount") or ""),
            customer_surcharge_cents=surcharge,
        )

    def as_form(self, timezone_name: str) -> dict[str, str]:
        local_value = self.paid_at.replace(tzinfo=timezone.utc).astimezone(project_zone(timezone_name))
        return {
            "request_uid": self.request_uid,
            "payment_method_id": str(self.payment_method_id or ""),
            "terminal_id": str(self.terminal_id or ""),
            "card_mode": self.card_mode or "",
            "installments": str(self.installments or ""),
            "paid_at": local_value.strftime("%Y-%m-%dT%H:%M"),
            "revision": str(self.revision or ""),
            "notes": self.notes or "",
            "override_fee_percentage": format_fee_percentage(self.override_fee_percentage_scaled) if self.override_fee_percentage_scaled is not None else "",
            "override_fixed_fee": f"{self.override_fixed_fee_cents // 100},{self.override_fixed_fee_cents % 100:02d}" if self.override_fixed_fee_cents is not None else "",
            "fee_override_reason": self.fee_override_reason or "",
            "customer_surcharge": f"{self.customer_surcharge_cents // 100},{self.customer_surcharge_cents % 100:02d}",
        }
