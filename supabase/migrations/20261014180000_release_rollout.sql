-- Distribution is server-owned metadata; do not extend the v1 client manifest.
alter table public.np_erp_releases
    add column rollout_state text not null default 'active' check (rollout_state in ('active','paused','withdrawn')),
    add column rollout_percentage integer not null default 100 check (rollout_percentage between 0 and 100);

create function public.np_admin_set_release_rollout(p_actor_id uuid, p_release_id uuid, p_state text, p_percentage integer)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare r public.np_erp_releases;
begin
    if not exists (select 1 from public.np_platform_users where id=p_actor_id and active and role='platform_admin') then
        return jsonb_build_object('ok',false,'code','unauthorized');
    end if;
    if p_state is null or p_state not in ('active','paused','withdrawn') or p_percentage is null or p_percentage not between 0 and 100 then
        return jsonb_build_object('ok',false,'code','invalid_payload');
    end if;
    perform pg_advisory_xact_lock(hashtextextended('np-release-catalog',0));
    select * into r from public.np_erp_releases where id=p_release_id for update;
    if not found or r.status not in ('verified','published','withdrawn') or (r.status='withdrawn' and p_state <> 'withdrawn') then
        return jsonb_build_object('ok',false,'code','invalid_payload');
    end if;
    update public.np_erp_releases set rollout_state=p_state, rollout_percentage=p_percentage,
        status=case when p_state='withdrawn' then 'withdrawn' else status end where id=p_release_id;
    insert into public.np_platform_audit_events(actor_user_id,action,target_type,target_id,correlation_id,metadata)
        values(p_actor_id,'release.rollout_changed','release',r.id::text,r.id::text,
               jsonb_build_object('state',p_state,'percentage',p_percentage));
    return jsonb_build_object('ok',true,'release_id',r.id);
end;
$$;
revoke all on function public.np_admin_set_release_rollout(uuid,uuid,text,integer) from public, anon, authenticated;
grant execute on function public.np_admin_set_release_rollout(uuid,uuid,text,integer) to service_role;

create or replace function public.np_erp_check_release(p_tenant_id text, p_installation_id text,
    p_secret_hash text, p_nonce_hash text, p_sent_at timestamptz, p_installed_version text)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare i public.np_installations; r public.np_erp_releases; nonce_id uuid; instant timestamptz := clock_timestamp();
begin
    if p_secret_hash is null or p_secret_hash !~ '^[0-9a-f]{64}$'
       or p_nonce_hash is null or p_nonce_hash !~ '^[0-9a-f]{64}$'
       or p_sent_at is null or abs(extract(epoch from instant-p_sent_at)) > 60
       or public.np_release_version_parts(p_installed_version) is null then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;
    select installation.* into i from public.np_installations installation
        join public.np_tenants tenant on tenant.id = installation.tenant_id
        where installation.installation_key = p_installation_id and tenant.tenant_key = p_tenant_id
        and installation.status = 'active' and tenant.status = 'active'
        and tenant.kind in ('customer', 'internal') and installation.environment = 'prod' and installation.channel = 'prod'
        for update of installation;
    if not found or not exists (select 1 from public.np_installation_credentials c
        where c.tenant_id = i.tenant_id and c.installation_id = i.id and c.kind = 'hmac_sha256'
        and c.secret_digest = decode(p_secret_hash, 'hex') and c.revoked_at is null
        and c.valid_from <= instant and (c.expires_at is null or c.expires_at > instant)) then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;
    if (select count(*) from public.np_request_nonces where tenant_id = i.tenant_id
        and installation_id = i.id and nonce like 'update:%' and accepted_at >= instant - interval '1 minute') >= 12 then
        return jsonb_build_object('ok', false, 'code', 'rate_limited');
    end if;
    insert into public.np_request_nonces(tenant_id, installation_id, nonce, request_timestamp, accepted_at, expires_at)
        values(i.tenant_id, i.id, 'update:' || p_nonce_hash, p_sent_at, instant, instant + interval '10 minutes')
        on conflict (tenant_id, installation_id, nonce) do nothing returning id into nonce_id;
    if nonce_id is null then return jsonb_build_object('ok', false, 'code', 'replay'); end if;
    select * into r from public.np_erp_releases where update_channel = i.update_channel
        and status = 'published' and published_at <= instant
        and public.np_release_version_parts(version) > public.np_release_version_parts(p_installed_version)
        order by sequence desc limit 1;
    -- Pause the newest release instead of falling back to an older build.
    -- The wire manifest stays schema 1 for already installed clients.
    if r.id is not null and (r.rollout_state <> 'active' or
        (('x' || substr(md5(i.id::text || ':' || r.id::text),1,8))::bit(32)::bigint % 100) >= r.rollout_percentage) then
        r := null;
    end if;
    return jsonb_build_object('ok', true, 'update_channel', i.update_channel,
        'release', case when r.id is null then null else public.np_release_manifest(r) end);
