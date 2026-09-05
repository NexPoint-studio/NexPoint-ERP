"""Inventaria e compara a transferência local, sem imprimir conteúdo de arquivos."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def snapshot(root: Path, excluded_root_entries: tuple[str, ...] = ()) -> dict:
    files = {}
    directories = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative.split("/", 1)[0] in excluded_root_entries:
            continue
        # Evidências geradas pelo próprio verificador não são dados do projeto.
        if relative == "artifacts/relocation" or relative.startswith("artifacts/relocation/"):
            continue
        if path.is_symlink() or path.is_junction():
            raise RuntimeError(f"Vínculo precisa de verificação separada: {relative}")
        if path.is_dir():
            directories.append(relative)
        elif path.is_file():
            with path.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            files[relative] = {"size": path.stat().st_size, "sha256": digest}
        else:
            raise RuntimeError(f"Tipo de arquivo inesperado: {relative}")
    return {"files": files, "directories": directories}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("snapshot", "verify"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--exclude-root-entry", action="append", default=[])
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if not root.is_dir():
        raise RuntimeError("A raiz deve ser uma pasta existente.")
    before = (json.loads(args.manifest.read_text(encoding="utf-8"))
              if args.operation == "verify" else None)
    excluded = tuple(before.get("excluded_root_entries", []) if before is not None
                     else args.exclude_root_entry)
    if any(not name or name in (".", "..") or "/" in name or "\\" in name for name in excluded):
        raise RuntimeError("Exclusões devem ser nomes exatos de entradas da raiz.")
    current = snapshot(root, excluded)
    basic = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "file_count": len(current["files"]),
        "directory_count": len(current["directories"]),
        "bytes": sum(item["size"] for item in current["files"].values()),
        "excluded_root_entries": excluded,
    }
    if args.operation == "snapshot":
        if args.manifest.exists():
            raise RuntimeError("O inventário já existe; não será sobrescrito.")
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps({**basic, **current}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(basic, ensure_ascii=False))
        return
    assert before is not None
    missing = sorted(set(before["files"]) - set(current["files"]))
    added = sorted(set(current["files"]) - set(before["files"]))
    changed = sorted(name for name in set(before["files"]) & set(current["files"])
                     if before["files"][name] != current["files"][name])
    missing_dirs = sorted(set(before["directories"]) - set(current["directories"]))
    added_dirs = sorted(set(current["directories"]) - set(before["directories"]))
    result = {**basic, "missing": missing, "added": added, "changed": changed,
              "missing_directories": missing_dirs, "added_directories": added_dirs,
              "identical": not (missing or added or changed or missing_dirs or added_dirs)}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if not result["identical"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
