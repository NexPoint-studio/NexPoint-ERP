"""Synthetic cloud reassociation QA in a networkless, disposable Postgres."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_releases_database as qa


MOVED_SCOPED = (
    "np_installation_credentials", "np_sync_envelopes", "np_sync_acks",
    "np_heartbeats", "np_health_snapshots", "np_installation_versions",
    "np_observability_events", "np_diagnostic_events", "np_fingerprints",
    "np_risks", "np_incidents", "np_support_tickets", "np_support_ticket_events",
    "np_admin_recovery_requests", "np_admin_reset_authorizations",
    "np_company_profiles", "np_company_profile_receipts",
)


def populate_scoped_history(source_id: str, installation_id: str, actor_id: str) -> None:
    """One closed/sanitized synthetic row in every other affected scope table."""
    envelope_id, ticket_id, recovery_id = str(uuid4()), str(uuid4()), str(uuid4())
    credential_id = qa.sql("select id from public.np_installation_credentials where installation_id='" +
                           installation_id + "';")
    scoped = f"'{source_id}','{installation_id}'"
    qa.sql(f"""
      insert into public.np_sync_envelopes(id,tenant_id,installation_id,idempotency_key,
        correlation_id,aggregate_type,aggregate_id,kind,schema_version,occurred_at,
        payload_hash,payload_retention_until,purge_after)
      values('{envelope_id}',{scoped},'sync-qa','corr-sync','qa','aggregate-qa',
        'heartbeat',1,clock_timestamp(),decode(repeat('b',64),'hex'),
        clock_timestamp()+interval '2 days',clock_timestamp()+interval '40 days');
      insert into public.np_sync_acks(tenant_id,installation_id,envelope_id,ack_key)
        values({scoped},'{envelope_id}','ack-qa');
      insert into public.np_health_snapshots(tenant_id,installation_id,idempotency_key,
        correlation_id,reported_at)
        values({scoped},'health-qa','corr-health',clock_timestamp());
      insert into public.np_observability_events(tenant_id,installation_id,
        idempotency_key,correlation_id,event_name,level,component,occurred_at)
        values({scoped},'obs-qa','corr-obs','qa.event','info','qa',clock_timestamp());
      insert into public.np_diagnostic_events(tenant_id,installation_id,idempotency_key,
        correlation_id,source_event_id,check_name,result,level,module,component,
        environment,operation,event_status,fingerprint,occurred_at)
        values({scoped},'diag-qa','corr-diag','source-qa','check-qa','healthy',
        'info','erp','qa','prod','inspect','ok','fingerprint-qa',clock_timestamp());
      insert into public.np_fingerprints(tenant_id,installation_id,fingerprint,
        category,first_seen_at,last_seen_at)
        values({scoped},'fingerprint-qa','qa',clock_timestamp(),clock_timestamp());
      insert into public.np_risks(tenant_id,installation_id,idempotency_key,
        correlation_id,source_risk_id,fingerprint,severity,title,detected_at)
        values({scoped},'risk-qa','corr-risk','source-risk-qa','fingerprint-qa',
        'low','Synthetic QA risk',clock_timestamp());
      insert into public.np_incidents(tenant_id,installation_id,idempotency_key,
        correlation_id,source_incident_id,severity,title,opened_at)
        values({scoped},'incident-qa','corr-incident','source-incident-qa','low',
        'Synthetic QA incident',clock_timestamp());
      insert into public.np_support_tickets(id,tenant_id,installation_id,idempotency_key,
        correlation_id,source_ticket_id,category,status,subject,sanitized_description,opened_at)
        values('{ticket_id}',{scoped},'ticket-qa','corr-ticket','source-ticket-qa',
        'qa','closed','Synthetic QA ticket','Synthetic closed ticket',clock_timestamp());
      insert into public.np_support_ticket_events(tenant_id,installation_id,ticket_id,
        idempotency_key,event_type,occurred_at)
        values({scoped},'{ticket_id}','ticket-event-qa','closed',clock_timestamp());
      insert into public.np_admin_recovery_requests(id,tenant_id,installation_id,
        support_ticket_id,idempotency_key,correlation_id,requester_fingerprint,
        status,requested_at,expires_at,decided_at)
        values('{recovery_id}',{scoped},'{ticket_id}','recovery-qa','corr-recovery',
        'requester-qa','denied',clock_timestamp()-interval '1 hour',
        clock_timestamp()+interval '1 hour',clock_timestamp());
      insert into public.np_admin_reset_authorizations(tenant_id,installation_id,
        recovery_request_id,support_ticket_id,authorization_digest,authorized_by,
        authorized_at,expires_at,revoked_at)
        values({scoped},'{recovery_id}','{ticket_id}',decode(repeat('c',64),'hex'),
        '{actor_id}',clock_timestamp()-interval '1 minute',
        clock_timestamp()+interval '1 hour',clock_timestamp());
      insert into public.np_request_nonces(tenant_id,installation_id,nonce,
        request_timestamp,expires_at)
        values({scoped},'historical-nonce-qa',clock_timestamp(),
        clock_timestamp()+interval '10 minutes');
    """)
    # Retain both consumed enrollment receipts and their credential link.
    verifier = "scrypt$16384$8$1$" + "A" * 22 + "==$" + "B" * 43 + "="
    owner_verifier = "scrypt$16384$8$1$" + "A" * 22 + "==$" + "C" * 43 + "="
    qa.sql(f"""
      insert into public.np_installation_activation_grants(username,display_name,
        password_hash,owner_username,owner_display_name,owner_password_hash,
        tenant_id,installation_key,installation_label,company_name,created_at,
        expires_at,consumed_at,consumed_request_id,credential_id,retry_until)
      values('qa-installation-user','QA User','{verifier}','qa-owner','QA Owner',
        '{owner_verifier}','{source_id}','device-qa-001','QA PC','QA Company',
        clock_timestamp()-interval '1 day',clock_timestamp()+interval '1 day',
        clock_timestamp()-interval '1 hour','{uuid4()}','{credential_id}',
        clock_timestamp()+interval '1 hour');
      insert into public.np_installation_activation_codes(code_digest,tenant_id,
        installation_key,installation_label,company_name,created_by,created_at,
        expires_at,consumed_at,consumed_request_id,credential_id,retry_until)
      values(repeat('d',64),'{source_id}','device-qa-001','QA PC','QA Company',
        '{actor_id}',clock_timestamp()-interval '1 day',
        clock_timestamp()+interval '1 day',clock_timestamp()-interval '1 hour',
        '{uuid4()}','{credential_id}',clock_timestamp()+interval '1 hour');
    """)


def main():
    checks = []
    report = {"passed": False, "checks": checks, "container": qa.NAME}
    started = False
    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        if not condition:
            raise AssertionError(name)
    def rpc(*values):
        return json.loads(qa.sql("select public.np_admin_reassociate_installation(" +
            ",".join(qa.literal(v) + ("::uuid" if i in (0, 1) else "")
                     for i, v in enumerate(values)) + ");"))
    def receipt(tenant, operation, *, secret="a", nonce="f", confirm=False):
        return json.loads(qa.sql("select public.np_erp_handoff_receipt(" +
            ",".join((qa.literal(tenant), qa.literal("device-qa-001"),
                      qa.literal(operation) + "::uuid", qa.literal(secret * 64),
                      qa.literal(nonce * 64), "clock_timestamp()",
                      "true" if confirm else "false")) + ");"))
    try:
        qa.docker("run", "-d", "--name", qa.NAME, "--network", "none", "--label",
                  "nexpoint.audit=handoff-qa", "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "postgres:17-bookworm")
        started = True
        for _ in range(100):
            try:
                qa.sql("select 1;")
                break
            except RuntimeError:
                time.sleep(.25)
        qa.sql("create schema extensions; create role anon nologin; create role authenticated nologin; "
               "create role service_role nologin bypassrls; create schema auth; "
               "create function auth.jwt() returns jsonb language sql stable as $$ select '{}'::jsonb $$; "
               "create schema storage; create table storage.buckets(id text primary key,name text,"
               "public boolean,file_size_limit bigint,allowed_mime_types text[]);")
        for path in sorted((qa.ROOT / "supabase/migrations").glob("*.sql")):
            qa.sql(path.read_text(encoding="utf-8"))
        check("migrations_apply", True)
        actor, denied = str(uuid4()), str(uuid4())
        for user, role in ((actor, "platform_admin"), (denied, "nexpoint_control_admin")):
            qa.sql("insert into public.np_platform_users(id,username,display_name,role,password_hash) values("
                   f"'{user}','qa-{user}','QA','{role}','scrypt$" + "a" * 64 + "');")
        qa.sql("insert into public.np_tenants(tenant_key,display_name,kind) "
               "values('source-qa','NexPoint QA','internal');")
        source_id = qa.sql("select id from public.np_tenants where tenant_key='source-qa';")
        for key in ("source-home", "device-qa-001", "device-qa-002"):
            qa.sql("insert into public.np_installations(tenant_id,installation_key,label,environment,channel) "
                   f"values('{source_id}','{key}','QA device','prod','prod');")
        first_id = qa.sql("select id from public.np_installations where installation_key='device-qa-001';")
        second_id = qa.sql("select id from public.np_installations where installation_key='device-qa-002';")
        for device in (first_id, second_id):
            qa.sql("insert into public.np_installation_credentials(tenant_id,installation_id,key_id,kind,secret_digest) "
                   f"values('{source_id}','{device}','key-{device}','hmac_sha256',decode(repeat('a',64),'hex'));")
        qa.sql("update public.np_company_profile_sources set installation_id='" + first_id +
               "' where tenant_id='" + source_id + "';")
        qa.sql("insert into public.np_company_profiles(tenant_id,installation_id,revision,name,trade_name,payload_hash) "
               f"values('{source_id}','{first_id}',3,'Legal QA','Nil QA',decode(repeat('b',64),'hex'));")
        qa.sql("insert into public.np_company_profile_receipts(tenant_id,installation_id,revision,payload_hash) "
               f"values('{source_id}','{first_id}',3,decode(repeat('b',64),'hex'));")
        qa.sql("insert into public.np_heartbeats(tenant_id,installation_id,idempotency_key,correlation_id,"
               "reported_at,app_version,build_id,sync_status) "
               f"values('{source_id}','{first_id}','hb-qa','corr-qa',clock_timestamp(),"
               "'1.1.0','build-qa','synced');")
        qa.sql("insert into public.np_installation_versions(tenant_id,installation_id,idempotency_key,"
               "correlation_id,version,build_id,build_commit,environment,channel,reported_at) "
               f"values('{source_id}','{first_id}','ver-qa','corr-ver','1.1.0','build-qa',"
               "'unknown','prod','prod',clock_timestamp());")
        populate_scoped_history(source_id, first_id, actor)
        op = str(uuid4())
        args = (actor, op, "source-qa", "target-qa", "Nil QA", "device-qa-001",
                "PC Nil QA", "c" * 64, "d" * 64)
        check("non_admin_denied", rpc(denied, *args[1:])["code"] == "unauthorized")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: rpc(*args), range(2)))
        moved = results[0]
        check("concurrent_same_operation_idempotent", results[1] == moved)
        check("cloud_move", moved["ok"] and moved["state"] == "cloud_committed")
        check("idempotent_retry", rpc(*args) == moved)
        fetched = receipt("source-qa", op)
        check("old_scope_resume_only_committed_receipt", fetched["ok"] and
              fetched["target_tenant_id"] == "target-qa" and
              fetched["backup_sha256"] == "c" * 64)
        check("replayed_receipt_rejected", receipt("source-qa", op)["code"] == "replay")
        check("other_scope_receipt_denied", receipt("unrelated-qa", op, nonce="1")["code"] == "unauthorized")
        check("wrong_secret_receipt_denied", receipt("source-qa", op, secret="2", nonce="2")["code"] == "unauthorized")
        check("old_scope_cannot_confirm", receipt("source-qa", op, nonce="3", confirm=True)["code"] == "old_scope_cannot_confirm")
        check("new_scope_can_confirm", receipt("target-qa", op, nonce="4", confirm=True)["state"] == "confirmed")
        check("confirmation_idempotent", receipt("target-qa", op, nonce="5", confirm=True)["state"] == "confirmed")
        check("changed_receipt_rejected", rpc(*args[:-1], "e" * 64)["code"] == "idempotency_conflict")
        check("new_operation_rejected", rpc(actor, str(uuid4()), *args[2:])["code"] == "source_scope_mismatch")
        target_id = qa.sql("select id from public.np_tenants where tenant_key='target-qa';")
        check("two_tenants_distinct", target_id != source_id and
              qa.sql("select count(*) from public.np_tenants;") == "2")
        check("one_original_installation", qa.sql("select count(*) from public.np_installations "
              "where installation_key='device-qa-001';") == "1")
        check("installation_uuid_stable", qa.sql("select id from public.np_installations "
              "where installation_key='device-qa-001' and tenant_id='" + target_id + "';") == first_id)
        check("source_home_preserved", qa.sql("select count(*) from public.np_installations "
              "where tenant_id='" + source_id + "';") == "2")
        check("source_profile_reassigned", qa.sql("select installation_id from "
              "public.np_company_profile_sources where tenant_id='" + source_id + "';") != first_id)
        check("target_profile_source", qa.sql("select installation_id from "
              "public.np_company_profile_sources where tenant_id='" + target_id + "';") == first_id)
        for table in MOVED_SCOPED:
            check("all_scoped_rows_" + table, qa.sql("select count(*) from public." + table +
                  " where tenant_id='" + target_id + "' and installation_id='" + first_id + "';") == "1" and
                  qa.sql("select count(*) from public." + table + " where tenant_id='" + source_id +
                  "' and installation_id='" + first_id + "';") == "0")
        check("historical_nonce_preserved", qa.sql(
              "select count(*) from public.np_request_nonces where tenant_id='" + target_id +
              "' and installation_id='" + first_id + "' and nonce='historical-nonce-qa';") == "1" and
              qa.sql("select count(*) from public.np_request_nonces where tenant_id='" + source_id +
              "' and installation_id='" + first_id + "';") == "0")
        for table in ("np_installation_activation_grants", "np_installation_activation_codes"):
            check("enrollment_receipt_" + table, qa.sql("select count(*) from public." + table +
                  " where tenant_id='" + target_id + "' and installation_key='device-qa-001';") == "1")
        for table in ("np_installation_credentials", "np_heartbeats", "np_installation_versions",
                      "np_company_profiles", "np_company_profile_receipts"):
            check("scope_" + table, qa.sql("select count(*) from public." + table +
                  " where tenant_id='" + target_id + "' and installation_id='" + first_id + "';") == "1")
        check("receipt_hash_unchanged", qa.sql("select encode(payload_hash,'hex') from "
              "public.np_company_profile_receipts where installation_id='" + first_id + "';") == "b" * 64)
        check("stable_update_channel", qa.sql("select update_channel from public.np_installations "
              "where id='" + first_id + "';") == "stable")
        profile_id = "profile-qa-" + str(uuid4())
        payload = {"tenant_id": "target-qa", "installation_id": "device-qa-001",
                   "revision": 4, "name": "Legal QA", "trade_name": "Nil QA Renamed"}
        event = {"event_type": "company_profile", "aggregate_type": "company",
                 "aggregate_id": profile_id, "schema_version": 1,
                 "idempotency_key": profile_id, "payload": payload}
        response = json.loads(qa.sql("select public.erp_ingest_sync_batch(" +
            qa.literal("device-qa-001") + "," + qa.literal("a" * 64) + "," +
            qa.literal("e" * 64) + ",clock_timestamp()," +
            qa.literal(json.dumps([event])) + "::jsonb);"))
        check("future_sync_new_scope", response["ok"] is True)
        check("future_rename_only_target", qa.sql("select display_name from public.np_tenants "
              "where id='" + target_id + "';") == "Nil QA Renamed" and
              qa.sql("select display_name from public.np_tenants where id='" + source_id + "';") == "NexPoint QA")
        check("new_profile_revision", qa.sql("select revision from public.np_company_profiles "
              "where installation_id='" + first_id + "';") == "4")
        check("old_and_new_receipts", qa.sql("select count(*) from public.np_company_profile_receipts "
              "where installation_id='" + first_id + "';") == "2")
        check("one_audit", qa.sql("select count(*) from public.np_platform_audit_events "
              "where action='installation.reassociated';") == "1")
        check("anon_no_handoff_receipt", qa.sql("select has_table_privilege('anon',"
              "'public.np_installation_handoffs','select');") == "f")
        check("authenticated_no_transfer_rpc", qa.sql("select has_function_privilege('authenticated',"
              "'public.np_admin_reassociate_installation(uuid,uuid,text,text,text,text,text,text,text)',"
              "'execute');") == "f")
        def visible_as(tenant):
            return qa.sql("begin; grant select on public.np_installations to authenticated; "
                "set local role authenticated; set local request.jwt.claims=" +
                qa.literal(json.dumps({"tenant_id": tenant})) + "; "
                "select count(*) from public.np_installations; rollback;")
        check("source_rls_keeps_only_own_installations", visible_as(source_id) == "2")
        check("target_rls_keeps_only_own_installations", visible_as(target_id) == "1")
        check("invalid_claim_sees_nothing", visible_as("not-a-uuid") == "0")
        check("anon_no_installations", qa.sql("select has_table_privilege('anon',"
              "'public.np_installations','select');") == "f")
        def visible_scoped(tenant, table):
            return qa.sql("begin; grant select on public." + table + " to authenticated; "
                "set local role authenticated; set local request.jwt.claims=" +
                qa.literal(json.dumps({"tenant_id": tenant})) + "; "
                "select count(*) from public." + table + " where installation_id='" +
                first_id + "'; rollback;")
        for table in (*[t for t in MOVED_SCOPED if t not in
                         {"np_company_profiles", "np_company_profile_receipts"}],
                      "np_request_nonces"):
            check("rls_" + table,
                  visible_scoped(source_id, table) == "0" and
                  int(visible_scoped(target_id, table)) >= 1 and
                  visible_scoped("not-a-uuid", table) == "0")
        for table in ("np_company_profiles", "np_company_profile_receipts",
                      "np_company_profile_sources"):
            check("private_rls_" + table,
                  all(visible_scoped(tenant, table) == "0" for tenant in
                      (source_id, target_id, "not-a-uuid")))
        second_op = str(uuid4())
        second_args = (actor, second_op, "source-qa", "target-qa", "Nil QA Renamed",
                       "device-qa-002", "Second QA PC", "6" * 64, "7" * 64)
        check("existing_target_name_mismatch_blocked",
              rpc(*second_args[:4], "Unexpected Company", *second_args[5:])["code"] == "target_invalid")
        check("existing_target_reassociation", rpc(*second_args)["state"] == "cloud_committed")
        check("existing_target_canonical_source_preserved", qa.sql(
              "select installation_id from public.np_company_profile_sources where tenant_id='" +
              target_id + "';") == first_id)
        home_id = qa.sql("select id from public.np_installations where installation_key='source-home';")
        check("origin_canonical_source_replaced_again", qa.sql(
              "select installation_id from public.np_company_profile_sources where tenant_id='" +
              source_id + "';") == home_id)
        check("second_installation_uuid_and_credential_preserved",
              qa.sql("select id from public.np_installations where installation_key='device-qa-002' "
                     "and tenant_id='" + target_id + "';") == second_id and
              qa.sql("select count(*) from public.np_installation_credentials where installation_id='" +
                     second_id + "' and tenant_id='" + target_id + "';") == "1")
        check("origin_installation_preserved", qa.sql(
              "select count(*) from public.np_installations where tenant_id='" + source_id + "';") == "1")
        report["passed"] = True
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)[:1800]}
    finally:
        if started:
            label = qa.docker("inspect", "--format", '{{index .Config.Labels "nexpoint.audit"}}', qa.NAME)
            if label == "handoff-qa":
                qa.docker("rm", "-f", qa.NAME)
                report["container_removed"] = True
        qa.OUT.mkdir(parents=True, exist_ok=True)
        path = qa.OUT / "handoff-database.json"
        path.write_text(json.dumps(report,indent=2),encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "checks": checks,
                          "report": str(path), "error": report.get("error")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
