from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


DEFAULT_RECENT_DAYS = 30
DEFAULT_ATTENTION_DAYS = 60
DEFAULT_DISTANT_DAYS = 90
MAX_INACTIVITY_DAYS = 365_000


def _configured_day(settings: dict[str, str], key: str, default: int) -> int:
    try:
        value = int(settings.get(key, str(default)))
    except (TypeError, ValueError):
        return default
    return value if 0 <= value <= MAX_INACTIVITY_DAYS else default


@dataclass(frozen=True, slots=True)
class InactivityThresholds:
    recent_days: int = DEFAULT_RECENT_DAYS
    attention_days: int = DEFAULT_ATTENTION_DAYS
    distant_days: int = DEFAULT_DISTANT_DAYS

    @classmethod
    def from_settings(cls, settings: dict[str, str]) -> "InactivityThresholds":
        values = cls(
            recent_days=_configured_day(
                settings, "customers.inactivity.recent_days", DEFAULT_RECENT_DAYS
            ),
            attention_days=_configured_day(
                settings, "customers.inactivity.attention_days", DEFAULT_ATTENTION_DAYS
            ),
            distant_days=_configured_day(
                settings, "customers.inactivity.distant_days", DEFAULT_DISTANT_DAYS
            ),
        )
        if values.recent_days < values.attention_days < values.distant_days:
            return values
        # Uma configuração local corrompida não pode tornar Clientes inacessível
        # nem criar faixas sobrepostas. O conjunto padrão é restaurado em bloco.
        return cls()

    def minimum_days_for_filter(self, relationship: str) -> int | None:
        """Traduz os códigos legados 30/60/90 para os limites configurados.

        A classificação inclui o dia do limite na faixa anterior. Portanto,
        "mais de 30 dias" começa em 31 quando o limite configurado é 30.
        """
        return {
            "30_PLUS": self.recent_days + 1,
            "60_PLUS": self.attention_days + 1,
            "90_PLUS": self.distant_days + 1,
        }.get(relationship)

    def classify(self, last_activity: datetime | None, *, now: datetime | None = None) -> tuple[str, str, int | None]:
        if last_activity is None:
            return "NEVER", "Nunca atendido", None
        current = now or datetime.now(timezone.utc)
        if last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=timezone.utc)
        days = max(0, (current - last_activity).days)
        if days <= self.recent_days:
            return "RECENT", "Recente", days
        if days <= self.attention_days:
            return "ATTENTION", "Atenção", days
        if days <= self.distant_days:
            return "DISTANT", "Afastado", days
        return "LONG_AGO", "Há muito tempo", days


RELATIONSHIP_BADGES = {
    "NEVER": "neutral",
    "RECENT": "success",
    "ATTENTION": "warning",
    "DISTANT": "warning",
    "LONG_AGO": "danger",
}
