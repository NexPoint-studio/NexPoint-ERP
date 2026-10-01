-- First-device authorization. No credentials, users or installations are seeded.
-- The server administrator creates an expiring grant; the desktop never gets
-- administrative cloud credentials from this contract. Only after successful
-- authorization it receives a distinct local-owner verifier for its new SQLite.
begin;

create table public.np_installation_activation_grants (
    id uuid primary key default extensions.gen_random_uuid(),
    username text not null unique check (
        username = lower(btrim(username)) and username ~ '^[a-z0-9][a-z0-9_.@+-]{2,159}$'
    ),
    display_name text not null check (length(display_name) between 1 and 160),
    password_hash text not null check (
        password_hash ~ '^scrypt[$]16384[$]8[$]1[$][A-Za-z0-9_-]{22}==[$][A-Za-z0-9_-]{43}=$'
    ),
    owner_username text not null check (
        owner_username ~ '^[a-z0-9][a-z0-9_.@+-]{2,159}$'
        and owner_username <> username and owner_username <> 'nexpoint-admin'
    ),
    owner_display_name text not null check (length(owner_display_name) between 1 and 160),
    owner_password_hash text not null check (
        owner_password_hash ~ '^scrypt[$]16384[$]8[$]1[$][A-Za-z0-9_-]{22}==[$][A-Za-z0-9_-]{43}=$'
        and owner_password_hash <> password_hash
    ),
    auth_version uuid not null default extensions.gen_random_uuid(),
    tenant_id uuid not null references public.np_tenants(id) on delete restrict,
    installation_key text not null unique check (
        public.np_identifier_is_valid(installation_key, 120)
    ),
    installation_label text not null check (length(installation_label) between 1 and 160),
    company_name text not null check (length(company_name) between 1 and 160),
    environment public.np_environment not null default 'prod' check (environment = 'prod'),
    local_role text not null default 'user' check (local_role = 'user'),
    active boolean not null default true,
    can_activate boolean not null default true,
    created_at timestamptz not null default clock_timestamp(),
    expires_at timestamptz not null,
    revoked_at timestamptz,
    consumed_at timestamptz,
    consumed_request_id uuid unique,
    credential_id uuid unique references public.np_installation_credentials(id) on delete restrict,
    retry_until timestamptz,
    constraint np_activation_expiry check (
        expires_at > created_at and expires_at <= created_at + interval '30 days'
    ),
    constraint np_activation_consumption check (
        (consumed_at is null and consumed_request_id is null and credential_id is null
            and retry_until is null)
        or (consumed_at is not null and consumed_request_id is not null and credential_id is not null
            and retry_until > consumed_at and retry_until <= consumed_at + interval '7 days')
    )
);

alter table public.np_installation_activation_grants enable row level security;
alter table public.np_installation_activation_grants force row level security;
revoke all on public.np_installation_activation_grants from public, anon, authenticated, service_role;
grant select, insert, update on public.np_installation_activation_grants to service_role;

-- The fixed global row bounds anonymous work and storage. Account rows are only
-- allocated for approved usernames and expire naturally with their grants.
create table nexa_private.erp_activation_rate_windows (
    bucket text primary key,
    window_start timestamptz not null,
    attempts integer not null check (attempts > 0)
);
alter table nexa_private.erp_activation_rate_windows enable row level security;
alter table nexa_private.erp_activation_rate_windows force row level security;
revoke all on nexa_private.erp_activation_rate_windows
    from public, anon, authenticated, service_role;

create function public.np_activation_grant_version()
returns trigger language plpgsql set search_path = pg_catalog as $$
begin
    if row(new.username, new.password_hash, new.tenant_id, new.installation_key,
           new.environment, new.active, new.can_activate, new.expires_at, new.revoked_at,
           new.owner_username, new.owner_password_hash, new.company_name, new.display_name,
           new.owner_display_name, new.installation_label)
       is distinct from
       row(old.username, old.password_hash, old.tenant_id, old.installation_key,
           old.environment, old.active, old.can_activate, old.expires_at, old.revoked_at,
           old.owner_username, old.owner_password_hash, old.company_name, old.display_name,
           old.owner_display_name, old.installation_label) then
        new.auth_version := extensions.gen_random_uuid();
    end if;
    return new;
