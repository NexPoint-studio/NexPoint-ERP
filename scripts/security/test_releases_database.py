"""Release lifecycle/RLS regression in synthetic, networkless PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
import subprocess
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
NAME = "np-release-qa-" + secrets.token_hex(5)
OUT = ROOT / "artifacts" / "release-qa" / NAME
CHECKS = []


def docker(*args, stdin=None):
    result = subprocess.run(["docker", *args], input=stdin, text=True, encoding="utf-8",
                            capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError(result.stderr[:2400])
    return result.stdout.strip()


def sql(statement):
    return docker("exec", "-i", NAME, "psql", "-h", "127.0.0.1", "-U", "postgres",
                  "-Atq", "-v", "ON_ERROR_STOP=1", stdin=statement)


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def check(name, condition):
    CHECKS.append({"name": name, "passed": bool(condition)})
    if not condition:
        raise AssertionError(name)


def main():
    OUT.mkdir(parents=True)
    report = {"passed": False, "checks": CHECKS, "isolated_container": NAME}
    try:
        docker("run", "-d", "--name", NAME, "--network", "none", "--label",
               "nexpoint.audit=release-qa", "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "postgres:17-bookworm")
        for _ in range(40):
            try:
                sql("select 1;")
                break
            except RuntimeError:
                time.sleep(.25)
        sql("create schema extensions; create role anon nologin; create role authenticated nologin; "
            "create role service_role nologin bypassrls; create schema auth; "
            "create function auth.jwt() returns jsonb language sql stable as $$ select '{}'::jsonb $$;")
        sql("create schema storage; create table storage.buckets("
            "id text primary key, name text not null, public boolean not null, file_size_limit bigint, allowed_mime_types text[]);")
        for path in sorted((ROOT / "supabase/migrations").glob("*.sql")):
            sql("begin;\n" + path.read_text(encoding="utf-8") + "\ncommit;")
        check("migrations_apply", True)
        check("release_bucket_private", sql("select public from storage.buckets where id='nexpoint-releases';") == "f")
        check("release_bucket_free_compatible_limit", sql("select file_size_limit from storage.buckets where id='nexpoint-releases';") == str(50 * 1024 * 1024))
        admin, scoped, inactive = (str(uuid4()) for _ in range(3))
        for identifier, role, active in ((admin, "platform_admin", True),
                                         (scoped, "nexpoint_control_admin", True),
                                         (inactive, "platform_admin", False)):
            sql(f"insert into np_platform_users(id,username,display_name,role,password_hash,active) "
                f"values('{identifier}','qa-{identifier}','QA admin','{role}','scrypt$" + "a" * 64 + f"',{str(active).lower()});")
        sql("insert into np_tenants(tenant_key,display_name,kind) values('qa-internal','QA internal','internal'),('qa-customer','QA customer','customer');")
        for key, tenant in (("qa-update-internal", "qa-internal"), ("qa-update-customer", "qa-customer")):
            sql(f"insert into np_installations(tenant_id,installation_key,label,environment,channel) select id,'{key}','QA computer','prod','prod' from np_tenants where tenant_key='{tenant}';")
        sql("insert into np_installation_credentials(tenant_id,installation_id,key_id,kind,secret_digest) select tenant_id,id,'qa-update-key','hmac_sha256',decode(repeat('a',64),'hex') from np_installations;")
        check("customer_stable_default", sql("select update_channel from np_installations where installation_key='qa-update-customer';") == "stable")
        check("internal_default", sql("select update_channel from np_installations where installation_key='qa-update-internal';") == "internal")
        check("environment_unchanged", sql("select count(*) from np_installations where environment='prod' and channel='prod';") == "2")
        metadata = dict(version="1.2.0", update_channel="stable", platform="windows", architecture="x64",
                        build_commit="a" * 40, artifact_sha256="b" * 64, artifact_size=1024,
                        release_notes="QA synthetic release", minimum_supported_version="1.0.0",
                        mandatory=False, code_signing="CODE_SIGNING_PENDING", signer_thumbprint=None)

        def create(values=metadata, actor=admin):
            return json.loads(sql(f"select np_admin_create_release('{actor}',{literal(json.dumps(values))}::jsonb);"))

        def publish(identifier, actor=admin):
            return json.loads(sql(f"select np_admin_publish_release('{actor}','{identifier}');"))

        def verify(identifier, digest="b" * 64, size=1024, actor=admin):
            return json.loads(sql(f"select np_admin_verify_release('{actor}','{identifier}','{digest}',{size});"))

        def lookup(tenant="qa-customer", installation="qa-update-customer", digest="a" * 64,
                   nonce=None, version="1.1.0", sent="clock_timestamp()"):
            nonce = nonce or secrets.token_hex(32)
            return json.loads(sql(f"select np_erp_check_release('{tenant}','{installation}','{digest}',"
                                  f"'{nonce}',{sent},'{version}');"))

        check("scoped_admin_cannot_create", create(actor=scoped).get("code") == "unauthorized")
        check("inactive_admin_cannot_create", create(actor=inactive).get("code") == "unauthorized")
        first = create()
        release = first["release_id"]
        check("draft_created", first["status"] == "draft")
        check("create_idempotent", create() == first)
        check("duplicate_metadata_conflict", create({**metadata, "artifact_size": 1000}).get("code") == "idempotency_conflict")
        check("unverified_publish_denied", publish(release).get("code") == "release_not_verified")
        check("draft_not_distributed", lookup()["release"] is None)
        check("wrong_hash_rejected", verify(release, digest="c" * 64).get("ok") is False)
        check("wrong_size_rejected", verify(release, size=1023).get("ok") is False)
        check("scoped_admin_cannot_verify", verify(release, actor=scoped).get("code") == "unauthorized")
        check("verified", verify(release).get("ok") is True)
        check("verified_not_distributed", lookup()["release"] is None)
        check("scoped_admin_cannot_publish", publish(release, actor=scoped).get("code") == "unauthorized")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(publish, [release, release]))
        check("publish_concurrent_idempotent", results[0] == results[1] and results[0].get("ok") is True)
        check("one_publish_audit", sql("select count(*) from np_platform_audit_events where action='release.published';") == "1")
        manifest = lookup()["release"]
        check("published_manifest", manifest["release_id"] == release and manifest["version"] == "1.2.0")
        check("no_privileged_secret_or_url", not any(word in json.dumps(manifest).lower() for word in ("password", "secret", "token", "https:")))
        check("stable_not_internal", lookup("qa-internal", "qa-update-internal")["release"] is None)
        check("cross_tenant_rejected", lookup("qa-internal").get("code") == "unauthorized")
        check("wrong_credential_rejected", lookup(digest="f" * 64).get("code") == "unauthorized")
        check("expired_request_rejected", lookup(sent="clock_timestamp()-interval '2 minutes'").get("code") == "unauthorized")
        nonce = secrets.token_hex(32)
        check("new_nonce", lookup(nonce=nonce).get("ok") is True)
        check("replay_rejected", lookup(nonce=nonce).get("code") == "replay")
        check("already_current", lookup(version="1.2.0")["release"] is None)
        check("newer_than_catalog_no_downgrade", lookup(version="2.0.0")["release"] is None)
        older = create({**metadata, "version": "1.1.5"})["release_id"]
        verify(older)
        check("publishing_downgrade_rejected", publish(older).get("code") == "release_downgrade")
        for key, value in (("version", "1.02.0"), ("artifact_sha256", "wrong"), ("artifact_size", -1),
                           ("update_channel", "prod"), ("signer_thumbprint", "A" * 40),
                           ("minimum_supported_version", "9.0.0"), ("unexpected", "injected")):
            check("invalid_" + key, create({**metadata, "version": "1.3.0", key: value}).get("code") == "invalid_payload")
        for _ in range(12):
            limited = lookup()
        check("durable_rate_limit", limited.get("code") == "rate_limited")
        for role in ("anon", "authenticated"):
            check(role + "_no_catalog", sql(f"select has_table_privilege('{role}','np_erp_releases','select');") == "f")
            for signature in ("np_admin_create_release(uuid,jsonb)", "np_admin_verify_release(uuid,uuid,text,bigint)",
                              "np_admin_publish_release(uuid,uuid)", "np_erp_check_release(text,text,text,text,timestamptz,text)"):
                check(role + "_no_" + signature, sql(f"select has_function_privilege('{role}','{signature}','execute');") == "f")
        check("service_cannot_skip_lifecycle", sql("select has_table_privilege('service_role','np_erp_releases','update');") == "f")
        check("catalog_rls_forced", sql("select relrowsecurity and relforcerowsecurity from pg_class where relname='np_erp_releases';") == "t")
        check("credentials_preserved", sql("select count(*) from np_installation_credentials where secret_digest=decode(repeat('a',64),'hex');") == "2")
        def heartbeat(state=None, channel="stable", seconds=0, tenant="qa-customer", key=None):
            key = key or "qa-heartbeat-" + secrets.token_hex(8)
            payload = {"tenant_id": tenant, "installation_id": "qa-update-customer",
                       "version": "1.1.0", "build": "QA-update-telemetry", "commit": "a" * 40,
                       "channel": "PROD", "environment": "production", "health": "healthy",
                       "last_seen": (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()}
            if state is not None:
                payload.update(update_state=state, update_channel=channel, update_observed_at=payload["last_seen"])
            item = {"event_type": "heartbeat", "aggregate_type": "installation", "aggregate_id": key,
                    "payload": payload, "schema_version": 1, "idempotency_key": key}
            return json.loads(sql("select public.erp_ingest_sync_batch('qa-update-customer','" + "a" * 64 +
                "','" + secrets.token_hex(32) + "',clock_timestamp()," + literal(json.dumps([item])) + "::jsonb);"))
        update_state = lambda: sql("select coalesce(update_state,'null') from np_installations where installation_key='qa-update-customer';")
        heartbeat()
        check("legacy_heartbeat_no_fake_update_state", update_state() == "null")
        accepted = heartbeat("AVAILABLE")
        check("heartbeat_ingestion_update_state", update_state() == "AVAILABLE")
        heartbeat("FAILED", seconds=-30)
        check("stale_update_heartbeat_ignored", update_state() == "AVAILABLE")
        heartbeat("READY", channel="internal")
        check("heartbeat_cannot_change_channel", update_state() == "AVAILABLE" and sql("select update_channel from np_installations where installation_key='qa-update-customer';") == "stable")
        heartbeat("arbitrary-state")
        check("invalid_state_ignored_without_breaking_sync", update_state() == "AVAILABLE")
        heartbeat("FAILED", seconds=600)
        check("future_update_timestamp_rejected", update_state() == "AVAILABLE")
        heartbeat("FAILED", tenant="qa-internal")
        check("cross_tenant_update_state_denied", update_state() == "AVAILABLE")
        heartbeat("SUCCEEDED")
        check("successful_update_reported", update_state() == "SUCCEEDED")
        check("other_installation_unchanged", sql("select update_state is null from np_installations where installation_key='qa-update-internal';") == "t")
        check("update_trigger_not_callable", sql("select has_function_privilege('service_role','np_capture_update_heartbeat()','execute');") == "f")
        report["passed"] = True
    except Exception as error:
        report.update(error_type=type(error).__name__, detail=str(error))
    finally:
        (OUT / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
        if report["passed"]:
            docker("rm", "-f", NAME)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
