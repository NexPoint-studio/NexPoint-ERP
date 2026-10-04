-- Additive release catalog. Operational SQLite and installation credentials are untouched.
-- Environment/channel keep their original meaning; update_channel is independent.
alter table public.np_installations add column update_channel text not null default 'internal'
    check (update_channel in ('stable', 'beta', 'internal'));
update public.np_installations i set update_channel = 'stable'
    from public.np_tenants t where t.id = i.tenant_id and t.kind = 'customer';

create function public.np_default_update_channel() returns trigger
language plpgsql security definer set search_path = pg_catalog as $$
begin
    if exists (select 1 from public.np_tenants where id = new.tenant_id and kind = 'customer') then
        new.update_channel := 'stable';
    end if;
    return new;
end;
$$;
create trigger np_installation_update_channel_default before insert on public.np_installations
for each row execute function public.np_default_update_channel();

create function public.np_release_version_parts(value text) returns integer[]
language sql immutable strict set search_path = pg_catalog as $$
    select case when value ~ '^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$'
        then string_to_array(value, '.')::integer[] else null end;
$$;

create table public.np_erp_releases (
    id uuid primary key default extensions.gen_random_uuid(),
    sequence bigint generated always as identity unique check (sequence between 1 and 9007199254740991),
    version text not null check (public.np_release_version_parts(version) is not null),
    update_channel text not null check (update_channel in ('stable', 'beta', 'internal')),
    platform text not null default 'windows' check (platform = 'windows'),
    architecture text not null default 'x64' check (architecture = 'x64'),
    status text not null default 'draft' check (status in ('draft', 'verified', 'published', 'withdrawn')),
    build_commit text not null check (build_commit ~ '^[a-f0-9]{40}$'),
    artifact_sha256 text not null check (artifact_sha256 ~ '^[a-f0-9]{64}$'),
    artifact_size bigint not null check (artifact_size between 1 and 536870912),
    release_notes text not null check (length(release_notes) between 1 and 4000),
    minimum_supported_version text not null check (
        public.np_release_version_parts(minimum_supported_version) is not null
        and public.np_release_version_parts(minimum_supported_version) <= public.np_release_version_parts(version)),
    mandatory boolean not null default false,
    code_signing text not null check (code_signing in ('CODE_SIGNING_PENDING', 'AUTHENTICODE')),
    signer_thumbprint text,
    created_by uuid not null references public.np_platform_users(id),
    created_at timestamptz not null default clock_timestamp(),
    verified_at timestamptz,
    published_at timestamptz,
    constraint np_release_identity unique (update_channel, platform, architecture, version),
    constraint np_release_signature check (
        (code_signing = 'CODE_SIGNING_PENDING' and signer_thumbprint is null)
        or (code_signing = 'AUTHENTICODE' and signer_thumbprint is not null
            and signer_thumbprint ~ '^([A-F0-9]{40}|[A-F0-9]{64})$')),
    constraint np_release_verified check (status not in ('verified', 'published') or verified_at is not null),
    constraint np_release_published check (status <> 'published' or published_at is not null)
);
alter table public.np_erp_releases enable row level security;
alter table public.np_erp_releases force row level security;
revoke all on public.np_erp_releases from public, anon, authenticated, service_role;
grant select on public.np_erp_releases to service_role;

create function public.np_release_manifest(r public.np_erp_releases) returns jsonb
language sql stable set search_path = pg_catalog as $$
    select jsonb_build_object(
        'schema_version', 1, 'release_id', r.id, 'sequence', r.sequence,
        'version', r.version, 'update_channel', r.update_channel, 'platform', r.platform,
        'architecture', r.architecture, 'build_commit', r.build_commit,
        'artifact_sha256', r.artifact_sha256, 'artifact_size', r.artifact_size,
        'release_notes', r.release_notes, 'minimum_supported_version', r.minimum_supported_version,
        'mandatory', r.mandatory, 'published_at', r.published_at,
        'code_signing', r.code_signing, 'signer_thumbprint', r.signer_thumbprint);
