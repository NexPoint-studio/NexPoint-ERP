"""Packaged helper for protected update jobs and operator-run handoff recovery."""
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description="Atualizador NexPoint ERP")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--job", type=Path)
    mode.add_argument("--verify-runtime", action="store_true",
                      help="Verifica dependências locais sem instalar ou acessar a rede")
    mode.add_argument("--handoff-prepare", nargs=2, metavar=("OPERATION_ID", "TARGET_TENANT"))
    mode.add_argument("--handoff-resume", metavar="OPERATION_ID")
    arguments = parser.parse_args()
    try:
        from app.updates.desktop import run_update_job
        if arguments.verify_runtime:
            return 0
        if arguments.handoff_prepare or arguments.handoff_resume:
            from app.core.cloud_profile import packaged_qa
            if packaged_qa():
                raise RuntimeError("Transferencia de instalacao indisponivel no laboratorio QA.")
            if sys.platform != "win32" or not getattr(sys, "frozen", False):
                raise RuntimeError("A transferência exige o componente Windows instalado.")
            from app.core.installation_identity import InstallationCredentialStore
            from app.services.installation_handoff import InstallationHandoff
            from app.updates.desktop import ensure_no_pending_restore
            from app.updates.process_lock import installation_process_lock
            from app.updates.state import UpdateJournal
            local_app_data = os.environ.get("LOCALAPPDATA", "")
            if not local_app_data:
                raise RuntimeError("O perfil Windows precisa ser conferido.")
            root = Path(local_app_data).resolve() / "NexPoint" / "ERP"
            handoff = InstallationHandoff(root)
            with installation_process_lock(Path(sys.executable).parent):
                ensure_no_pending_restore(handoff.database)
                credentials = InstallationCredentialStore(handoff.vault).load()
                update_state = UpdateJournal(root / "updates", credentials).load()
                if update_state["state"] not in {"IDLE", "CURRENT", "SUCCEEDED", "ROLLED_BACK"}:
                    raise RuntimeError("Conclua a atualização pendente antes da transferência.")
                if arguments.handoff_prepare:
                    result = handoff.prepare(*arguments.handoff_prepare)
                else:
                    result = handoff.resume(arguments.handoff_resume)
                # Non-secret receipt for the NexPoint operator. This file stays
                # under the current Windows profile and outside the installer.
                report = root / "handoff" / ("operator-" + result["phase"] + "-" + result["operation_id"] + ".json")
                if report.exists():
                    if json.loads(report.read_text(encoding="utf-8")) != result:
                        raise RuntimeError("O comprovante local da transferência diverge.")
                else:
                    with report.open("x", encoding="utf-8") as stream:
                        json.dump(result, stream, sort_keys=True)
            return 0
        result = run_update_job(arguments.job)
        if result in {"SUCCEEDED", "ROLLED_BACK"}:
            return 0
    except Exception:
        if arguments.verify_runtime:
            return 1
    import ctypes
    message = (
        "A transferência não foi concluída. Preserve o backup e os arquivos locais. Contate a NexPoint para retomar."
        if arguments.handoff_prepare or arguments.handoff_resume else
        "A atualização não foi concluída. Não apague arquivos nem reinstale. "
        "Abra o ERP novamente ou contate a NexPoint se precisar de ajuda."
    )
    ctypes.windll.user32.MessageBoxW(None, message, "NexPoint ERP", 0x30)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