end;
$$;
create trigger np_activation_grant_version_before_update
before update on public.np_installation_activation_grants
for each row execute function public.np_activation_grant_version();

-- Called only by the Edge Function, before any password KDF. No public role can
-- invoke this RPC or read a grant/hash. All rejections avoid account disclosure.
create function public.np_prepare_installation_activation(
    p_username text, p_tenant_key text, p_installation_key text, p_environment text
)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare
    v_now timestamptz := clock_timestamp();
    v_count integer;
    v_grant public.np_installation_activation_grants%rowtype;
    v_tenant public.np_tenants%rowtype;
begin
    insert into nexa_private.erp_activation_rate_windows(bucket, window_start, attempts)
    values ('global', v_now, 1)
    on conflict (bucket) do update set
        attempts = case when erp_activation_rate_windows.window_start <= v_now - interval '1 minute'
            then 1 else least(erp_activation_rate_windows.attempts + 1, 121) end,
        window_start = case when erp_activation_rate_windows.window_start <= v_now - interval '1 minute'
            then v_now else erp_activation_rate_windows.window_start end
    returning attempts into v_count;
    if v_count > 120 then
        return jsonb_build_object('ok', false, 'code', 'rate_limited');
    end if;

    select * into v_grant from public.np_installation_activation_grants
     where username = p_username;
    if not found then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;
    insert into nexa_private.erp_activation_rate_windows(bucket, window_start, attempts)
    values ('account:' || v_grant.id::text, v_now, 1)
    on conflict (bucket) do update set
        attempts = case when erp_activation_rate_windows.window_start <= v_now - interval '15 minutes'
            then 1 else least(erp_activation_rate_windows.attempts + 1, 11) end,
        window_start = case when erp_activation_rate_windows.window_start <= v_now - interval '15 minutes'
            then v_now else erp_activation_rate_windows.window_start end
    returning attempts into v_count;
    if v_count > 10 then
        return jsonb_build_object('ok', false, 'code', 'rate_limited');
    end if;
    select * into v_tenant from public.np_tenants where id = v_grant.tenant_id;
    if not v_grant.active or not v_grant.can_activate or v_grant.revoked_at is not null
       or v_grant.installation_key is distinct from p_installation_key
       or p_environment is distinct from 'prod' or v_grant.environment <> 'prod'
       or v_tenant.tenant_key is distinct from p_tenant_key
       or v_tenant.status <> 'active' or v_tenant.kind not in ('internal', 'customer')
       or (v_grant.consumed_at is null and v_grant.expires_at <= v_now)
       or (v_grant.consumed_at is not null and v_grant.retry_until <= v_now) then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;
    return jsonb_build_object('ok', true, 'grant_id', v_grant.id,
        'auth_version', v_grant.auth_version, 'password_hash', v_grant.password_hash);
end;
$$;

-- The Edge has verified the password before invoking this RPC. The auth version
-- closes the revocation/password-change race between preflight and completion.
-- One transaction creates the machine + one credential + consumption receipt.
create function public.np_complete_installation_activation(
    p_grant_id uuid, p_auth_version uuid, p_username text, p_tenant_key text,
    p_installation_key text, p_environment text, p_request_id uuid,
    p_key_id text, p_secret_digest text
)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare
    v_now timestamptz := clock_timestamp();
    v_grant public.np_installation_activation_grants%rowtype;
    v_tenant public.np_tenants%rowtype;
    v_installation public.np_installations%rowtype;
    v_credential public.np_installation_credentials%rowtype;
