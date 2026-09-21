"""Explicit, sanitized ERP -> local Control Center telemetry contract."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import hmac
from pathlib import Path
import re
from threading import RLock
import time
from typing import Mapping

from app.repositories import ConfigurationRepository
from app.services.connectivity import ConnectivityState
from app.services.erp_preflight import run_preflight
from control_center.domain import ErpInstallation, HealthSnapshot, RiskSummary, Tenant
from control_center.local_repository import LocalControlCenterRepository
from control_center.sanitization import sanitize_mapping, sanitize_text
from app.repositories.sync import OutboxRepository


_HEALTH_RANK = {
    "healthy": 0,
    "normal": 0,
    "unknown": 1,
    "warning": 2,
    "degraded": 2,
    "unavailable": 3,
    "offline": 3,
    "high": 4,
    "critical": 5,
}


def _worst_health(*states: str) -> str:
    values = tuple(state for state in states if state in _HEALTH_RANK)
    return max(values, key=lambda state: _HEALTH_RANK[state]) if values else "unknown"


def _check_state(status: str) -> str:
    return {"PASS": "healthy", "WARN": "warning", "FAIL": "critical"}.get(
        str(status).upper(), "unknown"
    )


def control_center_database_for(erp_database_path: Path, *, operational: bool) -> Path:
    """Choose a sibling platform database while refusing the operational filename."""

    source = Path(erp_database_path).resolve()
    filename = "control_center.sqlite3" if operational else f"{source.stem}_control_center.sqlite3"
    destination = source.with_name(filename).resolve()
    if destination == source:
        raise RuntimeError("O Control Center não pode compartilhar o banco operacional.")
    return destination


def _stable_database_id(purpose: str, database_path: Path) -> str:
    """Stable opaque ID that is independent from rotatable session secrets."""

    source = str(Path(database_path).resolve()).casefold()
    digest = sha256(
        f"nexpoint-control-center:v1:{purpose}:{source}".encode("utf-8")
    ).hexdigest()[:24]
    return f"{purpose}_{digest}"


def tenant_id_for(database_path: Path) -> str:
    return _stable_database_id("tenant", database_path)


def installation_id_for(database_path: Path) -> str:
    return _stable_database_id("installation", database_path)


def legacy_id_for(database_path: Path, secret: str, purpose: str) -> str:
    """Reproduce the pre-V1 ID only to adopt already persisted local records."""

    source = str(Path(database_path).resolve()).casefold()
    key = sha256(secret.encode("utf-8")).digest()
    digest = hmac.new(
        key,
        f"{purpose}:{source}".encode("utf-8"),
        sha256,
    ).hexdigest()[:24]
    return f"{purpose}_{digest}"


def control_center_identity_for(
    settings: Mapping[str, str],
) -> tuple[str, str, str]:
    """Derive opaque sidecar IDs from the identity stored inside the ERP DB."""

    identity = str(settings.get("system.control_center_identity") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", identity):
        raise RuntimeError("A identidade persistente do ERP e invalida.")
    source = sha256(
        f"nexpoint-control-center:v2:source:{identity}".encode("ascii")
    ).hexdigest()
    tenant = sha256(
        f"nexpoint-control-center:v2:tenant:{identity}".encode("ascii")
    ).hexdigest()[:24]
    installation = sha256(
        f"nexpoint-control-center:v2:installation:{identity}".encode("ascii")
    ).hexdigest()[:24]
    return source, f"tenant_{tenant}", f"installation_{installation}"


class ControlTelemetryAdapter:
    """Publish bounded technical snapshots without querying customer records."""

    def __init__(self, repository: LocalControlCenterRepository, *, interval_seconds: float = 300.0,
                 outbox_only: bool = False):
        self.repository = repository
        self.interval_seconds = max(1.0, min(float(interval_seconds), 3600.0))
        self._last_publish = 0.0
        self._last_heartbeat = 0.0
        self._lock = RLock()
        self.outbox_only = outbox_only

    def maybe_publish(self, app, *, force: bool = False) -> bool:
        now_monotonic = time.monotonic()
        try:
            monitor = getattr(app.state, "diagnostic_monitor", None)
            has_events = bool(
                monitor is not None
                and monitor.recent(include_all=True, limit=1)
            )
        except Exception:
            # Even the event probe is telemetry and must remain best-effort.
            return False
        with self._lock:
            last_attempt = self._last_publish if has_events else self._last_heartbeat
            if not force and now_monotonic - last_attempt < self.interval_seconds:
                return False
            if has_events:
                self._last_publish = now_monotonic
            else:
                self._last_heartbeat = now_monotonic
        try:
            observed = self.publish(app)
        except Exception:
            # Telemetry is best-effort: it can never interrupt local ERP work.
            with self._lock:
                if has_events:
                    if self._last_publish == now_monotonic:
                        self._last_publish = last_attempt
                else:
                    if self._last_heartbeat == now_monotonic:
                        self._last_heartbeat = last_attempt
            return False
        completed_at = time.monotonic()
        with self._lock:
            if observed:
                self._last_publish = max(self._last_publish, completed_at)
            self._last_heartbeat = max(self._last_heartbeat, completed_at)
        return observed

    def publish(self, app) -> bool:
        now = datetime.now(timezone.utc)
        with app.state.session_factory() as session:
            settings = ConfigurationRepository(session).settings()
        _source, fallback_tenant_id, fallback_installation_id = (
            control_center_identity_for(settings)
        )
        tenant_id = str(
            getattr(app.state, "control_center_tenant_id", "")
            or fallback_tenant_id
        )
        installation_id = str(
            getattr(app.state, "control_center_installation_id", "")
            or fallback_installation_id
        )
        monitor = getattr(app.state, "diagnostic_monitor", None)
        events = monitor.recent(include_all=True, limit=100) if monitor is not None else ()
        pending_events = (
            monitor.pending_for_sync(limit=100)
            if monitor is not None and hasattr(monitor, "pending_for_sync")
            else tuple(
                event for event in events
                if event.severity in {"WARNING", "ERROR", "CRITICAL"}
            )
        )
        report = monitor.risk_report(include_all=True) if monitor is not None else None
        findings = report.findings if report is not None else ()
        score = report.overall_score if report is not None else 0
        observed = bool(events)
        calculated_health = "critical" if score >= 90 else "high" if score >= 75 else "warning" if score >= 25 else "healthy"
        preflight = (
            run_preflight(app.state.engine)
            if self.outbox_only and getattr(app.state, "engine", None) is not None
            else None
        )
        findings_by_check = {
            finding.check: finding
            for finding in (preflight.findings if preflight is not None else ())
        }
        database_state = _worst_health(
            *(
                _check_state(findings_by_check[name].status)
                for name in ("required_tables", "sqlite_integrity", "foreign_keys")
                if name in findings_by_check
            )
        )
        migration_state = _check_state(
            findings_by_check["schema"].status
            if "schema" in findings_by_check
            else "UNKNOWN"
        )
        with app.state.session_factory() as outbox_session:
            # Heartbeats and health snapshots must not change their own queue
            # metrics on the next publication within the same time bucket.
            outbox_counts = OutboxRepository(outbox_session).counts(
                exclude_event_types=("heartbeat", "health")
            )
        if outbox_counts.get("dead_letter", 0):
            outbox_state = "high"
        elif outbox_counts.get("failed", 0):
            outbox_state = "warning"
        else:
            outbox_state = "healthy"
        connectivity = getattr(app.state, "connectivity_service", None)
        connectivity_state = (
            connectivity.snapshot().state
            if connectivity is not None
            else ConnectivityState.UNKNOWN
        )
        sync_state = {
            ConnectivityState.ONLINE: "healthy",
            ConnectivityState.DEGRADED: "degraded",
            ConnectivityState.OFFLINE: "offline",
            ConnectivityState.UNKNOWN: "unknown",
        }[connectivity_state]
        nexa_state = (
            "normal"
            if str(getattr(app.state.settings, "nexa_bridge_url", "")).strip()
            else "unavailable"
        )
        previous_tenant = None if self.outbox_only else self.repository.get_tenant(tenant_id)
        previous_installation = None if self.outbox_only else self.repository.get_installation(installation_id)
        health = _worst_health(
            calculated_health,
            database_state,
            migration_state,
            outbox_state,
            sync_state,
        ) if self.outbox_only else calculated_health if observed else (
            previous_installation.health if previous_installation is not None
            else previous_tenant.health_status if previous_tenant is not None
            else "unknown"
        )
        display_name = sanitize_text(settings.get("company.name") or app.state.settings.company_name, maximum=180) or "Sua Empresa"
        version = sanitize_text(settings.get("app.version") or app.state.settings.version, maximum=80) or "unknown"
        environment = sanitize_text(app.state.settings.environment, maximum=40) or "local"
        build = sanitize_text(app.state.settings.build, maximum=80) or "unknown"
        commit = sanitize_text(
            getattr(app.state.settings, "commit", "unknown"), maximum=40
        ) or "unknown"
        channel = sanitize_text(
            getattr(app.state.settings, "channel", "LOCAL"), maximum=16
        ) or "LOCAL"
        tenant = Tenant(
            id=tenant_id, display_name=display_name, status="active", created_at=now, updated_at=now,
            erp_version=version, environment=environment, last_seen_at=now, health_status=health,
            is_demo=bool(getattr(app.state, "demo_mode", False)),
            tenant_type=("DEMO" if bool(getattr(app.state, "demo_mode", False))
                         else str(getattr(app.state.settings, "tenant_type", "CUSTOMER"))),
        )
        installation = ErpInstallation(
            id=installation_id, tenant_id=tenant_id, installation_id="primary-local", version=version,
            build=build, environment=environment, last_seen_at=now, health=health, platform="windows-local",
            metadata=sanitize_mapping({
                "product": "NexPoint ERP", "mode": environment,
                "channel": channel, "commit": commit,
            }),
            created_at=now, updated_at=now, is_demo=tenant.is_demo,
        )
        if self.outbox_only:
            outbox_payloads: list[tuple[str, str, str, dict[str, object], str]] = []
            heartbeat_bucket = int(now.timestamp() // self.interval_seconds)
            heartbeat_risk_level = report.level.casefold() if report else "normal"
            heartbeat_seen = datetime.fromtimestamp(
                heartbeat_bucket * self.interval_seconds, tz=timezone.utc
            )
            heartbeat_id = "heartbeat_" + sha256(
                (
                    f"{installation_id}:{heartbeat_bucket}:{health}:{score}:"
                    f"{heartbeat_risk_level}:{version}:{build}:{commit}:{channel}"
                ).encode()
            ).hexdigest()[:24]
            outbox_payloads.append(("heartbeat", "installation", heartbeat_id, {
                "tenant_id": tenant_id, "tenant_alias": tenant_id, "installation_id": installation_id,
                "tenant_name": display_name, "tenant_type": tenant.tenant_type,
                "version": version, "build": build, "commit": commit,
                "channel": channel, "environment": environment, "health": health,
                "risk_summary": {"score": score, "level": heartbeat_risk_level},
                "last_seen": heartbeat_seen.isoformat(),
            }, f"heartbeat:{heartbeat_id}"))
            health_id = "health_" + sha256(
                (
                    f"{installation_id}:{heartbeat_bucket}:{health}:{database_state}:"
                    f"{migration_state}:{outbox_state}:{sync_state}:{nexa_state}:{score}:"
                    f"{sum(outbox_counts.values())}:{outbox_counts.get('dead_letter', 0)}:"
                    f"{sum(event.occurrence_count for event in events)}:"
                    f"{sum(event.retry_count for event in events)}:"
                    f"{','.join(event.fingerprint for event in events)}:"
                    f"{','.join(finding.fingerprint for finding in findings[:50])}"
                ).encode()
            ).hexdigest()[:24]
            doctor_counts = {
                status: sum(
                    finding.status == status
                    for finding in (preflight.findings if preflight is not None else ())
                )
                for status in ("PASS", "WARN", "FAIL")
            }
            outbox_payloads.append(("health", "health", health_id, {
                "tenant_id": tenant_id, "installation_id": installation_id, "status": health,
                "database_state": database_state, "migration_state": migration_state,
                "outbox_state": outbox_state, "sync_state": sync_state,
                "nexa_state": nexa_state, "risk_score": score,
                "recent_errors": sum(e.occurrence_count for e in events if e.severity in {"ERROR", "CRITICAL"}),
                "retry_count": sum(e.retry_count for e in events),
                "pending_outbox": sum(
                    outbox_counts.get(state, 0)
                    for state in ("pending", "sending", "failed", "dead_letter")
                ),
                "dead_letter_count": outbox_counts.get("dead_letter", 0),
                "latency_ms": max((e.duration_ms for e in events if e.duration_ms is not None), default=None),
                "fingerprints": list(dict.fromkeys(e.fingerprint for e in events))[:20],
                "active_risk_fingerprints": [finding.fingerprint for finding in findings[:50]],
                "doctor_pass": doctor_counts["PASS"],
                "doctor_warn": doctor_counts["WARN"],
                "doctor_fail": doctor_counts["FAIL"],
                "captured_at": heartbeat_seen.isoformat(),
                "details": {
                    "event_count": sum(e.occurrence_count for e in events),
                    "preflight_status": preflight.status if preflight is not None else "UNKNOWN",
                },
            }, f"health:{health_id}"))
            if observed:
                for event in pending_events:
                    structured_store = getattr(monitor, "observability_store", None)
                    if structured_store is not None and event.sync_state != "pending":
                        continue
                    if structured_store is None and event.severity not in {"WARNING", "ERROR", "CRITICAL"}:
                        continue
                    outbox_payloads.append(("diagnostic_event", "diagnostic", event.id, {
                        "tenant_id": tenant_id, "installation_id": installation_id,
                        "event_id": event.id,
                        "timestamp": (event.first_seen_at or event.timestamp).isoformat(),
                        "level": event.severity,
                        "environment": event.environment,
                        "user_pseudonym": event.user_pseudonym,
                        "session_id": event.session_id,
                        "correlation_id": event.correlation_id,
                        "request_id": event.request_id,
                        "module": event.module,
                        "component": event.component,
                        "event_type": event.event_type,
                        "operation": event.operation,
                        "status": event.status,
                        "duration_ms": event.duration_ms,
                        "error_code": event.error_code,
                        "fingerprint": event.fingerprint,
                        "retry_count": event.retry_count,
                        "metadata": dict(event.metadata),
                        "schema_version": 1,
                        "app_version": event.app_version or version,
                        "build": event.build or build,
                    }, f"diagnostic:{installation_id}:{event.id}"))
                for finding in findings[:50]:
                    risk_id = "risk_" + sha256(f"{tenant_id}:{finding.fingerprint}".encode()).hexdigest()[:24]
                    occurrence_id = sha256(
                        f"{risk_id}:{now.isoformat()}".encode()
                    ).hexdigest()[:24]
                    outbox_payloads.append(("risk", "risk", risk_id, {
                        "tenant_id": tenant_id, "installation_id": installation_id,
                        "module": finding.module, "fingerprint": finding.fingerprint,
                        "score": finding.score, "level": finding.severity.casefold(),
                        "confidence": finding.confidence.casefold(), "evidence": list(finding.evidence[:10]),
                        "probable_cause": finding.probable_causes[0] if finding.probable_causes else None,
                        "first_seen_at": now.isoformat(), "last_seen_at": now.isoformat(),
                    }, f"risk:{risk_id}:{occurrence_id}"))
            with app.state.session_factory() as session:
                outbox = OutboxRepository(session)
                for event_type, aggregate_type, aggregate_id, payload, key in outbox_payloads:
                    outbox.enqueue(event_type=event_type, aggregate_type=aggregate_type,
                                   aggregate_id=aggregate_id, payload=payload,
                                   idempotency_key=key)
                session.commit()
            return observed
        self.repository.upsert_tenant(tenant)
        self.repository.upsert_installation(installation)
        if not observed:
            # Um monitor vazio em processo recem-iniciado nao prova saude e
            # jamais pode encerrar riscos persistidos.
            return False
        latencies = [event.duration_ms for event in events if event.duration_ms is not None]
        fingerprints = tuple(dict.fromkeys(event.fingerprint for event in events))[:20]
        self.repository.record_health_snapshot(HealthSnapshot(
            tenant_id=tenant_id, installation_id=installation_id, status=health, risk_score=score,
            recent_errors=sum(event.occurrence_count for event in events if event.severity in {"ERROR", "CRITICAL"}),
            retry_count=sum(event.retry_count for event in events),
            latency_ms=max(latencies) if latencies else None, fingerprints=fingerprints, captured_at=now,
            details=sanitize_mapping({"event_count": len(events), "source": "erp_diagnostic_monitor"}),
            is_demo=tenant.is_demo,
        ))
        published_findings = findings[:50]
        for finding in published_findings:
            risk_id = "risk_" + sha256(f"{tenant_id}:{finding.fingerprint}".encode()).hexdigest()[:24]
            self.repository.upsert_risk_summary(RiskSummary(
                id=risk_id, tenant_id=tenant_id, installation_id=installation_id,
                module=sanitize_text(finding.module, maximum=64), fingerprint=finding.fingerprint,
                score=finding.score, level=finding.severity.casefold(), confidence=finding.confidence.casefold(),
                evidence=tuple(sanitize_text(item, maximum=300) for item in finding.evidence[:10]),
                probable_cause=sanitize_text(finding.probable_causes[0], maximum=500) if finding.probable_causes else None,
                first_seen_at=now, last_seen_at=now, status="open", is_demo=tenant.is_demo,
            ))
        self.repository.reconcile_installation_risks(
            tenant_id,
            installation_id,
            tuple(finding.fingerprint for finding in published_findings),
        )
        return True