end;
$$;


-- Bounded update diagnostics, optional for legacy clients.
alter table public.np_installations
    add column update_error_code text check (update_error_code in ('OFFLINE','INVALID_RELEASE','DOWNLOAD_FAILED','INSTALL_FAILED','INSUFFICIENT_SPACE','APP_RUNNING','HEALTH_FAILED','SCHEMA_CHANGED','CANCELLED','RESTART_FAILED')),
    add column update_target_version text check (update_target_version ~ '^(0|[1-9][0-9]{0,5})[.](0|[1-9][0-9]{0,5})[.](0|[1-9][0-9]{0,5})$'),
    add column update_last_attempt_at timestamptz;
create or replace function public.np_capture_update_heartbeat() returns trigger
language plpgsql security definer set search_path = pg_catalog, public
as $$
declare
    v_payload jsonb;
    v_state text;
    v_channel text;
    v_observed timestamptz;
begin
    select payload into v_payload from public.np_sync_envelopes
     where id = new.id and tenant_id = new.tenant_id
       and installation_id = new.installation_id and kind = 'heartbeat';
    v_state := v_payload->>'update_state';
    v_channel := v_payload->>'update_channel';
    v_observed := public.np_parse_iso_timestamptz(v_payload->>'update_observed_at');
    if v_state is null or v_state not in (
        'IDLE', 'CHECKING', 'CURRENT', 'AVAILABLE', 'DOWNLOADING', 'READY',
        'INSTALLING', 'RESTARTING', 'SUCCEEDED', 'FAILED', 'ROLLED_BACK', 'ROLLBACK_REQUIRED'
    ) or v_channel is null or v_channel not in ('stable', 'beta', 'internal')
      or v_observed is null or v_observed > new.received_at + interval '5 minutes' then
        return new;
    end if;
    update public.np_installations
       set update_state = v_state, update_reported_at = v_observed,
           update_error_code = case when v_payload->>'update_error_code' in ('OFFLINE','INVALID_RELEASE','DOWNLOAD_FAILED','INSTALL_FAILED','INSUFFICIENT_SPACE','APP_RUNNING','HEALTH_FAILED','SCHEMA_CHANGED','CANCELLED','RESTART_FAILED') then v_payload->>'update_error_code' else null end,
           update_target_version = case when public.np_release_version_parts(v_payload->>'update_target_version') is not null then v_payload->>'update_target_version' else null end,
           update_last_attempt_at = case when public.np_parse_iso_timestamptz(v_payload->>'update_last_attempt_at') <= new.received_at + interval '5 minutes' then public.np_parse_iso_timestamptz(v_payload->>'update_last_attempt_at') else null end
     where id = new.installation_id and tenant_id = new.tenant_id
       and update_channel = v_channel
       and (update_reported_at is null or v_observed > update_reported_at);
    return new;
end;
$$;