begin
    if p_request_id is null or p_request_id::text !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       or p_key_id is null or p_key_id !~ '^key_[0-9a-f]{64}$'
       or p_secret_digest is null or p_secret_digest !~ '^[0-9a-f]{64}$' then
        return jsonb_build_object('ok', false, 'code', 'invalid_payload');
    end if;
    select * into v_grant from public.np_installation_activation_grants
     where id = p_grant_id for update;
    if not found then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;
    select * into v_tenant from public.np_tenants where id = v_grant.tenant_id for share;
    v_now := clock_timestamp();
    if v_grant.auth_version is distinct from p_auth_version
       or v_grant.username is distinct from p_username
       or not v_grant.active or not v_grant.can_activate or v_grant.revoked_at is not null
       or v_grant.installation_key is distinct from p_installation_key
       or p_environment is distinct from 'prod' or v_grant.environment <> 'prod'
       or v_tenant.tenant_key is distinct from p_tenant_key
       or v_tenant.status <> 'active' or v_tenant.kind not in ('internal', 'customer') then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;
    perform pg_advisory_xact_lock(hashtextextended('np-provision:' || p_installation_key, 0));
    if v_grant.consumed_at is not null then
        if v_grant.retry_until <= v_now then
            return jsonb_build_object('ok', false, 'code', 'unauthorized');
        end if;
        select * into v_credential from public.np_installation_credentials
         where id = v_grant.credential_id for share;
        select * into v_installation from public.np_installations
         where id = v_credential.installation_id for share;
        if v_grant.consumed_request_id is distinct from p_request_id
           or v_credential.key_id is distinct from p_key_id
           or v_credential.secret_digest is distinct from decode(p_secret_digest, 'hex')
           or v_credential.tenant_id is distinct from v_grant.tenant_id
           or v_credential.kind <> 'hmac_sha256' or v_credential.revoked_at is not null
           or v_credential.valid_from > v_now
           or (v_credential.expires_at is not null and v_credential.expires_at <= v_now)
           or v_installation.installation_key is distinct from p_installation_key
           or v_installation.status <> 'active' or v_installation.environment <> 'prod'
           or v_installation.channel <> 'prod' then
            return jsonb_build_object('ok', false, 'code', 'activation_conflict');
        end if;
    else
        if v_grant.expires_at <= v_now then
            return jsonb_build_object('ok', false, 'code', 'unauthorized');
        end if;
        if exists (select 1 from public.np_installations where installation_key = p_installation_key)
           or exists (select 1 from public.np_installation_activation_grants
                      where consumed_request_id = p_request_id) then
            return jsonb_build_object('ok', false, 'code', 'activation_conflict');
        end if;
        insert into public.np_installations (
            tenant_id, installation_key, label, environment, channel, status
        ) values (v_grant.tenant_id, p_installation_key, v_grant.installation_label,
                  'prod', 'prod', 'active') returning * into v_installation;
        insert into public.np_installation_credentials (
            tenant_id, installation_id, key_id, kind, secret_digest
        ) values (v_grant.tenant_id, v_installation.id, p_key_id, 'hmac_sha256',
                  decode(p_secret_digest, 'hex')) returning * into v_credential;
        update public.np_installation_activation_grants
           set consumed_at = v_now, consumed_request_id = p_request_id,
               credential_id = v_credential.id, retry_until = v_now + interval '7 days'
         where id = v_grant.id;
        insert into public.np_platform_audit_events (
            action, target_type, target_id, correlation_id, metadata
        ) values ('installation.activated', 'installation', p_installation_key,
            p_request_id::text, jsonb_build_object('tenant_key', p_tenant_key,
                'activation_grant_id', v_grant.id));
    end if;
    return jsonb_build_object('ok', true, 'schema_version', 1, 'request_id', p_request_id,
        'tenant_key', v_tenant.tenant_key, 'tenant_kind', v_tenant.kind,
        'installation_key', v_installation.installation_key, 'environment', 'prod',
        'company_name', v_grant.company_name, 'installation_label', v_installation.label,
        'key_id', v_credential.key_id, 'username', v_grant.username,
        'display_name', v_grant.display_name, 'role', 'user',
        'owner_username', v_grant.owner_username, 'owner_display_name', v_grant.owner_display_name,
        'owner_password_hash', v_grant.owner_password_hash);
end;
$$;

revoke all on function public.np_activation_grant_version() from public, anon, authenticated;
revoke all on function public.np_prepare_installation_activation(text, text, text, text)
    from public, anon, authenticated;
revoke all on function public.np_complete_installation_activation(uuid, uuid, text, text, text, text, uuid, text, text)
    from public, anon, authenticated;
grant execute on function public.np_prepare_installation_activation(text, text, text, text) to service_role;
grant execute on function public.np_complete_installation_activation(uuid, uuid, text, text, text, text, uuid, text, text) to service_role;

commit;
