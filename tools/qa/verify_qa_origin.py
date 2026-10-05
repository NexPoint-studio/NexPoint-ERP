"""Read-only preflight of the fixed local QA origin; never contacts PROD.

This is host evidence, not a substitute for guest/LAN firewall probes or the
Windows installation gates. Output contains checks and public metadata only.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
from pathlib import Path
import socket
import ssl
import subprocess
import tomllib


ROOT = Path(__file__).resolve().parents[2]
QA = ROOT / "artifacts" / "qa-handoff-backend-20261004"
QA_ID = "nexpoint-erp-handoff-qa"
DEV_ID = "nexpoint-erp"
HOST = "qa.nexpoint.test"
ADDRESS = "10.254.241.1"
PORT = 56430


def command(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=20)
    if result.returncode:
        # Docker output or exception text may include connection credentials.
        raise RuntimeError("Local QA command failed")
    return result.stdout


def inspect(service: str, project: str = QA_ID) -> dict:
    name = f"supabase_{service}_{project}"
    value = json.loads(command("docker", "inspect", name))
    if len(value) != 1 or value[0]["Name"] != "/" + name:
        raise RuntimeError("Unexpected local container identity")
    return value[0]


def environment(container: dict) -> dict[str, str]:
    return dict(item.split("=", 1) for item in container["Config"]["Env"] if "=" in item)


def validates_jwt(token: str, secret: str, role: str) -> bool:
    try:
        head, body, signature = token.split(".")
        decode = lambda item: base64.urlsafe_b64decode(item + "=" * (-len(item) % 4))
        header, payload = json.loads(decode(head)), json.loads(decode(body))
        expected = hmac.digest(secret.encode(), f"{head}.{body}".encode(), "sha256")
        return header.get("alg") == "HS256" and payload.get("role") == role and hmac.compare_digest(expected, decode(signature))
    except (ValueError, TypeError, KeyError):
        return False


def tls_probe(context: ssl.SSLContext, hostname: str = HOST) -> tuple[str, int]:
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with socket.create_connection((ADDRESS, PORT), timeout=5) as connection:
        with context.wrap_socket(connection, server_hostname=hostname) as secure:
            secure.sendall(f"GET /auth/v1/health HTTP/1.1\r\nHost: {HOST}:{PORT}\r\nConnection: close\r\n\r\n".encode())
            status = secure.recv(1024).split(b"\r\n", 1)[0].split()
            return secure.version() or "", int(status[1])


def run() -> dict:
    checks: dict[str, bool] = {}
    config = tomllib.loads((QA / "supabase/config.toml").read_text(encoding="utf-8"))
    checks["qa_project_identity"] = config["project_id"] == QA_ID
    checks["local_internal_ports"] = config["api"]["port"] == 56421 and config["db"]["port"] == 56422
    checks["unused_services_disabled"] = all(not config[name]["enabled"] for name in ("studio", "analytics", "realtime", "local_smtp"))
    containers = {service: inspect(service) for service in ("auth", "edge_runtime", "db", "kong", "storage", "rest")}
    checks["qa_containers_running"] = all(c["State"]["Running"] for c in containers.values())
    checks["internal_ports_loopback_only"] = all(
        containers[service]["HostConfig"]["PortBindings"] == {
            exposed: [{"HostIp": "127.0.0.1", "HostPort": port}]
        }
        and containers[service]["NetworkSettings"]["Ports"][exposed] == [
            {"HostIp": "127.0.0.1", "HostPort": port}
        ]
        for service, exposed, port in (("kong", "8000/tcp", "56421"), ("db", "5432/tcp", "56422"))
    )
    auth, edge = environment(containers["auth"]), environment(containers["edge_runtime"])
    checks["edge_public_qa_origin_pinned"] = (
        edge.get("SUPABASE_URL") == "http://kong:8000"
        and edge.get("NEXPOINT_QA_MODE") == "isolated"
        and edge.get("NEXPOINT_QA_PUBLIC_ORIGIN") == f"https://{HOST}:{PORT}"
    )
    dev_auth = environment(inspect("auth", DEV_ID))
    expected = dict(line.split("=", 1) for line in (QA / ".env.local").read_text().splitlines() if line and not line.startswith("#"))
    secret = expected["NEXPOINT_QA_JWT_SECRET"]
    checks["qa_jwt_exclusive"] = len(secret) >= 48 and auth.get("GOTRUE_JWT_SECRET") == secret and secret != dev_auth.get("GOTRUE_JWT_SECRET")
    checks["edge_jwt_matches_qa"] = edge.get("SUPABASE_INTERNAL_JWT_SECRET") == secret
    checks["qa_anon_signature"] = validates_jwt(edge.get("SUPABASE_ANON_KEY", ""), secret, "anon")
    checks["qa_service_role_signature"] = validates_jwt(edge.get("SUPABASE_SERVICE_ROLE_KEY", ""), secret, "service_role")
    checks["qa_publishable_key"] = edge.get("SUPABASE_INTERNAL_PUBLISHABLE_KEY") == expected["NEXPOINT_QA_PUBLISHABLE_KEY"]
    checks["qa_backend_secret_key"] = edge.get("SUPABASE_INTERNAL_SECRET_KEY") == expected["NEXPOINT_QA_SECRET_KEY"]
    checks["qa_key_material_distinct"] = len(set(expected.values())) == len(expected)
    def fingerprint(project: str) -> str:
        return command("docker", "exec", f"supabase_db_{project}", "psql", "-U", "postgres", "-d", "postgres", "-At", "-c", "select system_identifier from pg_control_system();").strip()
    qa_database, dev_database = fingerprint(QA_ID), fingerprint(DEV_ID)
    checks["distinct_local_database_clusters"] = qa_database.isdecimal() and dev_database.isdecimal() and qa_database != dev_database
    qa_mounts = {item["Name"] for item in containers["db"]["Mounts"] if item["Type"] == "volume"}
    dev_mounts = {item["Name"] for item in inspect("db", DEV_ID)["Mounts"] if item["Type"] == "volume"}
    checks["distinct_database_volumes"] = bool(qa_mounts) and qa_mounts.isdisjoint(dev_mounts)
    ca = QA / "qa-certs/ca.crt"
    protocol, status = tls_probe(ssl.create_default_context(cafile=str(ca)))
    checks["verified_tls_health"] = status == 200
    for label, context, hostname in (
        ("untrusted_ca_rejected", ssl.create_default_context(), HOST),
        ("unexpected_sni_rejected", ssl.create_default_context(cafile=str(ca)), "wrong.qa.nexpoint.test"),
    ):
        try:
            tls_probe(context, hostname)
        except ssl.SSLError as error:
            # Caddy can reject an unknown SNI before presenting a certificate.
            # An untrusted CA must fail certificate validation specifically.
            checks[label] = label == "unexpected_sni_rejected" or isinstance(error, ssl.SSLCertVerificationError)
        else:
            checks[label] = False
    return {
        "scope": "host preflight only; guest and physical-network probes remain separate gates",
        "origin": f"https://{HOST}:{PORT}",
        "project": QA_ID,
        "tls_protocol": protocol,
        "ca_sha256": hashlib.sha256(ca.read_bytes()).hexdigest(),
        "qa_database_fingerprint": qa_database,
        "checks": checks,
        "passed": all(checks.values()),
        "production_contacted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="Save sanitized evidence under the fixed ignored QA directory")
    args = parser.parse_args()
    try:
        result = run()
    except Exception as error:
        result = {"passed": False, "error_type": type(error).__name__, "details": "Preflight incomplete; no secrets emitted", "production_contacted": False}
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.report:
        (QA / "origin-preflight.json").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
