"""Activation contract checks in a fresh isolated Docker PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import secrets
import subprocess
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
RUN = secrets.token_hex(5)
NAME = "np-activation-qa-" + RUN
OUT = ROOT / "artifacts" / "activation-qa" / RUN
OUT.mkdir(parents=True)
checks = []


def docker(*args, stdin=None):
    result = subprocess.run(["docker", *args], input=stdin, text=True, encoding="utf-8", capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError(result.stderr[:2400])
    return result.stdout.strip()


def sql(text):
    return docker("exec", "-i", NAME, "psql", "-h", "127.0.0.1", "-U", "postgres", "-Atq", "-v", "ON_ERROR_STOP=1", stdin=text)


def check(name, ok):
    checks.append({"name": name, "passed": bool(ok)})
    if not ok:
        raise AssertionError(name)


def complete(grant, version, username, install, request, digest="a", key="b", tenant="qa-tenant-001", environment="prod"):
    return json.loads(sql(f"select public.np_complete_installation_activation('{grant}','{version}','{username}','{tenant}','{install}','{environment}','{request}','key_{key * 64}','{digest * 64}');"))


def prepare(username, installation):
    return json.loads(sql(f"select public.np_prepare_installation_activation('{username}','qa-tenant-001','{installation}','prod');"))


try:
    docker("run", "-d", "--name", NAME, "--network", "none", "--label", "nexpoint.audit=activation-qa", "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "postgres:17-bookworm")
    for _ in range(40):
        try:
            sql("select 1;")
            break
        except RuntimeError:
            time.sleep(.25)
    sql("create schema extensions; create role anon nologin; create role authenticated nologin; create role service_role nologin bypassrls; create schema auth; create function auth.jwt() returns jsonb language sql stable as $$ select '{}'::jsonb $$;")
    for name in ("20260920010000_prod_cloud_foundation.sql", "20260929010000_platform_sessions.sql", "20260930010000_installation_activation.sql"):
        sql((ROOT / "supabase/migrations" / name).read_text(encoding="utf-8"))
    check("migrations_apply", True)
    # Synthetic verifiers only; no local/production files or credentials loaded.
    operator = "scrypt$16384$8$1$" + "A" * 22 + "==$" + "A" * 43 + "="
    owner = "scrypt$16384$8$1$" + "B" * 22 + "==$" + "B" * 43 + "="
    sql("insert into np_tenants(tenant_key,display_name,kind,status,billing_exempt) values ('qa-tenant-001','QA Company','internal','active',true);")

    def grant(number):
        username, installation = f"qa.operator{number}", f"qa-device-{number:03}"
        sql(f"insert into np_installation_activation_grants(username,display_name,password_hash,owner_username,owner_display_name,owner_password_hash,tenant_id,installation_key,installation_label,company_name,expires_at) select '{username}','QA Operator','{operator}','qa.owner{number}','QA Owner','{owner}',id,'{installation}','QA Computer','QA Company',clock_timestamp()+interval '1 day' from np_tenants where tenant_key='qa-tenant-001';")
        value = prepare(username, installation)
        return value["grant_id"], value["auth_version"], username, installation

    values = grant(1)
    request = str(uuid4())
    first = complete(*values, request)
    check("first_activation", first.get("ok") is True and first["role"] == "user")
    check("same_request_idempotent", complete(*values, request) == first)
    check("new_request_replay_rejected", complete(*values, str(uuid4())).get("code") == "activation_conflict")
    check("new_key_rejected", complete(*values, request, digest="c").get("code") == "activation_conflict")
    check("tenant_mismatch", complete(*values, request, tenant="wrong-tenant").get("code") == "unauthorized")
    check("environment_mismatch", complete(*values, request, environment="qa").get("code") == "unauthorized")
    check("one_credential", sql("select count(*) from np_installation_credentials;") == "1")
    check("one_installation", sql("select count(*) from np_installations;") == "1")
    check("audit_no_secret", "secret" not in sql("select metadata from np_platform_audit_events;") and operator not in sql("select metadata from np_platform_audit_events;"))
    check("tenant_preserved", sql("select display_name from np_tenants;") == "QA Company")
    # Enrollment must produce a credential accepted by the existing sync RPC.
    # This heartbeat is synthetic and remains in this networkless QA container.
    envelope = json.dumps([{
        "event_type": "heartbeat", "aggregate_type": "installation",
        "aggregate_id": values[3], "schema_version": 1,
        "idempotency_key": "activation-qa-heartbeat-001",
        "payload": {"tenant_id": "qa-tenant-001", "installation_id": values[3],
                    "version": "1.1.0", "build": "QA-activation", "commit": "unknown",
                    "environment": "production", "channel": "prod", "health": "healthy"},
    }])
    def sync(digest, nonce):
        return json.loads(sql(f"select public.erp_ingest_sync_batch('{values[3]}','{digest * 64}','{nonce * 64}',clock_timestamp(),'{envelope}'::jsonb);"))
    check("activated_credential_sync_ack", sync("a", "1").get("ok") is True)
    check("sync_wrong_digest_denied", sync("b", "2").get("code") == "unauthorized")
    check("sync_heartbeat_persisted", sql("select count(*) from np_sync_envelopes where kind='heartbeat';") == "1")
    concurrent = grant(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(complete, *concurrent, str(uuid4()), digest=d, key=d) for d in ("c", "d")]
        results = [f.result() for f in futures]
    check("concurrent_one_winner", sum(r.get("ok") is True for r in results) == 1)
    check("concurrent_one_credential", sql("select count(*) from np_installation_credentials;") == "2")
    for number, assignment in ((3, "active=false"), (4, "can_activate=false"), (5, "revoked_at=clock_timestamp()"), (6, "created_at=clock_timestamp()-interval '2 days',expires_at=clock_timestamp()-interval '1 day'")):
        invalid = grant(number)
        sql(f"update np_installation_activation_grants set {assignment} where id='{invalid[0]}';")
        check(f"invalid_grant_{number}_preflight", prepare(invalid[2], invalid[3]).get("code") == "unauthorized")
        check(f"invalid_grant_{number}_race", complete(*invalid, str(uuid4())).get("code") == "unauthorized")
    check("unknown_user", prepare("unknown.user", "qa-device-999").get("code") == "unauthorized")
    expired_retry = grant(7)
    rid = str(uuid4())
    complete(*expired_retry, rid, key="e")
    sql(f"update np_installation_activation_grants set consumed_at=statement_timestamp()-interval '8 days',retry_until=statement_timestamp()-interval '1 day' where id='{expired_retry[0]}';")
    check("expired_retry", complete(*expired_retry, rid, key="e").get("code") == "unauthorized")
    changed = grant(8)
    sql(f"update np_installation_activation_grants set password_hash='{owner}',owner_password_hash='{operator}' where id='{changed[0]}';")
    check("password_change_race", complete(*changed, str(uuid4())).get("code") == "unauthorized")
    limited = grant(9)
    for _ in range(9):
        prepare(limited[2], limited[3])
    check("durable_rate_limit", prepare(limited[2], limited[3]).get("code") == "rate_limited")
    for role in ("anon", "authenticated"):
        check(f"{role}_no_grants", sql(f"select has_table_privilege('{role}','np_installation_activation_grants','select');") == "f")
        check(f"{role}_no_complete", sql(f"select has_function_privilege('{role}','np_complete_installation_activation(uuid,uuid,text,text,text,text,uuid,text,text)','execute');") == "f")
        check(f"{role}_no_prepare", sql(f"select has_function_privilege('{role}','np_prepare_installation_activation(text,text,text,text)','execute');") == "f")
    check("rls_forced", sql("select relrowsecurity and relforcerowsecurity from pg_class where relname='np_installation_activation_grants';") == "t")
    report = {"passed": True, "checks": checks, "isolated_container": NAME}
except Exception as error:
    report = {"passed": False, "checks": checks, "error_type": type(error).__name__, "detail": str(error), "isolated_container": NAME}
finally:
    (OUT / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    # Preserve failed QA state for inspection; never touch project containers.
    if report.get("passed"):
        docker("rm", "-f", NAME)

raise SystemExit(0 if report["passed"] else 1)