$$;

-- Only trusted server code may call these RPCs, after authenticating platform_admin.
-- Verification must stream the stored bytes and compare hash/size/signature; UI cannot attest it.
create function public.np_admin_create_release(p_actor_id uuid, p_metadata jsonb) returns jsonb
language plpgsql security definer set search_path = pg_catalog as $$
declare r public.np_erp_releases; existing_metadata jsonb;
begin
    if not exists (select 1 from public.np_platform_users where id = p_actor_id and active and role = 'platform_admin') then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;
    if p_metadata is null or jsonb_typeof(p_metadata) <> 'object'
       or (p_metadata - array['version','update_channel','platform','architecture','build_commit',
            'artifact_sha256','artifact_size','release_notes','minimum_supported_version','mandatory',
            'code_signing','signer_thumbprint']) <> '{}'::jsonb
       or (select count(*) from jsonb_object_keys(p_metadata)) <> 12
       or jsonb_typeof(p_metadata->'artifact_size') <> 'number'
       or jsonb_typeof(p_metadata->'mandatory') <> 'boolean'
       or (p_metadata->>'artifact_size') !~ '^[0-9]+$' then
        return jsonb_build_object('ok', false, 'code', 'invalid_payload');
    end if;
    perform pg_advisory_xact_lock(hashtextextended('np-release-catalog', 0));
    select * into r from public.np_erp_releases where version = p_metadata->>'version'
        and update_channel = p_metadata->>'update_channel'
        and platform = p_metadata->>'platform' and architecture = p_metadata->>'architecture';
    if found then
        existing_metadata := public.np_release_manifest(r) - array['schema_version','release_id','sequence','published_at'];
        if existing_metadata <> p_metadata then
            return jsonb_build_object('ok', false, 'code', 'idempotency_conflict');
        end if;
        return jsonb_build_object('ok', true, 'release_id', r.id, 'status', r.status);
    end if;
    insert into public.np_erp_releases(version, update_channel, platform, architecture,
        build_commit, artifact_sha256, artifact_size, release_notes, minimum_supported_version,
        mandatory, code_signing, signer_thumbprint, created_by)
    values (p_metadata->>'version', p_metadata->>'update_channel', p_metadata->>'platform',
        p_metadata->>'architecture', p_metadata->>'build_commit', p_metadata->>'artifact_sha256',
        (p_metadata->>'artifact_size')::bigint, p_metadata->>'release_notes',
        p_metadata->>'minimum_supported_version', (p_metadata->>'mandatory')::boolean,
        p_metadata->>'code_signing', p_metadata->>'signer_thumbprint', p_actor_id) returning * into r;
    insert into public.np_platform_audit_events(actor_user_id, action, target_type, target_id, correlation_id, metadata)
        values (p_actor_id, 'release.created', 'release', r.id::text, r.id::text,
            jsonb_build_object('version', r.version, 'update_channel', r.update_channel));
    return jsonb_build_object('ok', true, 'release_id', r.id, 'status', r.status);
exception when check_violation or not_null_violation or invalid_text_representation or numeric_value_out_of_range then
    return jsonb_build_object('ok', false, 'code', 'invalid_payload');
end;
$$;

create function public.np_admin_verify_release(p_actor_id uuid, p_release_id uuid, p_sha256 text, p_size bigint)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare r public.np_erp_releases;
begin
    if not exists (select 1 from public.np_platform_users where id = p_actor_id and active and role = 'platform_admin') then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;
    select * into r from public.np_erp_releases where id = p_release_id for update;
    if not found or r.status = 'withdrawn' or p_sha256 is distinct from r.artifact_sha256 or p_size is distinct from r.artifact_size then
        return jsonb_build_object('ok', false, 'code', 'invalid_payload');
    end if;
    if r.status = 'draft' then
        update public.np_erp_releases set status = 'verified', verified_at = clock_timestamp() where id = r.id;
        insert into public.np_platform_audit_events(actor_user_id, action, target_type, target_id, correlation_id)
            values (p_actor_id, 'release.verified', 'release', r.id::text, r.id::text);
    end if;
    return jsonb_build_object('ok', true, 'release_id', r.id);
