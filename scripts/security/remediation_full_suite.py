"""Run the complete Python test suite in isolated, file-level QA shards.

Uses remediation_tests.py unchanged: every child owns its source export,
synthetic appdata, database fixtures and pytest basetemp. No xdist/plugin install.
Run only after implementation is stable; source drift makes the result fail.
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "scripts/security/remediation_tests.py"
ARTIFACTS = ROOT / "artifacts/security/remediation"
COUNTERS = ("tests", "failures", "errors", "skipped")
SERIAL_FILES = {"tests/test_observability_performance.py"}


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_manifest(root: Path = ROOT) -> dict[str, dict[str, object]]:
    # Match exactly the candidate source set copied by remediation_tests.py.
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
    ).decode("utf-8").split("\0")
    return {
        name: {"sha256": digest(root / name), "bytes": (root / name).stat().st_size}
        for name in sorted(set(names))
        if name and (root / name).is_file()
    }


def test_weight(path: Path) -> tuple[int, int]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    count = sum(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        for node in ast.walk(tree)
    )
    return count, path.stat().st_size


def build_shards(sources: dict[str, object], shard_count: int) -> tuple[list[str], list[list[str]]]:
    selected = sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / "tests").rglob("test_*.py"))
    if not selected:
        raise RuntimeError("No tests/test_*.py files found")
    if set(selected) - sources.keys():
        raise RuntimeError("A test file is absent from the harness source export; review locally")
    # Pytest also discovers *_test.py by default. Refuse an incomplete claim if
    # that naming convention appears later without extending this manifest.
    alternate = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests").rglob("*_test.py")
    }
    if alternate - set(selected):
        raise RuntimeError("Additional pytest filename patterns require an explicit manifest update")
    ranked = sorted(selected, key=lambda name: (*test_weight(ROOT / name), name), reverse=True)
    shards = [[] for _ in range(min(shard_count, len(ranked)))]
    for index, name in enumerate(ranked):
        shards[index % len(shards)].append(name)
    for shard in shards:
        shard.sort()
    serial = [name for name in selected if name in SERIAL_FILES]
    shards = [[name for name in shard if name not in SERIAL_FILES] for shard in shards]
    shards = [shard for shard in shards if shard]
    if serial:
        shards.append(serial)
    flattened = [name for shard in shards for name in shard]
    if sorted(flattened) != selected or len(flattened) != len(set(flattened)):
        raise RuntimeError("Shard union is incomplete or contains duplicates")
    return selected, shards


def junit_summary(path: Path) -> dict[str, object]:
    document = ET.parse(path).getroot()
    leaves = [suite for suite in document.iter("testsuite") if not suite.findall("testsuite")]
    if not leaves:
        raise ValueError("JUnit contains no test suite")
    summary: dict[str, object] = {
        key: sum(int(suite.get(key, "0")) for suite in leaves) for key in COUNTERS
    }
    summary["seconds"] = round(sum(float(suite.get("time", "0")) for suite in leaves), 3)
    summary["reported_testcases"] = sum(1 for _ in document.iter("testcase"))
    return summary


def run_shard(index: int, files: list[str], out: Path, expected: dict[str, dict[str, object]]) -> dict[str, object]:
    shard_dir = out / f"shard-{index + 1}"
    shard_dir.mkdir()
    command = [sys.executable, str(HARNESS), *files]
    write_json(shard_dir / "selection.json", {"files": files, "command": command})
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    # The child prints sanitized summaries. Raw pytest messages remain in its
    # ignored QA directory; never echo assertion data or parameter values here.
    (shard_dir / "harness.log").write_text(completed.stdout + "\n" + completed.stderr, encoding="utf-8")
    child_runs = set()
    for line in completed.stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and re.fullmatch(r"run-[0-9a-f]{10}", str(value.get("run", ""))):
            child_runs.add(value["run"])
    result: dict[str, object] = {
        "shard": index + 1, "file_count": len(files), "exit_code": completed.returncode,
        "verified": False,
    }
    if len(child_runs) != 1:
        result["error"] = "Missing or ambiguous child run identifier; inspect ignored harness.log"
        write_json(shard_dir / "result.json", result)
        return result
    child = ARTIFACTS / "tests" / child_runs.pop()
    result["child_artifacts"] = child.relative_to(ROOT).as_posix()
    junit = child / "results.xml"
    if not junit.is_file():
        result["error"] = "Missing child JUnit report"
        write_json(shard_dir / "result.json", result)
        return result
    result["junit_sha256"] = digest(junit)
    result["counts"] = junit_summary(junit)
    actual = {
        name: {"sha256": digest(child / "source" / name), "bytes": (child / "source" / name).stat().st_size}
        for name in expected if (child / "source" / name).is_file()
    }
    write_json(shard_dir / "export-source-hashes.json", actual)
    result["export_matches_source"] = actual == expected
    result["export_source_differences"] = sorted(
        name for name in expected if actual.get(name) != expected[name]
    )
    counts = result["counts"]
    result["verified"] = bool(
        completed.returncode == 0 and result["export_matches_source"]
        and counts["tests"] > 0 and counts["tests"] == counts["reported_testcases"]
        and counts["failures"] == 0 and counts["errors"] == 0
    )
    write_json(shard_dir / "result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shards", type=int, choices=range(1, 5), default=4)
    parser.add_argument("--plan-only", action="store_true", help="Write selection/hashes without executing any tests")
    args = parser.parse_args()
    started = datetime.now(timezone.utc)
    out = ARTIFACTS / "full-suite" / ("run-" + secrets.token_hex(5))
    out.mkdir(parents=True)
    sources = source_manifest()
    selected, shards = build_shards(sources, args.shards)
    write_json(out / "source-hashes-before.json", sources)
    manifest = {
        "started_at": started.isoformat(), "mode": "plan-only" if args.plan_only else "execute",
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "selection": "All tests/test_*.py recursively, every file exactly once",
        "selected_files": selected, "shards": shards,
        "union_exact": True, "duplicates": [], "source_file_count": len(sources),
        "balance": "Descending test definition count, then bytes; round robin by whole file",
        "serial_files": sorted(SERIAL_FILES),
        "serial_reason": "Absolute filesystem performance thresholds require no concurrent QA shards; assertions and thresholds are unchanged",
        "isolation": "One remediation_tests.py export/appdata/basetemp per shard; loopback only",
        "fixed_port_review": {
            "file": "tests/test_stabilization_security.py", "port": 8765,
            "note": "Whole file stays in one shard; its occupied-port check does not terminate an existing server. Other server-cycle tests request ephemeral ports. External local processes can still affect port availability.",
        },
        "limitations": "File sharding does not test shared interpreter ordering between files assigned to different shards. A definition count estimates weight, not parametrized case count or duration. This is local QA, not PROD validation.",
    }
    write_json(out / "manifest.json", manifest)
    print(json.dumps({"full_suite": out.name, "files": len(selected), "shards": len(shards), "mode": manifest["mode"]}), flush=True)
    if args.plan_only:
        return 0
    results = []
    concurrent = []
    for index, files in enumerate(shards):
        if set(files) <= SERIAL_FILES:
            result = run_shard(index, files, out, sources)
            results.append(result)
            print(json.dumps({"shard": result["shard"], "verified": result["verified"], "counts": result.get("counts")}), flush=True)
        else:
            concurrent.append((index, files))
    with ThreadPoolExecutor(max_workers=args.shards) as pool:
        futures = {pool.submit(run_shard, index, files, out, sources): index for index, files in concurrent}
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:
                result = {"shard": futures[future] + 1, "verified": False, "error_type": type(exc).__name__}
                # Exception messages can carry XML/assertion data; retain only
                # the type in the public orchestration summary.
                write_json(out / f"shard-{futures[future] + 1}" / "result.json", result)
            results.append(result)
            print(json.dumps({"shard": result["shard"], "verified": result["verified"], "counts": result.get("counts")}), flush=True)
    after = source_manifest()
    write_json(out / "source-hashes-after.json", after)
    summary = {
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 3),
        "shards": sorted(results, key=lambda result: result["shard"]),
        "counts": {key: sum(result.get("counts", {}).get(key, 0) for result in results) for key in COUNTERS},
        "source_unchanged": after == sources,
        "source_differences": sorted(name for name in sources.keys() | after.keys() if sources.get(name) != after.get(name)),
        "verified": len(results) == len(shards) and all(result["verified"] for result in results) and after == sources,
    }
    write_json(out / "summary.json", summary)
    print(json.dumps({"full_suite": out.name, "verified": summary["verified"], "source_unchanged": summary["source_unchanged"], "counts": summary["counts"]}), flush=True)
    return 0 if summary["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
