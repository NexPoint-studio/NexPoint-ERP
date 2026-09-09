from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.services.cash_validation import local_now, local_to_utc_naive, project_zone


@dataclass(slots=True)
class PaymentInput:
    request_uid: str
    payment_method_id: int | None
    terminal_id: int | None
    card_mode: str | None
    installments: int | None
    paid_at: datetime
    revision: int | None
    errors: dict[str, str] = field(default_factory=dict)

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
            errors=errors,
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
        }
