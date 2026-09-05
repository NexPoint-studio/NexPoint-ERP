"""Regenera somente launchers do venv atual, offline, depois de mover o ERP."""
from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path
import json
import sys

from pip._vendor.distlib.scripts import ScriptMaker


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    scripts = root / ".venv" / "Scripts"
    python = scripts / "python.exe"
    if Path(sys.executable).resolve() != python.resolve():
        raise RuntimeError("Execute com o Python da .venv deste projeto.")
    backup = root / "backups/relocation/python_before_repair_20260905/.venv/Scripts"
    launchers = sorted(path for path in scripts.glob("*.exe")
                       if path.name not in {"python.exe", "pythonw.exe"})
    entries = {entry.name: entry.value for entry in entry_points(group="console_scripts")}
    entries[f"pip{sys.version_info.major}.{sys.version_info.minor}"] = "pip._internal.cli.main:main"
    for launcher in launchers:
        if launcher.stem not in entries or not (backup / launcher.name).is_file():
            raise RuntimeError(f"Launcher sem entry point ou backup: {launcher.name}")
    maker = ScriptMaker(None, str(scripts))
    maker.executable = str(python)
    maker.clobber = True
    maker.variants = {""}
    for launcher in launchers:
        maker.make(f"{launcher.stem} = {entries[launcher.stem]}")
        if str(python).encode("utf-8") not in launcher.read_bytes():
            raise RuntimeError(f"Caminho novo ausente: {launcher.name}")
    print(json.dumps({"offline": True, "regenerated": [p.name for p in launchers]}))


if __name__ == "__main__":
    main()
