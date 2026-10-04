-- Device enrollment v2: random single-use code, never an operational account.
begin;
create table public.np_installation_activation_codes (
 id uuid primary key default extensions.gen_random_uuid(),
 code_digest text not null unique check (code_digest ~ '^[0-9a-f]{64}$'),
 tenant_id uuid not null references public.np_tenants(id) on delete restrict,
 installation_key text not null check (public.np_identifier_is_valid(installation_key,120)),
 installation_label text not null check (length(installation_label) between 1 and 160),
 company_name text not null check (length(company_name) between 1 and 180),
 created_by uuid not null references public.np_platform_users(id) on delete restrict,
 created_at timestamptz not null default clock_timestamp(),
 expires_at timestamptz not null,
 revoked_at timestamptz,
 consumed_at timestamptz,
 consumed_request_id uuid unique,
 credential_id uuid unique references public.np_installation_credentials(id) on delete restrict,
 retry_until timestamptz,
 check (expires_at > created_at and expires_at <= created_at + interval '7 days'),
 check ((consumed_at is null and consumed_request_id is null and credential_id is null and retry_until is null)
     or (consumed_at is not null and consumed_request_id is not null and credential_id is not null
         and retry_until > consumed_at and retry_until <= consumed_at + interval '1 day'))
);
alter table public.np_installation_activation_codes enable row level security;
alter table public.np_installation_activation_codes force row level security;
revoke all on public.np_installation_activation_codes from public,anon,authenticated,service_role;
grant select on public.np_installation_activation_codes to service_role;

create function public.np_admin_issue_installation_code(p_actor_id uuid, p_tenant_key text,
 p_installation_key text, p_installation_label text, p_code_digest text, p_hours integer)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare t public.np_tenants; r public.np_installation_activation_codes;
begin
 if not exists(select 1 from public.np_platform_users where id=p_actor_id and active and role='platform_admin') then
  return jsonb_build_object('ok',false,'code','unauthorized'); end if;
 if p_code_digest is null or p_code_digest !~ '^[0-9a-f]{64}$' or p_hours is null or p_hours not between 1 and 168
  or not public.np_identifier_is_valid(p_installation_key,120) or p_installation_label is null
  or length(btrim(p_installation_label)) not between 1 and 160 then
  return jsonb_build_object('ok',false,'code','invalid_payload'); end if;
 select * into t from public.np_tenants where tenant_key=p_tenant_key for share;
 if not found or t.status<>'active' or t.kind not in ('internal','customer') then
  return jsonb_build_object('ok',false,'code','unauthorized'); end if;
 perform pg_advisory_xact_lock(hashtextextended('np-provision:'||p_installation_key,0));
 if exists(select 1 from public.np_installations where installation_key=p_installation_key) then
  return jsonb_build_object('ok',false,'code','activation_conflict'); end if;
 -- Reissuing revokes outstanding codes for this machine; no parallel grants.
 update public.np_installation_activation_codes set revoked_at=clock_timestamp()
 where installation_key=p_installation_key and revoked_at is null and consumed_at is null;
 insert into public.np_installation_activation_codes(code_digest,tenant_id,installation_key,installation_label,
 company_name,created_by,expires_at) values(p_code_digest,t.id,p_installation_key,btrim(p_installation_label),
 t.display_name,p_actor_id,clock_timestamp()+make_interval(hours=>p_hours)) returning * into r;
 insert into public.np_platform_audit_events(actor_user_id,action,target_type,target_id,correlation_id,metadata)
 values(p_actor_id,'activation.code_issued','installation',p_installation_key,r.id::text,
 jsonb_build_object('tenant_key',p_tenant_key,'expires_at',r.expires_at));
 return jsonb_build_object('ok',true,'id',r.id,'expires_at',r.expires_at,
 'tenant_key',p_tenant_key,'installation_key',p_installation_key,'company_name',r.company_name);
end; $$;

