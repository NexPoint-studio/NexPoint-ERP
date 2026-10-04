"""Inventory handoff dependencies in disposable PostgreSQL; never connects to PROD.

This is a schema/coverage check, not a transfer implementation or authorization.
Only catalog metadata is recorded. All migrations run in a networkless container.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_releases_database as qa


SCOPED = {
    "np_installation_credentials", "np_sync_envelopes", "np_sync_acks",
    "np_heartbeats", "np_health_snapshots", "np_installation_versions",
    "np_observability_events", "np_diagnostic_events", "np_fingerprints",
    "np_risks", "np_incidents", "np_support_tickets", "np_support_ticket_events",
    "np_admin_recovery_requests", "np_admin_reset_authorizations", "np_request_nonces",
    "np_company_profiles", "np_company_profile_sources", "np_company_profile_receipts",
}
ENROLLMENT = {"np_installation_activation_grants", "np_installation_activation_codes"}
HANDOFF_META = {"np_installation_handoffs"}

CATALOG = """
select coalesce(jsonb_agg(jsonb_build_object(
 'schema', n.nspname, 'table', c.relname, 'name', f.conname,
 'parent_schema', pn.nspname, 'parent', p.relname,
 'columns', (select jsonb_agg(a.attname order by k.ord)
   from unnest(f.conkey) with ordinality k(num,ord)
   join pg_attribute a on a.attrelid=c.oid and a.attnum=k.num),
 'definition', pg_get_constraintdef(f.oid), 'deferrable', f.condeferrable,
 'initially_deferred', f.condeferred
) order by n.nspname,c.relname,f.conname), '[]'::jsonb)
from pg_constraint f join pg_class c on c.oid=f.conrelid
join pg_namespace n on n.oid=c.relnamespace
join pg_class p on p.oid=f.confrelid join pg_namespace pn on pn.oid=p.relnamespace
where f.contype='f' and n.nspname in ('public','nexa_private');
"""


def inspect_schema(sql):
    """Read catalogs, including transitive and logical (non-FK) scope links."""
    foreign_keys = json.loads(sql(CATALOG))
    descendants = {("public", "np_installations")}
    while True:
        expanded = descendants | {
            (row["schema"], row["table"]) for row in foreign_keys
            if (row["parent_schema"], row["parent"]) in descendants
        }
        if expanded == descendants:
            break
        descendants = expanded
    descendants.remove(("public", "np_installations"))
    logical = json.loads(sql("""
        select coalesce(jsonb_agg(jsonb_build_object('schema',table_schema,
          'table',table_name,'column',column_name) order by table_schema,table_name,column_name),'[]')
        from information_schema.columns where table_schema in ('public','nexa_private')
        and column_name in ('tenant_id','installation_id','installation_key');
    """))
    tables = json.loads(sql("""
        select coalesce(jsonb_agg(jsonb_build_object('table',c.relname,
          'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity) order by c.relname),'[]')
        from pg_class c join pg_namespace n on n.oid=c.relnamespace
        where n.nspname='public' and c.relkind='r' and c.relname like 'np_%';
    """))
    triggers = json.loads(sql("""
        select coalesce(jsonb_agg(jsonb_build_object('table',c.relname,'name',t.tgname,
          'definition',pg_get_triggerdef(t.oid)) order by c.relname,t.tgname),'[]')
        from pg_trigger t join pg_class c on c.oid=t.tgrelid
        join pg_namespace n on n.oid=c.relnamespace
        where n.nspname='public' and not t.tgisinternal;
    """))
    relevant = [row for row in foreign_keys if
                (row["schema"], row["table"]) in descendants and
                (row["parent_schema"], row["parent"]) in
                descendants | {("public", "np_installations")}]
    return {
        "descendants": [f"{schema}.{table}" for schema, table in sorted(descendants)],
        "foreign_keys": relevant, "logical_scope_columns": logical,
        "table_security": tables, "triggers": triggers,
    }


def main():
    checks = []
    report = {"passed": False, "transfer_ready": False, "checks": checks}
    started = False
    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        if not condition:
            raise AssertionError(name)
    try:
        qa.docker("run", "-d", "--name", qa.NAME, "--network", "none", "--label",
                  "nexpoint.audit=handoff-schema-qa", "-e",
                  "POSTGRES_HOST_AUTH_METHOD=trust", "postgres:17-bookworm")
        started = True
        for _ in range(40):
            try:
                qa.sql("select 1;")
                break
            except RuntimeError:
                time.sleep(.25)
        qa.sql("create schema extensions; create role anon nologin; "
               "create role authenticated nologin; create role service_role nologin bypassrls; "
               "create schema auth; create function auth.jwt() returns jsonb language sql "
               "stable as $$ select '{}'::jsonb $$; create schema storage; "
               "create table storage.buckets(id text primary key,name text,public boolean,"
               "file_size_limit bigint,allowed_mime_types text[]);")
        for path in sorted((qa.ROOT / "supabase/migrations").glob("*.sql")):
            qa.sql(path.read_text(encoding="utf-8"))
        check("all_migrations_apply", True)
        inventory = inspect_schema(qa.sql)
        report["inventory"] = inventory
        check("transitive_dependencies_reviewed", set(inventory["descendants"]) ==
              {"public." + table for table in SCOPED | ENROLLMENT | HANDOFF_META})
        logical_tables = {(r["schema"], r["table"]) for r in inventory["logical_scope_columns"]}
        check("logical_scope_dependencies_reviewed", logical_tables == {
            ("public", table) for table in SCOPED | ENROLLMENT | HANDOFF_META |
            {"np_installations", "np_platform_user_tenants"}
        })
        composite = [r for r in inventory["foreign_keys"] if "tenant_id" in r["columns"]]
        check("all_composite_scope_fks_reviewed", len(composite) == 21)
        check("reviewed_composite_constraints_are_deferred_capable",
              all(r["deferrable"] and not r["initially_deferred"] for r in composite))
        security = {r["table"]: r for r in inventory["table_security"]}
        check("all_affected_tables_keep_rls", all(security[t]["rls"] and
              security[t]["force_rls"] for t in SCOPED | ENROLLMENT | HANDOFF_META | {"np_installations"}))
        check("profile_authority_has_insert_trigger", any(r["name"] ==
              "np_assign_company_profile_source" and r["table"] == "np_installations"
              for r in inventory["triggers"]))
        check("legacy_grant_version_trigger_reviewed", any(r["table"] ==
              "np_installation_activation_grants" for r in inventory["triggers"]))
        # A synthetic unreviewed child must be discovered, rather than silently omitted.
        qa.sql("create table public.handoff_qa_unreviewed(id uuid primary key,"
               "installation_id uuid references public.np_installations(id));")
        changed = inspect_schema(qa.sql)
        check("new_dependency_is_detected", "public.handoff_qa_unreviewed" in changed["descendants"])
        report["passed"] = True
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)[:1500]}
    finally:
        if started:
            # Name plus ownership label; never remove a user-created database/container.
            label = qa.docker("inspect", "--format", '{{index .Config.Labels "nexpoint.audit"}}', qa.NAME)
            if label == "handoff-schema-qa":
                qa.docker("rm", "-f", qa.NAME)
                report["container_removed"] = True
        qa.OUT.mkdir(parents=True, exist_ok=True)
        path = qa.OUT / "handoff-schema.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "checks": checks, "report": str(path),
                          "error": report.get("error")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