end;
$$;

create function public.np_admin_publish_release(p_actor_id uuid, p_release_id uuid) returns jsonb
language plpgsql security definer set search_path = pg_catalog as $$
declare r public.np_erp_releases;
begin
    if not exists (select 1 from public.np_platform_users where id = p_actor_id and active and role = 'platform_admin') then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;
    perform pg_advisory_xact_lock(hashtextextended('np-release-catalog', 0));
    select * into r from public.np_erp_releases where id = p_release_id for update;
    if not found or r.status not in ('verified', 'published') then
        return jsonb_build_object('ok', false, 'code', 'release_not_verified');
    end if;
    if r.status = 'published' then
        return jsonb_build_object('ok', true, 'release', public.np_release_manifest(r));
    end if;
    if exists (select 1 from public.np_erp_releases old where old.update_channel = r.update_channel
        and old.status in ('published', 'withdrawn') and old.published_at is not null
        and (old.sequence >= r.sequence or public.np_release_version_parts(old.version) >= public.np_release_version_parts(r.version))) then
        return jsonb_build_object('ok', false, 'code', 'release_downgrade');
    end if;
    update public.np_erp_releases set status = 'published', published_at = clock_timestamp()
        where id = r.id returning * into r;
    insert into public.np_platform_audit_events(actor_user_id, action, target_type, target_id, correlation_id, metadata)
        values (p_actor_id, 'release.published', 'release', r.id::text, r.id::text,
            jsonb_build_object('version', r.version, 'update_channel', r.update_channel));
    return jsonb_build_object('ok', true, 'release', public.np_release_manifest(r));
end;
$$;

create function public.np_erp_check_release(p_tenant_id text, p_installation_id text,
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
    return jsonb_build_object('ok', true, 'update_channel', i.update_channel,
        'release', case when r.id is null then null else public.np_release_manifest(r) end);
end;
$$;

revoke all on function public.np_default_update_channel() from public, anon, authenticated, service_role;
revoke all on function public.np_release_version_parts(text) from public, anon, authenticated;
revoke all on function public.np_release_manifest(public.np_erp_releases) from public, anon, authenticated;
revoke all on function public.np_admin_create_release(uuid,jsonb) from public, anon, authenticated;
revoke all on function public.np_admin_verify_release(uuid,uuid,text,bigint) from public, anon, authenticated;
revoke all on function public.np_admin_publish_release(uuid,uuid) from public, anon, authenticated;
revoke all on function public.np_erp_check_release(text,text,text,text,timestamptz,text) from public, anon, authenticated;
grant execute on function public.np_release_version_parts(text), public.np_release_manifest(public.np_erp_releases),
    public.np_admin_create_release(uuid,jsonb), public.np_admin_verify_release(uuid,uuid,text,bigint),
    public.np_admin_publish_release(uuid,uuid), public.np_erp_check_release(text,text,text,text,timestamptz,text) to service_role;

-- Storage is supplied by Supabase, not by the networkless PostgreSQL QA harness.
do $$ begin
    if to_regclass('storage.buckets') is not null then
        if exists (select 1 from storage.buckets where id = 'nexpoint-releases' and public) then
            raise exception 'Release bucket must be private';
        end if;
        insert into storage.buckets(id, name, public, file_size_limit, allowed_mime_types)
            values('nexpoint-releases', 'nexpoint-releases', false, 52428800, array['application/octet-stream'])
            on conflict (id) do nothing;
    end if;
end $$;