create function public.np_admin_revoke_installation_code(p_actor_id uuid,p_code_id uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare r public.np_installation_activation_codes;
begin
 if not exists(select 1 from public.np_platform_users where id=p_actor_id and active and role='platform_admin') then
 return jsonb_build_object('ok',false,'code','unauthorized'); end if;
 update public.np_installation_activation_codes set revoked_at=coalesce(revoked_at,clock_timestamp())
 where id=p_code_id returning * into r;
 if not found then return jsonb_build_object('ok',false,'code','not_found'); end if;
 insert into public.np_platform_audit_events(actor_user_id,action,target_type,target_id,correlation_id)
 values(p_actor_id,'activation.code_revoked','installation',r.installation_key,r.id::text);
 return jsonb_build_object('ok',true,'id',r.id);
end; $$;

create function public.np_consume_installation_code(p_code_digest text,p_tenant_key text,p_installation_key text,
 p_request_id uuid,p_key_id text,p_secret_digest text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare r public.np_installation_activation_codes; t public.np_tenants;
 i public.np_installations; c public.np_installation_credentials; n integer; ts timestamptz:=clock_timestamp();
begin
 insert into nexa_private.erp_activation_rate_windows(bucket,window_start,attempts) values('v2:global',ts,1)
 on conflict(bucket) do update set
 attempts=case when erp_activation_rate_windows.window_start<=ts-interval '1 minute' then 1 else least(erp_activation_rate_windows.attempts+1,121) end,
 window_start=case when erp_activation_rate_windows.window_start<=ts-interval '1 minute' then ts else erp_activation_rate_windows.window_start end returning attempts into n;
 if n>120 then return jsonb_build_object('ok',false,'code','rate_limited'); end if;
 if p_code_digest is null or p_code_digest !~ '^[0-9a-f]{64}$' or p_request_id is null
 or p_request_id::text !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
 or p_key_id is null or p_key_id !~ '^key_[0-9a-f]{64}$'
 or p_secret_digest is null or p_secret_digest !~ '^[0-9a-f]{64}$' then
 return jsonb_build_object('ok',false,'code','invalid_payload'); end if;
 select * into r from public.np_installation_activation_codes where code_digest=p_code_digest for update;
 if not found then return jsonb_build_object('ok',false,'code','unauthorized'); end if;
 insert into nexa_private.erp_activation_rate_windows(bucket,window_start,attempts) values('v2:code:'||r.id,ts,1)
 on conflict(bucket) do update set
 attempts=case when erp_activation_rate_windows.window_start<=ts-interval '15 minutes' then 1 else least(erp_activation_rate_windows.attempts+1,11) end,
 window_start=case when erp_activation_rate_windows.window_start<=ts-interval '15 minutes' then ts else erp_activation_rate_windows.window_start end returning attempts into n;
 if n>10 then return jsonb_build_object('ok',false,'code','rate_limited'); end if;
 select * into t from public.np_tenants where id=r.tenant_id for share;
 if r.revoked_at is not null or r.installation_key is distinct from p_installation_key
 or t.tenant_key is distinct from p_tenant_key or t.status<>'active' or t.kind not in ('internal','customer') then
 return jsonb_build_object('ok',false,'code','unauthorized'); end if;
 perform pg_advisory_xact_lock(hashtextextended('np-provision:'||p_installation_key,0));
 ts:=clock_timestamp();
 if r.consumed_at is not null then
  if r.retry_until<=ts then return jsonb_build_object('ok',false,'code','unauthorized'); end if;
  select * into c from public.np_installation_credentials where id=r.credential_id for share;
  select * into i from public.np_installations where id=c.installation_id for share;
  if r.consumed_request_id is distinct from p_request_id or c.key_id is distinct from p_key_id
   or c.secret_digest is distinct from decode(p_secret_digest,'hex') or c.tenant_id is distinct from r.tenant_id
   or c.revoked_at is not null or c.valid_from>ts or (c.expires_at is not null and c.expires_at<=ts)
   or c.kind<>'hmac_sha256' or i.installation_key is distinct from p_installation_key
   or i.tenant_id is distinct from r.tenant_id or i.status<>'active' or i.environment<>'prod' or i.channel<>'prod' then
   return jsonb_build_object('ok',false,'code','activation_conflict'); end if;
 else
  if r.expires_at<=ts then return jsonb_build_object('ok',false,'code','unauthorized'); end if;
  if exists(select 1 from public.np_installations where installation_key=p_installation_key)
   or exists(select 1 from public.np_installation_activation_codes where consumed_request_id=p_request_id) then
   return jsonb_build_object('ok',false,'code','activation_conflict'); end if;
  insert into public.np_installations(tenant_id,installation_key,label,environment,channel,status,update_channel)
  values(t.id,p_installation_key,r.installation_label,'prod','prod','active','stable') returning * into i;
  insert into public.np_installation_credentials(tenant_id,installation_id,key_id,kind,secret_digest)
  values(t.id,i.id,p_key_id,'hmac_sha256',decode(p_secret_digest,'hex')) returning * into c;
  update public.np_installation_activation_codes set consumed_at=ts,consumed_request_id=p_request_id,
   credential_id=c.id,retry_until=ts+interval '1 day' where id=r.id;
  insert into public.np_platform_audit_events(action,target_type,target_id,correlation_id,metadata)
  values('installation.activated','installation',p_installation_key,p_request_id::text,jsonb_build_object('tenant_key',p_tenant_key,'code_id',r.id));
 end if;
 return jsonb_build_object('ok',true,'schema_version',2,'request_id',p_request_id,'tenant_key',t.tenant_key,
 'tenant_kind',t.kind,'installation_key',i.installation_key,'environment','prod','company_name',r.company_name,
 'installation_label',i.label,'key_id',c.key_id);
end; $$;
revoke all on function public.np_admin_issue_installation_code(uuid,text,text,text,text,integer) from public,anon,authenticated;
revoke all on function public.np_admin_revoke_installation_code(uuid,uuid) from public,anon,authenticated;
revoke all on function public.np_consume_installation_code(text,text,text,uuid,text,text) from public,anon,authenticated;
grant execute on function public.np_admin_issue_installation_code(uuid,text,text,text,text,integer) to service_role;
grant execute on function public.np_admin_revoke_installation_code(uuid,uuid) to service_role;
grant execute on function public.np_consume_installation_code(text,text,text,uuid,text,text) to service_role;
-- Keep historical rows; new deployments cannot enroll via operational passwords.
revoke execute on function public.np_prepare_installation_activation(text,text,text,text) from service_role;
revoke execute on function public.np_complete_installation_activation(uuid,uuid,text,text,text,text,uuid,text,text) from service_role;
commit;
