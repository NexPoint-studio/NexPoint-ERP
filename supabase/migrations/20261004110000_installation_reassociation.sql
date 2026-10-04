-- Cloud half of a controlled handoff. No operation is executed by this migration.
-- The operator must hold the desktop, verify backups and queues before invoking.
-- Only a platform_admin through service_role may call the atomic transfer RPC.
begin;

do $$
declare actual text[]; expected text[] := array[
 'np_admin_recovery_requests_installation_fk','np_admin_recovery_requests_ticket_fk',
 'np_admin_reset_authorizations_request_fk','np_admin_reset_authorizations_ticket_fk',
 'np_company_profile_receipts_tenant_id_installation_id_fkey',
 'np_company_profile_sources_tenant_id_installation_id_fkey',
 'np_company_profiles_tenant_id_installation_id_fkey',
 'np_diagnostic_events_installation_fk','np_fingerprints_installation_fk',
 'np_health_snapshots_installation_fk','np_heartbeats_installation_fk',
 'np_incidents_installation_fk','np_installation_credentials_installation_fk',
 'np_installation_versions_installation_fk','np_observability_events_installation_fk',
 'np_request_nonces_installation_fk','np_risks_installation_fk',
 'np_support_ticket_events_ticket_fk','np_support_tickets_installation_fk',
 'np_sync_acks_envelope_fk','np_sync_envelopes_installation_fk']; r record;
begin
 select array_agg(conname order by conname) into actual from pg_constraint c
 join pg_class child on child.oid=c.conrelid
 join pg_namespace ns on ns.oid=child.relnamespace
 where c.contype='f' and ns.nspname='public' and child.relname=any(array[
  'np_admin_recovery_requests','np_admin_reset_authorizations',
  'np_company_profile_receipts','np_company_profile_sources','np_company_profiles',
  'np_diagnostic_events','np_fingerprints','np_health_snapshots','np_heartbeats',
  'np_incidents','np_installation_credentials','np_installation_versions',
  'np_observability_events','np_request_nonces','np_risks',
  'np_support_ticket_events','np_support_tickets','np_sync_acks','np_sync_envelopes'])
 and array_length(c.conkey,1)>1
 and exists(select 1 from unnest(c.conkey) k join pg_attribute a
   on a.attrelid=child.oid and a.attnum=k where a.attname='tenant_id');
 if actual is distinct from (select array_agg(x order by x) from unnest(expected) x)
 then raise exception 'installation handoff FK inventory changed'; end if;
 for r in select child.relname, c.conname from pg_constraint c
 join pg_class child on child.oid=c.conrelid
 join pg_namespace ns on ns.oid=child.relnamespace
 where ns.nspname='public' and c.contype='f' and c.conname=any(expected)
 loop
  execute format('alter table public.%I alter constraint %I deferrable initially immediate',
    r.relname,r.conname);
 end loop;
end $$;

create table public.np_installation_handoffs (
 operation_id uuid primary key,
 installation_id uuid not null references public.np_installations(id) on delete restrict,
 installation_key text not null,
 source_tenant_id uuid not null references public.np_tenants(id) on delete restrict,
 target_tenant_id uuid not null references public.np_tenants(id) on delete restrict,
 requested_by uuid not null references public.np_platform_users(id) on delete restrict,
 backup_sha256 text not null check(backup_sha256 ~ '^[0-9a-f]{64}$'),
 local_preflight_sha256 text not null check(local_preflight_sha256 ~ '^[0-9a-f]{64}$'),
 status text not null default 'cloud_committed' check(status in ('cloud_committed','confirmed')),
 created_at timestamptz not null default clock_timestamp(),
 confirmed_at timestamptz,
 check((status='confirmed')=(confirmed_at is not null)),
 check(source_tenant_id<>target_tenant_id)
);
create index np_installation_handoffs_installation_idx
 on public.np_installation_handoffs(installation_id,created_at desc);
alter table public.np_installation_handoffs enable row level security;
alter table public.np_installation_handoffs force row level security;
revoke all on public.np_installation_handoffs from public,anon,authenticated,service_role;
grant select on public.np_installation_handoffs to service_role;

