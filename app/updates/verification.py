from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from app.updates.contracts import ReleaseManifest, UpdateValidationError
from app.updates.download import valid_artifact


def windows_signature(path: Path) -> dict[str, str | None]:
    """Query Windows trust without executing or importing the downloaded file."""
    if os.name != "nt":
        raise UpdateValidationError("A assinatura da atualização precisa ser validada no Windows.")
    shell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    script = r"""
$ErrorActionPreference = 'Stop'
$sig = Get-AuthenticodeSignature -LiteralPath $env:NEXPOINT_UPDATE_SIGNATURE_FILE
$thumbprint = $null
$sha256 = $null
if ($null -ne $sig.SignerCertificate) {
    $thumbprint = $sig.SignerCertificate.Thumbprint
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try { $sha256 = [BitConverter]::ToString($algorithm.ComputeHash($sig.SignerCertificate.RawData)).Replace('-', '') }
    finally { $algorithm.Dispose() }
}
@{status=$sig.Status.ToString();thumbprint=$thumbprint;sha256=$sha256} | ConvertTo-Json -Compress
"""
    environment = dict(os.environ)
    environment["NEXPOINT_UPDATE_SIGNATURE_FILE"] = str(path.absolute())
    try:
        result = subprocess.run([str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                                env=environment, capture_output=True, timeout=60,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode != 0 or len(result.stdout) > 4096:
            raise ValueError
        value = json.loads(result.stdout.decode("utf-8-sig"))
        if not isinstance(value, dict) or set(value) != {"status", "thumbprint", "sha256"}:
            raise ValueError
        return value
    except Exception:
        raise UpdateValidationError("O Windows não conseguiu validar a assinatura da atualização.") from None


def verify_installable_artifact(path: Path, manifest: ReleaseManifest, *, signature_reader=None) -> str:
    """Recheck bytes and the declared signing policy immediately before use.

    CODE_SIGNING_PENDING allows an unsigned official artifact only. Its origin
    and expected hash must come from the authenticated release exchange.
    A broken/unknown/untrusted signature is never treated as 'unsigned'.
    """
    manifest = ReleaseManifest.parse(manifest.to_dict())
    if not valid_artifact(path, manifest):
        raise UpdateValidationError("A integridade da atualização não foi confirmada.")
    result = (signature_reader or windows_signature)(path)
    if manifest.code_signing == "AUTHENTICODE":
        expected = manifest.signer_thumbprint
        actual = result.get("thumbprint" if len(expected) == 40 else "sha256")
        if result.get("status") != "Valid" or actual != expected:
            raise UpdateValidationError("A assinatura da atualização não corresponde ao publicador autorizado.")
    elif result.get("status") != "NotSigned" or result.get("thumbprint") is not None or result.get("sha256") is not None:
        raise UpdateValidationError("O estado de assinatura difere do manifesto oficial.")
    # Detect replacement during the signature query as well as before it.
    if not valid_artifact(path, manifest):
        raise UpdateValidationError("O arquivo da atualização mudou durante a verificação.")
    return manifest.code_signing
