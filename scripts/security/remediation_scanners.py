"""Selective remediation retests on a fresh, guarded export of Git candidates.

No application is started and no production endpoint, dotenv or local vault is
read. Scanner stdout/stderr stays in memory; reports contain allowlisted metadata
only, never source snippets, Match, Secret, Raw or SecretParts. Findings require
human triage: an exit code of zero means the scan completed, not that every alert
is harmless. Exit 2 means incomplete execution or a release guard failure.

Examples (from the repository root):
  .venv/Scripts/python.exe scripts/security/remediation_scanners.py --tool secrets
  .venv/Scripts/python.exe scripts/security/remediation_scanners.py --tool bandit
  .venv/Scripts/python.exe scripts/security/remediation_scanners.py --tool pip-audit
  .venv/Scripts/python.exe scripts/security/remediation_scanners.py --tool semgrep \
      --semgrep-config path/to/previously-reviewed-rules.yml

Semgrep requires explicit local rule files to avoid silently broadening discovery
or downloading a changing ruleset. pip-audit contacts its public advisory service
with package/version metadata only; it neither resolves nor installs packages.
CodeQL and image scans have separate resource/build prerequisites and are not
implicitly started here. Original discovery artifacts are never overwritten.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = ROOT / "artifacts/security/remediation/scanners"
TOOLS_ROOT = ROOT / "artifacts/security"
SCANNERS = ("release", "secrets", "gitleaks", "trufflehog", "bandit", "semgrep", "pip-audit")
MAX_FILE_BYTES = 10 * 1024 * 1024
METADATA_NAME = re.compile(r"[A-Za-z0-9_.:/+@ -]{1,240}\Z")


def identifier(value: object) -> str:
    text = str(value or "")
    return text if METADATA_NAME.fullmatch(text) else "[metadata omitted]"


def line_number(value: object) -> int:
    return value if isinstance(value, int) and 0 <= value <= 100_000_000 else 0


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class ScanRun:
    def __init__(self, timeout: int) -> None:
        self.out = OUTPUT_ROOT / ("run-" + secrets.token_hex(5))
        self.source = self.out / "source"
        self.source.mkdir(parents=True)
        self.timeout = timeout
        self.files: set[str] = set()
        self.commands: list[dict[str, Any]] = []
        self.results: dict[str, Any] = {}
        self.env = {
            key: value for key, value in os.environ.items()
            if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "COMSPEC", "PATHEXT"}
        }
        for name in ("temp", "home", "config", "cache", "appdata"):
            (self.out / name).mkdir()
        self.env.update(
            TEMP=str(self.out / "temp"), TMP=str(self.out / "temp"),
            USERPROFILE=str(self.out / "home"),
            APPDATA=str(self.out / "appdata"), LOCALAPPDATA=str(self.out / "appdata"),
            XDG_CONFIG_HOME=str(self.out / "config"), XDG_CACHE_HOME=str(self.out / "cache"),
            PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
            PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_CONFIG_FILE=os.devnull,
            SEMGREP_SEND_METRICS="off", SEMGREP_ENABLE_VERSION_CHECK="0",
            SEMGREP_LOG_FILE=os.devnull,
            SEMGREP_SETTINGS_FILE=str(self.out / "config/semgrep-settings.yml"),
            OTEL_SDK_DISABLED="true", DO_NOT_TRACK="1",
        )

    def path(self, value: object) -> str:
        """Accept only a path that belongs to the prepared candidate inventory."""
        text = str(value or "").replace("\\", "/")
        prefix = self.source.as_posix().rstrip("/") + "/"
        if text.casefold().startswith(prefix.casefold()):
            text = text[len(prefix):]
        if text.startswith("./"):
            text = text[2:]
        return text if text in self.files else "[path outside candidate inventory]"

    def prepare(self) -> bool:
        # Import definitions only. Do NOT call local_values()/main(): these inspect
        # local dotenv values and are intentionally excluded from this harness.
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("remediation_release_guard", ROOT / "scripts/secret_scan.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("release guard unavailable")
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        names = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
        ).decode("utf-8").split("\0")
        issues = []
        manifest = []
        for name in sorted(set(names) - {""}):
            path = ROOT / name
            reason = guard.blocked_path(name)
            if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
                reason = "symlink or path outside repository"
            if reason:
                issues.append({"file": name, "reason": reason})
                continue
            if not path.is_file():
                continue  # Deleted tracked file is absent from the current tree.
            if path.stat().st_size > MAX_FILE_BYTES:
                issues.append({"file": name, "reason": "unexpected large file"})
                continue
            data = path.read_bytes()
            for label, pattern in guard.SECRET_PATTERNS.items():
                if any(not guard.fixture_match(data, item.start(), item.end()) for item in pattern.finditer(data)):
                    issues.append({"file": name, "reason": label})
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            self.files.add(name)
            manifest.append({"file": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        self.results["release"] = {"status": "FAILED" if issues else "COMPLETED", "issues": issues, "files": len(manifest)}
        write_json(self.out / "source-manifest.json", manifest)
        return not issues

    def command(self, tool: str, args: list[str], accepted: tuple[int, ...] = (0,)) -> bytes:
        started = time.monotonic()
        record: dict[str, Any] = {"tool": tool, "argv": args, "cwd": "source"}
        self.commands.append(record)
        try:
            process = subprocess.Popen(args, cwd=self.source, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=os.name != "nt")
            try:
                stdout, stderr = process.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                # Stop only the process tree started by this command. A launcher
                # can leave its scanner child alive if only its own PID is killed.
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=15)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=15)
                raise
        except (OSError, subprocess.TimeoutExpired) as error:
            record.update(status="INCOMPLETE", failure_type=type(error).__name__)
            raise RuntimeError("scanner invocation incomplete") from None
        finally:
            record["seconds"] = round(time.monotonic() - started, 3)
        record.update(exit_code=process.returncode, stdout_bytes=len(stdout), stderr_bytes=len(stderr))
        if process.returncode not in accepted:
            record["status"] = "INCOMPLETE"
            raise RuntimeError("scanner returned unexpected status; raw output withheld")
        record["status"] = "COMPLETED"
        return stdout

    def gitleaks(self) -> dict[str, Any]:
        exe = TOOLS_ROOT / "tools-secrets/gitleaks/gitleaks.exe"
        output = self.command("gitleaks", [str(exe), "dir", str(self.source), "--redact=100", "--no-banner",
            "--no-color", "--exit-code=0", "--report-format=json", "--report-path=-", "--log-level=error"])
        rows = []
        for item in json.loads(output.decode("utf-8-sig")):
            rows.append({"rule": identifier(item.get("RuleID")), "file": self.path(item.get("File")),
                "line": line_number(item.get("StartLine"))})
        return {"status": "COMPLETED", "findings": rows}

    def trufflehog(self) -> dict[str, Any]:
        exe = TOOLS_ROOT / "tools-secrets/trufflehog/trufflehog.exe"
        output = self.command("trufflehog", [str(exe), "filesystem", str(self.source), "--no-verification",
            "--no-update", "--json", "--log-level=-1", "--concurrency=2", "--fail-on-scan-errors"])
        rows = []
        for line in output.decode("utf-8-sig").splitlines():
            item = json.loads(line)
            data = item.get("SourceMetadata", {}).get("Data", {})
            location = next(iter(data.values()), {})
            # Hash in memory only. Never persist detector objects: SecretParts may
            # contain a value even when Raw has already been redacted by a tool.
            raw = str(item.get("Raw", ""))
            rows.append({"detector": identifier(item.get("DetectorName")),
                "file": self.path(location.get("file")), "line": line_number(location.get("line")),
                "fingerprint": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                "verification": "disabled"})
        return {"status": "COMPLETED", "findings": rows}

    def bandit(self) -> dict[str, Any]:
        python = TOOLS_ROOT / "tools-sast/venv/Scripts/python.exe"
        output = self.command("bandit", [str(python), "-m", "bandit", "-r", "app", "control_center", "scripts",
            "--format", "json", "--quiet"], (0, 1))
        report = json.loads(output.decode("utf-8-sig"))
        rows = [{"rule": identifier(item.get("test_id")), "file": self.path(item.get("filename")),
            "line": line_number(item.get("line_number")), "severity": identifier(item.get("issue_severity")),
            "confidence": identifier(item.get("issue_confidence"))} for item in report.get("results", [])]
        errors = [{"file": self.path(item.get("filename"))} for item in report.get("errors", [])]
        metrics = report.get("metrics", {}).get("_totals", {})
        return {"status": "INCOMPLETE" if errors else "COMPLETED", "findings": rows, "errors": errors,
            "loc": line_number(metrics.get("loc")), "nosec": line_number(metrics.get("nosec"))}

    def semgrep(self, configs: list[Path]) -> dict[str, Any]:
        if not configs:
            return {"status": "INCOMPLETE", "reason": "Explicit reviewed local --semgrep-config required"}
        copied = []
        for position, path in enumerate(configs):
            if not path.is_file() or path.suffix.lower() not in {".yml", ".yaml", ".json"}:
                return {"status": "INCOMPLETE", "reason": "Rules must be existing local YAML/JSON files"}
            target = self.out / "config" / f"semgrep-{position}{path.suffix.lower()}"
            shutil.copyfile(path, target)
            copied.extend(["--config", str(target)])
        exe = TOOLS_ROOT / "tools-sast/venv/Scripts/semgrep.exe"
        output = self.command("semgrep", [str(exe), "scan", *copied, "--json", "--metrics=off",
            "--disable-version-check", "--no-git-ignore", "--jobs=2", "--timeout=10", "app", "control_center", "scripts"])
        report = json.loads(output.decode("utf-8-sig"))
        rows = [{"rule": identifier(item.get("check_id")), "file": self.path(item.get("path")),
            "line": line_number(item.get("start", {}).get("line")),
            "severity": identifier(item.get("extra", {}).get("severity"))} for item in report.get("results", [])]
        errors = [{"type": identifier(item.get("type")), "code": line_number(item.get("code")),
            "file": self.path(item.get("path"))} for item in report.get("errors", [])]
        scanned = report.get("paths", {}).get("scanned", [])
        return {"status": "INCOMPLETE" if errors or not scanned else "COMPLETED", "findings": rows,
            "errors": errors, "scanned_files": len(scanned),
            "rule_configs": [{"file": Path(copied[i]).name, "sha256": hashlib.sha256(Path(copied[i]).read_bytes()).hexdigest()}
                for i in range(1, len(copied), 2)]}

    def pip_audit(self) -> dict[str, Any]:
        python = TOOLS_ROOT / "tools-sast/venv/Scripts/python.exe"
        reports = []
        for lock in ("requirements/windows-runtime.lock", "requirements/windows-build.lock"):
            output = self.command("pip-audit", [str(python), "-m", "pip_audit", "--no-deps", "--disable-pip",
                "--progress-spinner=off", "--format=json", "--desc=off", "--aliases=on", "--timeout=20",
                "--cache-dir", str(self.out / "cache/pip-audit"), "--requirement", lock], (0, 1))
            report = json.loads(output.decode("utf-8-sig"))
            dependencies = report.get("dependencies", [])
            rows = []
            skipped = []
            for dependency in dependencies:
                if dependency.get("skip_reason"):
                    skipped.append(identifier(dependency.get("name")))
                for item in dependency.get("vulns", []):
                    rows.append({"package": identifier(dependency.get("name")), "version": identifier(dependency.get("version")),
                        "advisory": identifier(item.get("id")), "aliases": [identifier(alias) for alias in item.get("aliases", [])],
                        "fixed_versions": [identifier(version) for version in item.get("fix_versions", [])]})
            reports.append({"lock": lock, "dependencies": len(dependencies), "skipped": skipped, "findings": rows})
        return {"status": "INCOMPLETE" if any(row["skipped"] for row in reports) else "COMPLETED", "locks": reports}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tool", action="append", choices=SCANNERS, required=True)
    parser.add_argument("--semgrep-config", action="append", type=Path, default=[])
    parser.add_argument("--timeout", type=int, default=600, help="Per command timeout in seconds (30 to 1800)")
    args = parser.parse_args()
    if not 30 <= args.timeout <= 1800:
        parser.error("--timeout must be between 30 and 1800")
    selected = list(dict.fromkeys(tool for name in args.tool for tool in (
        ("gitleaks", "trufflehog") if name == "secrets" else (name,))))
    run = ScanRun(args.timeout)
    print(json.dumps({"run": run.out.name, "tools": selected}), flush=True)
    try:
        prepared = run.prepare()
        if prepared:
            for tool in selected:
                if tool == "release":
                    continue
                try:
                    run.results[tool] = (run.semgrep([path.resolve() for path in args.semgrep_config]) if tool == "semgrep"
                        else getattr(run, tool.replace("-", "_"))())
                except (RuntimeError, ValueError, KeyError, TypeError, OSError) as error:
                    # No exception text: it can include scanner stdout or snippets.
                    run.results[tool] = {"status": "INCOMPLETE", "failure_type": type(error).__name__}
                write_json(run.out / (tool + ".json"), run.results[tool])
                print(json.dumps({"tool": tool, "status": run.results[tool]["status"]}), flush=True)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
        run.results["preparation"] = {"status": "INCOMPLETE", "failure_type": type(error).__name__}
    finally:
        write_json(run.out / "commands.json", run.commands)
        result = {"run": run.out.name, "utc": datetime.now(timezone.utc).isoformat(),
            "scope": "Git candidates only; no history, ignored installation data or provider verification",
            "results": run.results}
        write_json(run.out / "results.json", result)
    statuses = Counter(row["status"] for row in run.results.values())
    print(json.dumps({"run": run.out.name, "status_counts": dict(statuses),
        "report": str((run.out / "results.json").relative_to(ROOT))}), flush=True)
    return 2 if any(status != "COMPLETED" for status in statuses) else 0


if __name__ == "__main__":
    raise SystemExit(main())
