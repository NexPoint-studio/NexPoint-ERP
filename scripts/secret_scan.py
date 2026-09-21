"""Fail a release when a Git candidate contains local data or a likely secret."""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
BLOCKED_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log", ".pem", ".key", ".session"}
BLOCKED_PARTS = {"backups", "logs", "local-secrets", "secrets", "__pycache__", ".tmp_fase2"}
SECRET_PATTERNS = {
    "jwt": re.compile(rb"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    "github_token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    "google_key": re.compile(rb"\bAIza[A-Za-z0-9_-]{30,}\b"),
    "openai_key": re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b"),
    "supabase_secret": re.compile(rb"\bsb_secret_[A-Za-z0-9_-]{20,}\b"),
    "private_key": re.compile(rb"-----BEGIN [^-]*PRIVATE KEY-----"),
}


def git_candidates() -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return tuple(
        ROOT / name.decode("utf-8")
        for name in completed.stdout.split(b"\0")
        if name
    )


def local_values() -> tuple[bytes, ...]:
    values: list[bytes] = []
    for path in ROOT.glob(".env*"):
        if path.name == ".env.example" or not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            normalized_name = name.strip().upper()
            if not any(
                marker in normalized_name
                for marker in ("SECRET", "PASSWORD", "TOKEN", "API_KEY", "PRIVATE_KEY")
            ):
                continue
            encoded = value.strip().encode("utf-8")
            if len(encoded) >= 12 and not encoded.startswith(b"SUBSTITUA_"):
                values.append(encoded)
    return tuple(values)


def fixture_match(data: bytes, start: int, end: int) -> bool:
    window = data[max(0, start - 160):min(len(data), end + 160)].upper()
    return any(
        marker in window
        for marker in (b"CANARY", b"FAKE", b"TEST_ONLY", b"MUST_NOT", b"EXAMPLE")
    )


def blocked_path(relative: str) -> str | None:
    normalized = PurePosixPath(relative.replace("\\", "/"))
    lowered_parts = {part.casefold() for part in normalized.parts}
    name = normalized.name.casefold()
    suffix = normalized.suffix.casefold()
    if name.startswith(".env") and name != ".env.example":
        return "dotenv"
    if suffix in BLOCKED_SUFFIXES:
        return f"blocked extension {suffix}"
    if lowered_parts & BLOCKED_PARTS:
        return "local/generated directory"
    if name.startswith("credentials") and name.endswith(".json"):
        return "credential file"
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-bytes", type=int, default=10 * 1024 * 1024)
    args = parser.parse_args()
    local_secrets = local_values()
    findings: list[tuple[str, str]] = []
    checked = 0
    for path in git_candidates():
        relative = path.relative_to(ROOT).as_posix()
        reason = blocked_path(relative)
        if reason:
            findings.append((relative, reason))
            continue
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size > args.max_bytes:
            findings.append((relative, "unexpected large file"))
            continue
        data = path.read_bytes()
        checked += 1
        for label, pattern in SECRET_PATTERNS.items():
            for match in pattern.finditer(data):
                if fixture_match(data, match.start(), match.end()):
                    continue
                findings.append((relative, label))
        if any(secret in data for secret in local_secrets):
            findings.append((relative, "matches a local secret value"))
    if findings:
        print("release secret scan failed")
        for relative, reason in sorted(set(findings)):
            print(f"- {relative}: {reason}")
        return 2
    print(f"release secret scan passed ({checked} Git candidate files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
