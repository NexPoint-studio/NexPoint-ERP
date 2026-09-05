from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class InactivityThresholds:
    recent_days: int = 30
    attention_days: int = 60
    distant_days: int = 90

    @classmethod
    def from_settings(cls, settings: dict[str, str]) -> "InactivityThresholds":
        return cls(
            recent_days=int(settings.get("customers.inactivity.recent_days", "30")),
            attention_days=int(settings.get("customers.inactivity.attention_days", "60")),
            distant_days=int(settings.get("customers.inactivity.distant_days", "90")),
        )

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