create function public.np_admin_reassociate_installation(
 p_actor_id uuid, p_operation_id uuid, p_source_tenant_key text,
 p_target_tenant_key text, p_target_display_name text,
 p_installation_key text, p_installation_label text,
 p_backup_sha256 text, p_local_preflight_sha256 text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare i public.np_installations; source public.np_tenants; target public.np_tenants;
 receipt public.np_installation_handoffs; replacement uuid; t text;
 scoped constant text[] := array[
  'np_admin_recovery_requests','np_admin_reset_authorizations',
  'np_company_profile_receipts','np_company_profiles','np_diagnostic_events',
  'np_fingerprints','np_health_snapshots','np_heartbeats','np_incidents',
  'np_installation_credentials','np_installation_versions',
  'np_observability_events','np_request_nonces','np_risks',
  'np_support_ticket_events','np_support_tickets','np_sync_acks','np_sync_envelopes'];
 deferred_fks constant text[] := array[
  'np_admin_recovery_requests_installation_fk','np_admin_recovery_requests_ticket_fk',
  'np_admin_reset_authorizations_request_fk','np_admin_reset_authorizations_ticket_fk',
  'np_company_profile_receipts_tenant_id_installation_id_fkey',
  'np_company_profile_sources_tenant_id_installation_id_fkey',
  'np_company_profiles_tenant_id_installation_id_fkey',
  'np_diagnostic_events_installation_fk','np_fingerprints_installation_fk',
  'np_health_snapshots_installation_fk','np_heartbeats_installation_fk',
  'np_incidents_installation_fk','np_installation_credentials_installation_fk',
  'np_installation_versions_installation_fk','np_observability_events_installation_fk',
  'np_request_nonces_installation_fk','np_risks_installation_fk',
  'np_support_ticket_events_ticket_fk','np_support_tickets_installation_fk',
  'np_sync_acks_envelope_fk','np_sync_envelopes_installation_fk'];
begin
 if not exists(select 1 from public.np_platform_users
    where id=p_actor_id and active and role='platform_admin') then
  return jsonb_build_object('ok',false,'code','unauthorized'); end if;
 if p_operation_id is null or not public.np_identifier_is_valid(p_source_tenant_key,80)
  or not public.np_identifier_is_valid(p_target_tenant_key,80)
  or p_source_tenant_key=p_target_tenant_key
  or not public.np_identifier_is_valid(p_installation_key,120)
  or p_target_display_name is null or length(btrim(p_target_display_name)) not between 1 and 180
  or p_target_display_name<>btrim(p_target_display_name)
  or p_target_display_name ~ '[[:cntrl:]]'
  or p_installation_label is null or length(btrim(p_installation_label)) not between 1 and 160
  or p_installation_label<>btrim(p_installation_label) or p_installation_label ~ '[[:cntrl:]]'
  or p_backup_sha256 is null or p_backup_sha256 !~ '^[0-9a-f]{64}$'
  or p_local_preflight_sha256 is null or p_local_preflight_sha256 !~ '^[0-9a-f]{64}$'
 then return jsonb_build_object('ok',false,'code','invalid_payload'); end if;

 -- Serialize with provision/activation and lock every affected writer. This call
 -- must be short and is never used while the desktop is running sync.
 perform pg_advisory_xact_lock(hashtextextended('np-provision:'||p_installation_key,0));
 lock table public.np_installations, public.np_installation_credentials,
  public.np_sync_envelopes, public.np_sync_acks, public.np_heartbeats,
  public.np_health_snapshots, public.np_installation_versions,
  public.np_observability_events, public.np_diagnostic_events,
  public.np_fingerprints, public.np_risks, public.np_incidents,
  public.np_support_tickets, public.np_support_ticket_events,
  public.np_admin_recovery_requests, public.np_admin_reset_authorizations,
  public.np_request_nonces, public.np_company_profile_sources,
  public.np_company_profiles, public.np_company_profile_receipts,
  public.np_installation_activation_grants, public.np_installation_activation_codes
  in share row exclusive mode;
 select * into receipt from public.np_installation_handoffs
  where operation_id=p_operation_id for update;
 if found then
  if receipt.installation_key=p_installation_key
   and receipt.backup_sha256=p_backup_sha256
   and receipt.local_preflight_sha256=p_local_preflight_sha256
   and receipt.requested_by=p_actor_id
   and exists(select 1 from public.np_tenants where id=receipt.source_tenant_id
     and tenant_key=p_source_tenant_key)
   and exists(select 1 from public.np_tenants where id=receipt.target_tenant_id
     and tenant_key=p_target_tenant_key)
   and exists(select 1 from public.np_installations where id=receipt.installation_id
     and tenant_id=receipt.target_tenant_id and installation_key=p_installation_key)
  then return jsonb_build_object('ok',true,'state',receipt.status,
      'operation_id',receipt.operation_id,'tenant_id',p_target_tenant_key,
      'installation_id',p_installation_key); end if;
  return jsonb_build_object('ok',false,'code','idempotency_conflict');
 end if;
 select * into i from public.np_installations where installation_key=p_installation_key for update;
 if not found then return jsonb_build_object('ok',false,'code','installation_missing'); end if;
 select * into source from public.np_tenants where id=i.tenant_id for update;
 if source.tenant_key<>p_source_tenant_key or source.status<>'active'
    or i.status<>'active' or i.environment<>'prod' then
  return jsonb_build_object('ok',false,'code','source_scope_mismatch'); end if;
 if not exists(select 1 from public.np_installations
     where tenant_id=source.id and id<>i.id) then
  return jsonb_build_object('ok',false,'code','source_would_be_empty'); end if;
 if exists(select 1 from public.np_admin_recovery_requests
   where installation_id=i.id and status in ('pending','authorized'))
   or exists(select 1 from public.np_admin_reset_authorizations
   where installation_id=i.id and consumed_at is null and revoked_at is null
     and expires_at>clock_timestamp())
 then return jsonb_build_object('ok',false,'code','recovery_pending'); end if;
 if exists(select 1 from public.np_installation_activation_codes
   where installation_key=i.installation_key and consumed_at is null
     and revoked_at is null and expires_at>clock_timestamp())
   or exists(select 1 from public.np_installation_activation_grants
   where installation_key=i.installation_key and consumed_at is null
     and revoked_at is null and expires_at>clock_timestamp())
 then return jsonb_build_object('ok',false,'code','activation_pending'); end if;
 if not exists(select 1 from public.np_installation_credentials
   where installation_id=i.id and tenant_id=source.id and revoked_at is null
     and valid_from<=clock_timestamp() and (expires_at is null or expires_at>clock_timestamp()))
 then return jsonb_build_object('ok',false,'code','credential_inactive'); end if;

 select * into target from public.np_tenants where tenant_key=p_target_tenant_key for update;
 if found then
  if target.kind<>'customer' or target.status<>'active' or target.id=source.id
     or target.display_name<>p_target_display_name then
   return jsonb_build_object('ok',false,'code','target_invalid'); end if;
 else
  insert into public.np_tenants(tenant_key,display_name,kind,status)
   values(p_target_tenant_key,p_target_display_name,'customer','active') returning * into target;
 end if;
 -- Defer only reviewed composite FKs. Other FKs/RLS/triggers remain enabled.
 execute (select 'set constraints ' || string_agg(format('public.%I', name), ',') ||
                 ' deferred' from unnest(deferred_fks) name);
 -- The canonical source row has tenant PK and must be reconciled separately.
 if exists(select 1 from public.np_company_profile_sources
    where tenant_id=source.id and installation_id=i.id) then
  delete from public.np_company_profile_sources where tenant_id=source.id;
  select id into replacement from public.np_installations
   where tenant_id=source.id and id<>i.id order by created_at,installation_key limit 1;
  insert into public.np_company_profile_sources(tenant_id,installation_id)
   values(source.id,replacement);
 end if;
 foreach t in array scoped loop
  execute format('update public.%I set tenant_id=$1 where tenant_id=$2 and installation_id=$3',t)
   using target.id,source.id,i.id;
 end loop;
 update public.np_installation_activation_grants set tenant_id=target.id,
   revoked_at=coalesce(revoked_at,clock_timestamp())
   where tenant_id=source.id and installation_key=i.installation_key;
 update public.np_installation_activation_codes set tenant_id=target.id,
   revoked_at=coalesce(revoked_at,clock_timestamp())
   where tenant_id=source.id and installation_key=i.installation_key;
 update public.np_installations set tenant_id=target.id,label=p_installation_label,
  update_channel='stable' where id=i.id;
 insert into public.np_company_profile_sources(tenant_id,installation_id)
  values(target.id,i.id) on conflict(tenant_id) do nothing;
 execute (select 'set constraints ' || string_agg(format('public.%I', name), ',') ||
                 ' immediate' from unnest(deferred_fks) name);
 insert into public.np_installation_handoffs(operation_id,installation_id,installation_key,
  source_tenant_id,target_tenant_id,requested_by,backup_sha256,local_preflight_sha256)
  values(p_operation_id,i.id,i.installation_key,source.id,target.id,p_actor_id,
   p_backup_sha256,p_local_preflight_sha256);
 insert into public.np_platform_audit_events(actor_user_id,action,target_type,target_id,
  correlation_id,metadata) values(p_actor_id,'installation.reassociated','installation',
  i.installation_key,p_operation_id::text,jsonb_build_object('source_tenant',
  p_source_tenant_key,'target_tenant',p_target_tenant_key));
 return jsonb_build_object('ok',true,'state','cloud_committed','operation_id',p_operation_id,
  'tenant_id',p_target_tenant_key,'installation_id',p_installation_key);
end $$;
revoke all on function public.np_admin_reassociate_installation(uuid,uuid,text,text,text,text,text,text,text)
 from public,anon,authenticated,service_role;
grant execute on function public.np_admin_reassociate_installation(uuid,uuid,text,text,text,text,text,text,text)
 to service_role;

-- The desktop may fetch/confirm only a committed receipt with its unchanged
-- installation secret. The old tenant header is accepted solely for resuming
-- this exact operation after the cloud move; ordinary APIs remain new-scope only.
create function public.np_erp_handoff_receipt(
 p_presented_tenant_key text, p_installation_key text, p_operation_id uuid,
 p_secret_hash text, p_nonce_hash text, p_sent_at timestamptz, p_confirm boolean)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare i public.np_installations; r public.np_installation_handoffs;
 old_tenant public.np_tenants; new_tenant public.np_tenants;
 nonce_id uuid; ts timestamptz := clock_timestamp();
begin
 if p_operation_id is null or not public.np_identifier_is_valid(p_presented_tenant_key,80)
  or not public.np_identifier_is_valid(p_installation_key,120)
  or p_secret_hash is null or p_secret_hash !~ '^[0-9a-f]{64}$'
  or p_nonce_hash is null or p_nonce_hash !~ '^[0-9a-f]{64}$'
  or p_sent_at is null or abs(extract(epoch from (ts-p_sent_at)))>60
  or p_confirm is null
 then return jsonb_build_object('ok',false,'code','unauthorized'); end if;
 select * into i from public.np_installations where installation_key=p_installation_key for share;
 if not found or i.status<>'active' or i.environment<>'prod' then
  return jsonb_build_object('ok',false,'code','unauthorized'); end if;
 select * into r from public.np_installation_handoffs
  where operation_id=p_operation_id and installation_id=i.id for update;
 if not found or r.target_tenant_id<>i.tenant_id then
  return jsonb_build_object('ok',false,'code','unauthorized'); end if;
 select * into old_tenant from public.np_tenants where id=r.source_tenant_id;
 select * into new_tenant from public.np_tenants where id=r.target_tenant_id;
 if p_presented_tenant_key not in(old_tenant.tenant_key,new_tenant.tenant_key)
  or not exists(select 1 from public.np_installation_credentials
     where installation_id=i.id and tenant_id=i.tenant_id and kind='hmac_sha256'
       and secret_digest=decode(p_secret_hash,'hex') and revoked_at is null
       and valid_from<=ts and (expires_at is null or expires_at>ts))
 then return jsonb_build_object('ok',false,'code','unauthorized'); end if;
 if p_confirm and p_presented_tenant_key<>new_tenant.tenant_key then
  return jsonb_build_object('ok',false,'code','old_scope_cannot_confirm'); end if;
 insert into public.np_request_nonces(tenant_id,installation_id,nonce,
  request_timestamp,accepted_at,expires_at)
  values(i.tenant_id,i.id,'handoff:'||p_nonce_hash,p_sent_at,ts,ts+interval '10 minutes')
  on conflict(tenant_id,installation_id,nonce) do nothing returning id into nonce_id;
 if nonce_id is null then return jsonb_build_object('ok',false,'code','replay'); end if;
 if p_confirm and r.status='cloud_committed' then
  update public.np_installation_handoffs set status='confirmed',confirmed_at=ts
   where operation_id=p_operation_id;
  insert into public.np_platform_audit_events(action,target_type,target_id,
   correlation_id,metadata) values('installation.handoff_confirmed','installation',
   i.installation_key,p_operation_id::text,
   jsonb_build_object('tenant_key',new_tenant.tenant_key));
 end if;
 return jsonb_build_object('ok',true,'schema_version',1,'operation_id',p_operation_id,
  'installation_id',i.installation_key,'source_tenant_id',old_tenant.tenant_key,
  'target_tenant_id',new_tenant.tenant_key,'target_tenant_kind',new_tenant.kind,
  'company_name',new_tenant.display_name,'installation_label',i.label,
  'backup_sha256',r.backup_sha256,'local_preflight_sha256',r.local_preflight_sha256,
  'state',case when p_confirm then 'confirmed' else r.status end);
end $$;
revoke all on function public.np_erp_handoff_receipt(text,text,uuid,text,text,timestamptz,boolean)
 from public,anon,authenticated,service_role;
grant execute on function public.np_erp_handoff_receipt(text,text,uuid,text,text,timestamptz,boolean)
 to service_role;
commit;
