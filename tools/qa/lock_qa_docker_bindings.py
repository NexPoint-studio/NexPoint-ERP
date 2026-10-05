"""Bind only this local QA stack's Kong/Postgres ports to host loopback.

Supabase CLI 2.119 publishes with HostIp='' even on a bridge configured with
host_binding_ipv4=127.0.0.1. This tool replaces only the two named QA
containers with equivalent Docker configurations and preserved QA volumes.
It keeps stopped originals until the replacements pass health checks; no
container from another project is stopped or removed. Do not run `supabase
start` again afterward, because the CLI could republish wide bindings.
"""
from __future__ import annotations

import copy
import http.client
import json
import subprocess
import time


PROJECT = "nexpoint-erp-handoff-qa"
NETWORK = "np-handoff-qa-loopback"
TARGETS = (("db", "5432/tcp", "56422"), ("kong", "8000/tcp", "56421"))
PIPE = r"\\.\pipe\dockerDesktopLinuxEngine"


class _Pipe:
    def __init__(self, stream):
        self.stream = stream

    def makefile(self, *_args):
        return self.stream


def api(method: str, path: str, payload=None):
    body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    for attempt in range(10):
        try:
            stream = open(PIPE, "r+b", buffering=0)
            break
        except OSError:
            if attempt == 9:
                raise
            time.sleep(0.5)
    with stream:
        stream.write(
            f"{method} /v1.55{path} HTTP/1.1\r\nHost: localhost\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n".encode() + body
        )
        response = http.client.HTTPResponse(_Pipe(stream))
        response.begin()
        raw = response.read()
        return response.status, json.loads(raw) if raw else {}


def required(method, path, payload=None, allowed=(200, 201, 204)):
    status, result = api(method, path, payload)
    if status not in allowed:
        # Docker errors may contain env/credentials; never display them.
        raise RuntimeError(f"Docker QA operation {method} failed (HTTP {status})")
    return result


def get(name):
    inspected = subprocess.run(["docker", "inspect", name], capture_output=True, text=True, timeout=15)
    if inspected.returncode:
        raise RuntimeError("Expected QA container is unavailable")
    result = json.loads(inspected.stdout)[0]
    if result["Name"] != "/" + name or result["Config"]["Labels"].get("com.supabase.cli.project") != PROJECT:
        raise RuntimeError("Container does not match the isolated QA project")
    if result["HostConfig"]["NetworkMode"] != NETWORK or set(result["NetworkSettings"]["Networks"]) != {NETWORK}:
        raise RuntimeError("QA container has an unexpected network")
    return result


def healthy(name):
    state = get(name)["State"]
    return state["Running"] and state.get("Health", {}).get("Status") == "healthy"


def clone_payload(current, exposed_port, host_port):
    source = current["Config"]
    config_keys = (
        "Image", "Hostname", "Domainname", "User", "Env", "Cmd", "Entrypoint",
        "WorkingDir", "ExposedPorts", "Healthcheck", "Labels", "Volumes",
        "StopSignal", "StopTimeout", "Tty", "OpenStdin", "AttachStdin",
        "AttachStdout", "AttachStderr", "ArgsEscaped",
    )
    payload = {key: copy.deepcopy(source[key]) for key in config_keys if source.get(key) is not None}
    host_config = copy.deepcopy(current["HostConfig"])
    if set(host_config["PortBindings"]) != {exposed_port}:
        raise RuntimeError("QA port binding has an unexpected shape")
    host_config["PortBindings"] = {exposed_port: [{"HostIp": "127.0.0.1", "HostPort": host_port}]}
    payload["HostConfig"] = host_config
    endpoint = current["NetworkSettings"]["Networks"][NETWORK]
    payload["NetworkingConfig"] = {"EndpointsConfig": {NETWORK: {"Aliases": endpoint.get("Aliases") or []}}}
    return payload


def lock_one(service, exposed_port, host_port):
    name = f"supabase_{service}_{PROJECT}"
    backup = name + "-qa-preloopback"
    current = get(name)
    if not current["State"]["Running"]:
        raise RuntimeError("QA container must be running before binding change")
    actual = current["HostConfig"]["PortBindings"][exposed_port]
    if actual == [{"HostIp": "127.0.0.1", "HostPort": host_port}]:
        return "already locked"
    if actual != [{"HostIp": "", "HostPort": host_port}]:
        raise RuntimeError("QA container has an unexpected existing bind")
    if subprocess.run(["docker", "inspect", backup], capture_output=True, timeout=15).returncode == 0:
        raise RuntimeError("Unfinished QA replacement exists; inspect before retry")
    payload = clone_payload(current, exposed_port, host_port)
    if service == "kong":
        # Supabase CLI writes QA-only kong.yml and localhost TLS files into the
        # container writable layer. Preserve them in a local, never-pushed QA
        # image; recreating from the base image would lose these files.
        runtime_image = "nexpoint-qa-kong-runtime:local"
        subprocess.run(["docker", "commit", name, runtime_image], check=True,
                       capture_output=True, timeout=90)
        payload["Image"] = runtime_image
    original_id = current["Id"]
    replacement_id = None
    stopped = False
    renamed = False
    try:
        subprocess.run(["docker", "stop", "--time", "30", original_id], check=True, capture_output=True, timeout=45)
        stopped = True
        subprocess.run(["docker", "rename", original_id, backup], check=True, capture_output=True, timeout=15)
        renamed = True
        replacement_id = required("POST", "/containers/create?name=" + name, payload, allowed=(201,))["Id"]
        subprocess.run(["docker", "start", replacement_id], check=True, capture_output=True, timeout=90)
        for _ in range(60):
            if healthy(name):
                break
            time.sleep(1)
        else:
            raise RuntimeError("Replacement QA container did not become healthy")
        final = get(name)
        ports = final["NetworkSettings"]["Ports"][exposed_port]
        if ports != [{"HostIp": "127.0.0.1", "HostPort": host_port}]:
            raise RuntimeError("QA container did not bind only to loopback")
    except Exception:
        if replacement_id:
            subprocess.run(["docker", "rm", "--force", replacement_id], capture_output=True, timeout=60)
        if renamed:
            subprocess.run(["docker", "rename", original_id, name], check=True, capture_output=True, timeout=15)
        if stopped:
            subprocess.run(["docker", "start", original_id], check=True, capture_output=True, timeout=20)
        raise
    subprocess.run(["docker", "rm", original_id], check=True, capture_output=True, timeout=20)
    return "locked and healthy"


def main():
    for service, exposed_port, host_port in TARGETS:
        result = lock_one(service, exposed_port, host_port)
        print(f"QA {service}: {result}")


if __name__ == "__main__":
    main()
