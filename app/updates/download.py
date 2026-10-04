from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import secrets
import shutil
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from app.updates.contracts import ReleaseDownload, ReleaseManifest, UpdateValidationError
from app.core.config import PRODUCTION_SUPABASE_PROJECT_REF


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def _open(url: str):
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    opener = build_opener(HTTPSHandler(context=context), _NoRedirect())
    return opener.open(Request(url, headers={"User-Agent": "NexPoint-ERP-Updater/1"}), timeout=30)


def valid_artifact(path: Path, manifest: ReleaseManifest) -> bool:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != manifest.artifact_size:
        return False
    digest = sha256()
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            return False
        stream.seek(0)
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return secrets.compare_digest(digest.hexdigest(), manifest.artifact_sha256)


def _safe_directory(path: Path) -> Path:
    path = path.absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise UpdateValidationError("A pasta de atualização não pode usar redirecionamento.")
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve(strict=True)


def download_release(download: ReleaseDownload, directory: Path, *, opener=None,
                     disk_usage=shutil.disk_usage,
                     project_ref: str = PRODUCTION_SUPABASE_PROJECT_REF) -> Path:
    """Download only after authenticated manifest/URL parsing; do not execute it."""
    manifest = ReleaseManifest.parse(download.manifest.to_dict())
    download = ReleaseDownload.parse(manifest, {
        "url": download.url, "expires_at": download.expires_at.isoformat(),
    }, project_ref=project_ref)
    target_dir = _safe_directory(Path(directory))
    target = target_dir / f"{manifest.artifact_sha256}.exe"
    if valid_artifact(target, manifest):
        return target
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise UpdateValidationError("O destino da atualização é inválido.")
        target.unlink()
    if download.expires_at <= datetime.now(timezone.utc):
        raise UpdateValidationError("A autorização de download expirou. Verifique novamente.")
    if disk_usage(target_dir).free < manifest.artifact_size * 2 + 16 * 1024 * 1024:
        raise UpdateValidationError("Não há espaço livre suficiente para baixar a atualização.")
    partial = target_dir / f".{manifest.artifact_sha256}.{secrets.token_hex(16)}.partial"
    try:
        with (opener or _open)(download.url) as response:
            if response.geturl() != download.url or response.getcode() != 200:
                raise UpdateValidationError("O servidor recusou o download oficial.")
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdecimal() or int(length) != manifest.artifact_size):
                raise UpdateValidationError("O tamanho recebido não corresponde à atualização.")
            received = 0
            with partial.open("xb") as stream:
                while block := response.read(min(1024 * 1024, manifest.artifact_size - received + 1)):
                    received += len(block)
                    if received > manifest.artifact_size:
                        raise UpdateValidationError("O arquivo recebido excede o tamanho autorizado.")
                    stream.write(block)
                stream.flush()
                os.fsync(stream.fileno())
        if not valid_artifact(partial, manifest):
            raise UpdateValidationError("A integridade da atualização não foi confirmada. Nada foi instalado.")
        os.replace(partial, target)
        return target
    except UpdateValidationError:
        raise
    except (OSError, HTTPError, URLError, TimeoutError):
        raise UpdateValidationError("Não foi possível baixar a atualização. Seus dados foram preservados.") from None
    finally:
        with suppress(OSError):
            partial.unlink(missing_ok=True)
