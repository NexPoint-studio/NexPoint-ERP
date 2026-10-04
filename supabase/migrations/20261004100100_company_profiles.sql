-- Company names are local-owned metadata. Scope and commercial status stay server-owned.
alter table public.np_tenants drop constraint np_tenants_name_length;
alter table public.np_tenants add constraint np_tenants_name_length check(length(display_name) between 1 and 180);
create table public.np_company_profile_sources (
 tenant_id uuid primary key references public.np_tenants(id),
 installation_id uuid not null,
 foreign key(tenant_id,installation_id) references public.np_installations(tenant_id,id)
);
create table public.np_company_profiles (
 tenant_id uuid not null,
 installation_id uuid primary key,
 revision bigint not null check(revision between 1 and 9007199254740991),
 name text not null check(length(name) between 1 and 180),
 trade_name text not null check(length(trade_name)<=180),
 payload_hash bytea not null,
 foreign key(tenant_id,installation_id) references public.np_installations(tenant_id,id)
);
create table public.np_company_profile_receipts (
 tenant_id uuid not null, installation_id uuid not null,
 revision bigint not null, payload_hash bytea not null,
 primary key(installation_id,revision),
 foreign key(tenant_id,installation_id) references public.np_installations(tenant_id,id)
);
-- Existing authority is determined before any profile arrives and is never re-elected.
insert into public.np_company_profile_sources(tenant_id,installation_id)
 select distinct on(tenant_id) tenant_id,id from public.np_installations
 order by tenant_id,created_at,installation_key;
create function public.np_assign_company_profile_source() returns trigger
language plpgsql security definer set search_path=pg_catalog as $$
begin
 insert into public.np_company_profile_sources values(new.tenant_id,new.id) on conflict do nothing;
 return new;
end $$;
revoke all on function public.np_assign_company_profile_source() from public,anon,authenticated,service_role;
create trigger np_assign_company_profile_source after insert on public.np_installations
 for each row execute function public.np_assign_company_profile_source();

create function public.np_capture_company_profile() returns trigger
language plpgsql security definer set search_path=pg_catalog as $$
declare
 p jsonb; r bigint; digest bytea; prior bytea; latest bigint; source uuid;
begin
 if new.kind::text <> 'company_profile' then return new; end if;
 p := new.payload;
 if exists(select 1 from jsonb_object_keys(p) k where k not in('tenant_id','installation_id','revision','name','trade_name','correlation_id'))
  or not(p ?& array['tenant_id','installation_id','revision','name','trade_name'])
  or jsonb_typeof(p->'revision') <> 'number' or (p->>'revision') !~ '^[1-9][0-9]{0,15}$'
  or jsonb_typeof(p->'name') <> 'string' or jsonb_typeof(p->'trade_name') <> 'string'
  or length(p->>'name') not between 1 and 180 or length(p->>'trade_name') > 180
  or btrim(p->>'name') <> p->>'name' or btrim(p->>'trade_name') <> p->>'trade_name'
  or (p->>'name') ~ '[[:cntrl:]]' or (p->>'trade_name') ~ '[[:cntrl:]]'
 then raise exception 'invalid company profile' using errcode='22023'; end if;
 r := (p->>'revision')::bigint;
 if r > 9007199254740991 then raise exception 'invalid profile revision' using errcode='22023'; end if;
 -- Serialize snapshots within one tenant; payload scope was authenticated by ingestion.
 select installation_id into source from public.np_company_profile_sources where tenant_id=new.tenant_id for update;
 if source is null then raise exception 'missing profile authority'; end if;
 digest := extensions.digest((p - 'correlation_id')::text,'sha256');
 select payload_hash into prior from public.np_company_profile_receipts where installation_id=new.installation_id and revision=r;
 if found and prior <> digest then raise exception 'profile revision conflict' using errcode='22023'; end if;
 insert into public.np_company_profile_receipts values(new.tenant_id,new.installation_id,r,digest) on conflict do nothing;
 select revision into latest from public.np_company_profiles where installation_id=new.installation_id;
 if latest is null or r > latest then
  insert into public.np_company_profiles values(new.tenant_id,new.installation_id,r,p->>'name',p->>'trade_name',digest)
  on conflict(installation_id) do update set revision=excluded.revision,name=excluded.name,trade_name=excluded.trade_name,payload_hash=excluded.payload_hash;
  if source=new.installation_id then
   update public.np_tenants set display_name=coalesce(nullif(p->>'trade_name',''),p->>'name') where id=new.tenant_id;
  end if;
 end if;
 return new;
end $$;
revoke all on function public.np_capture_company_profile() from public,anon,authenticated,service_role;
create trigger np_capture_company_profile after insert on public.np_sync_envelopes
 for each row execute function public.np_capture_company_profile();

-- Extend the existing authenticated ingestion without replacing its nonce/RLS/ACK rules.
-- Fail closed if the expected contract has changed upstream.
-- Match only the stable event-list token: pg_get_functiondef uses LF even when
-- the migration file is checked out with CRLF on Windows.
do $migration$
declare definition text; original text := $needle$'risk', 'incident', 'diagnostic_event'$needle$;
begin
 select pg_get_functiondef('public.erp_ingest_sync_batch(text,text,text,timestamptz,jsonb)'::regprocedure) into definition;
 if position(original in definition)=0 then raise exception 'unexpected ingestion contract'; end if;
 definition := replace(definition,original,$replacement$'risk', 'incident', 'diagnostic_event', 'company_profile'$replacement$);
 execute definition;
end $migration$;

do $$ declare t text; begin
 foreach t in array array['np_company_profile_sources','np_company_profiles','np_company_profile_receipts'] loop
  execute format('alter table public.%I enable row level security',t);
  execute format('alter table public.%I force row level security',t);
  execute format('revoke all on public.%I from public,anon,authenticated,service_role',t);
  execute format('grant select on public.%I to service_role',t);
 end loop;
end $$;
