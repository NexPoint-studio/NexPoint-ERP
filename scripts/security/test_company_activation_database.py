"""Company metadata and code activation QA; synthetic networkless PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import secrets
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_releases_database as qa


def main():
    checks = []
    def check(name, value):
        checks.append({"name": name, "passed": bool(value)})
        if not value:
            raise AssertionError(name)
    def rpc(name, *values):
        return json.loads(qa.sql(f"select {name}({','.join(qa.literal(v) for v in values)});"))
    passed = False
    try:
        qa.docker("run", "-d", "--name", qa.NAME, "--network", "none", "--label", "nexpoint.audit=company-activation-qa", "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "postgres:17-bookworm")
        for _ in range(40):
            try:
                qa.sql("select 1;")
                break
            except RuntimeError:
                time.sleep(.25)
        qa.sql("create schema extensions; create role anon nologin; create role authenticated nologin; create role service_role nologin bypassrls; create schema auth; create function auth.jwt() returns jsonb language sql stable as $$ select '{}'::jsonb $$; create schema storage; create table storage.buckets(id text primary key,name text,public boolean,file_size_limit bigint,allowed_mime_types text[]);")
        for path in sorted((qa.ROOT / "supabase/migrations").glob("*.sql")):
            qa.sql(path.read_text(encoding="utf-8"))
        check("all_migrations", True)
        actor, scoped = str(uuid4()), str(uuid4())
        for who, role in ((actor, "platform_admin"), (scoped, "nexpoint_control_admin")):
            qa.sql(f"insert into np_platform_users(id,username,display_name,role,password_hash) values('{who}','qa-{who}','QA','{role}','scrypt$" + "a" * 64 + "');")
        qa.sql("insert into np_tenants(tenant_key,display_name,kind) values('company-qa','Company QA','customer'),('company-other','Other','customer');")
        digest = sha256(secrets.token_bytes(32)).hexdigest()
        issue = lambda who=actor: rpc("np_admin_issue_installation_code", who, "company-qa", "device-qa-001", "PC QA", digest, 24)
        check("scoped_cannot_issue", issue(scoped)["ok"] is False)
        grant = issue()
        check("issue_code", grant["ok"] is True)
        request_id = str(uuid4())
        def consume(request=request_id, tenant="company-qa", key="a", secret="b"):
            return rpc("np_consume_installation_code", digest, tenant, "device-qa-001", request, "key_" + key * 64, secret * 64)
        check("scope_mismatch_denied", consume(tenant="company-other")["ok"] is False)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: consume(), range(2)))
        check("concurrent_retry_same_receipt", results[0] == results[1] and results[0]["ok"])
        check("no_user_hashes", not {"username", "password_hash", "owner_password_hash", "role"} & results[0].keys())
        check("new_request_denied", consume(request=str(uuid4()))["ok"] is False)
        check("new_key_denied", consume(key="c")["ok"] is False)
        check("one_credential", qa.sql("select count(*) from np_installation_credentials;") == "1")
        check("one_installation", qa.sql("select count(*) from np_installations;") == "1")

        def profile(revision, name, *, tenant="company-qa", installation="device-qa-001", secret="b"):
            identifier = "profile-qa-" + secrets.token_hex(8)
            payload = {"tenant_id": tenant, "installation_id": installation, "revision": revision, "name": "Legal name", "trade_name": name}
            item = {"event_type": "company_profile", "aggregate_type": "company", "aggregate_id": identifier, "schema_version": 1, "idempotency_key": identifier, "payload": payload}
            return json.loads(qa.sql("select erp_ingest_sync_batch(" + qa.literal(installation) + "," + qa.literal(secret * 64) + "," + qa.literal(secrets.token_hex(32)) + ",clock_timestamp()," + qa.literal(json.dumps([item])) + "::jsonb);"))
        read_name = lambda: qa.sql("select display_name from np_tenants where tenant_key='company-qa';")
        profile(2, "Renamed Company")
        check("profile_sync", read_name() == "Renamed Company")
        profile(1, "Old Name")
        check("out_of_order_ignored", read_name() == "Renamed Company")
        profile(2, "Renamed Company")
        check("equal_revision_same_payload", read_name() == "Renamed Company")
        try:
            profile(2, "Conflict")
        except RuntimeError:
            pass
        check("revision_conflict_preserves_name", read_name() == "Renamed Company")
        profile(3, "Cross tenant", tenant="company-other")
        check("cross_tenant_denied", read_name() == "Renamed Company")
        check("rename_no_duplicate", qa.sql("select count(*) from np_tenants;") == "2" and qa.sql("select count(*) from np_installations;") == "1")
        second_digest = sha256(secrets.token_bytes(32)).hexdigest()
        rpc("np_admin_issue_installation_code", actor, "company-qa", "device-qa-002", "PC QA 2", second_digest, 24)
        rpc("np_consume_installation_code", second_digest, "company-qa", "device-qa-002", str(uuid4()), "key_" + "d" * 64, "e" * 64)
        profile(99, "Other installation name", installation="device-qa-002", secret="e")
        check("secondary_does_not_overwrite_primary", read_name() == "Renamed Company")
        check("secondary_snapshot_retained", qa.sql("select count(*) from np_company_profiles;") == "2")
        check("revocation", rpc("np_admin_revoke_installation_code", actor, grant["id"])["ok"])
        check("revoked_retry_denied", consume()["ok"] is False)
        for role in ("anon", "authenticated"):
            for table in ("np_company_profiles", "np_company_profile_sources", "np_company_profile_receipts", "np_installation_activation_codes"):
                check(role + "_no_" + table, qa.sql(f"select has_table_privilege('{role}','{table}','select');") == "f")
        check("legacy_activation_disabled", qa.sql("select has_function_privilege('service_role','np_prepare_installation_activation(text,text,text,text)','execute');") == "f")
        passed = True
    except Exception as error:
        print(json.dumps({"error": type(error).__name__, "detail": str(error)[:2000]}))
    finally:
        qa.OUT.mkdir(parents=True, exist_ok=True)
        report = {"passed": passed, "checks": checks, "container": qa.NAME}
        (qa.OUT / "company-activation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
        if passed:
            qa.docker("rm", "-f", qa.NAME)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
