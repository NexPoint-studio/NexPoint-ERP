"""Compile-time cloud boundary, independent of the backend's legacy wire markers.

The physically isolated QA backend retains the prod/PROD schema contract. These
wire markers are not authority to select a destination. Only this pinned profile
selects destinations; QA manifests, UI and local telemetry remain qa/QA.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from app.core import _build_target


@dataclass(frozen=True, slots=True)
class CloudProfile:
    environment: str
    channel: str
    project_ref: str
    origin: str
    update_channel: str | None
    product_directory: str


PROFILE = CloudProfile(_build_target.ENVIRONMENT, _build_target.CHANNEL,
    _build_target.PROJECT_REF, _build_target.ORIGIN, _build_target.UPDATE_CHANNEL,
    _build_target.PRODUCT_DIRECTORY)


def packaged_qa() -> bool:
    return PROFILE.environment == "qa"


def cloud_installation(settings) -> bool:
    return settings.environment in {"prod", "production"} or (
        packaged_qa() and settings.environment == "qa" and not settings.qa_mode
    )


def origin_for(project_ref: str | None = None) -> str:
    ref = PROFILE.project_ref if project_ref is None else project_ref
    if not isinstance(ref, str) or not re.fullmatch(r"[a-z0-9]{20}", ref):
        raise ValueError("A origem cloud configurada e invalida.")
    if packaged_qa():
        if (ref != PROFILE.project_ref or PROFILE.origin != "https://qa.nexpoint.test:56430"
                or PROFILE.channel != "QA" or PROFILE.update_channel != "internal"
                or PROFILE.product_directory != "ERP-QA"):
            raise ValueError("O perfil cloud QA e inconsistente.")
        return PROFILE.origin
    return f"https://{ref}.supabase.co"


def endpoint(function: str, *, project_ref: str | None = None) -> str:
    if function not in {"erp-activate", "erp-sync", "erp-admin-recovery", "erp-update", "erp-chat", "erp-handoff"}:
        raise ValueError("A operacao cloud e invalida.")
    return origin_for(project_ref) + "/functions/v1/" + function


def require_qa_endpoint(value: str, function: str) -> None:
    if packaged_qa() and value != endpoint(function):
        raise ValueError("O destino nao pertence ao perfil QA compilado.")


def require_qa_identity(tenant: str, installation: str) -> None:
    if packaged_qa() and (tenant != "qa-nexpoint-lab" or installation != "qa-win-clean-04"):
        raise ValueError("A identidade nao pertence ao laboratorio QA.")


def require_qa_channel(channel: str) -> None:
    if packaged_qa() and channel != "internal":
        raise ValueError("O perfil QA exige o canal internal.")


def regular_path(path: Path) -> Path:
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError("O perfil QA exige pastas locais sem redirecionamento.")
    return path.resolve()
