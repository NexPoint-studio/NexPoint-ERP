-- Only the disposable nexpoint-security-db container. Transaction always rolls back.
\set ON_ERROR_STOP on
begin;
create extension if not exists pgtap with schema extensions;
-- Harness-only access to assertion functions, not product tables/RPCs.
grant usage on schema extensions to anon, authenticated;
set local search_path=public,extensions;
select no_plan();
select ok(bool_and(relrowsecurity and relforcerowsecurity), 'All np tables ENABLE and FORCE RLS')
from pg_class c join pg_namespace n on n.oid=c.relnamespace
where n.nspname='public' and c.relname like 'np_%' and c.relkind='r';
select ok(not exists(select 1 from information_schema.role_table_grants
where grantee in ('anon','authenticated') and table_schema='public' and table_name like 'np_%'),
'No direct table grants to browser roles');
select ok(not exists(select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
where n.nspname='public' and (p.proname like 'np_admin_%' or p.proname='erp_ingest_sync_batch')
and (has_function_privilege('anon',p.oid,'EXECUTE') or has_function_privilege('authenticated',p.oid,'EXECUTE'))),
'Privileged RPC unavailable to anon/authenticated');
select ok(not exists(select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
where n.nspname='public' and p.prosecdef and (p.proname like 'np_%' or p.proname like 'erp_%' or p.proname like 'nexa_%')
and not exists(select 1 from unnest(p.proconfig) cfg where cfg like 'search_path=%')),
'Security definer routines declare search_path');
select ok(not has_table_privilege('service_role','nexa_private.erp_replay_nonces','SELECT'),
'Service role has no direct replay-store read grant');
select ok(has_function_privilege('service_role','public.nexa_claim_erp_nonce(text,text)','EXECUTE'),
'Service role replay access uses RPC contract');
set local role anon;
select throws_ok('select * from public.np_platform_users','42501',null,'Anon cannot read platform users');
select throws_ok('select * from public.np_installation_credentials','42501',null,'Anon cannot read installation credentials');
reset role;
set local role authenticated;
select throws_ok('select * from public.np_platform_users','42501',null,'Authenticated cannot read platform users');
select throws_ok('update public.np_platform_users set active=false','42501',null,'Authenticated cannot mass assign platform users');
select throws_ok('select public.nexa_claim_erp_nonce(repeat(''a'',64),repeat(''b'',64))','42501',null,'Browser cannot claim nonce');
reset role;
select * from finish();
rollback;
