"""Recreate only the isolated QA Edge container with explicit public QA origin.

Never modifies Supabase PROD or the developer's unrelated local Supabase stack.
The QA source tree must already contain the matching erp-update handler.
"""
from __future__ import annotations

import copy
import subprocess
import time

from lock_qa_docker_bindings import PROJECT, NETWORK, get, required


NAME = f"supabase_edge_runtime_{PROJECT}"
BACKUP = NAME + "-qa-preorigin"
REQUIRED_ENV = {
    "SUPABASE_URL": "http://kong:8000",
    "NEXPOINT_QA_MODE": "isolated",
    "NEXPOINT_QA_PUBLIC_ORIGIN": "https://qa.nexpoint.test:56430",
}


def command(*args: str) -> None:
    result = subprocess.run(args, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError("Local QA Docker lifecycle operation failed")


def run() -> str:
    current = get(NAME)
    if not current["State"]["Running"]:
        raise RuntimeError("QA Edge must be running")
    existing = dict(item.split("=", 1) for item in current["Config"]["Env"] if "=" in item)
    if existing.get("SUPABASE_URL") != REQUIRED_ENV["SUPABASE_URL"]:
        raise RuntimeError("QA Edge has an unexpected internal origin")
    if all(existing.get(key) == value for key, value in REQUIRED_ENV.items()):
        return "already configured"
    if any(key in existing and existing[key] != value for key, value in REQUIRED_ENV.items()):
        raise RuntimeError("QA Edge has a conflicting public origin")
    if subprocess.run(["docker", "inspect", BACKUP], capture_output=True, timeout=15).returncode == 0:
        raise RuntimeError("Previous QA Edge replacement requires manual inspection")
    source = current["Config"]
    keys = ("Image", "Hostname", "Domainname", "User", "Env", "Cmd", "Entrypoint",
            "WorkingDir", "ExposedPorts", "Healthcheck", "Labels", "Volumes",
            "StopSignal", "StopTimeout", "Tty", "OpenStdin", "AttachStdin",
            "AttachStdout", "AttachStderr", "ArgsEscaped")
    payload = {key: copy.deepcopy(source[key]) for key in keys if source.get(key) is not None}
    payload["Env"] = list(source["Env"]) + [
        f"{key}={value}" for key, value in REQUIRED_ENV.items() if key not in existing
    ]
    payload["HostConfig"] = copy.deepcopy(current["HostConfig"])
    endpoint = current["NetworkSettings"]["Networks"][NETWORK]
    payload["NetworkingConfig"] = {"EndpointsConfig": {NETWORK: {
        "Aliases": endpoint.get("Aliases") or []}}}
    # Supabase CLI writes /root/index.ts into the container writable layer.
    # Preserve that QA-only runtime entrypoint before replacing the container.
    runtime_image = "nexpoint-qa-edge-runtime:local"
    command("docker", "commit", NAME, runtime_image)
    payload["Image"] = runtime_image
    original_id = current["Id"]
    replacement_id = None
    stopped = renamed = False
    try:
        command("docker", "stop", "--time", "30", original_id)
        stopped = True
        command("docker", "rename", original_id, BACKUP)
        renamed = True
        replacement_id = required("POST", "/containers/create?name=" + NAME, payload, allowed=(201,))["Id"]
        command("docker", "start", replacement_id)
        for _ in range(30):
            if get(NAME)["State"]["Running"]:
                break
            time.sleep(1)
        else:
            raise RuntimeError("QA Edge replacement did not start")
        final = get(NAME)
        actual = dict(item.split("=", 1) for item in final["Config"]["Env"] if "=" in item)
        if any(actual.get(key) != value for key, value in REQUIRED_ENV.items()):
            raise RuntimeError("QA Edge origin was not pinned")
    except Exception:
        if replacement_id:
            subprocess.run(["docker", "rm", "--force", replacement_id], capture_output=True, timeout=60)
        if renamed:
            command("docker", "rename", original_id, NAME)
        if stopped:
            command("docker", "start", original_id)
        raise
    command("docker", "rm", original_id)
    return "QA Edge origin configured"


if __name__ == "__main__":
    print(run())
