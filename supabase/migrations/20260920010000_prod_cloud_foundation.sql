-- NexPoint ERP - minimal production cloud data model.
-- The operational customer/finance database remains local in SQLite.  This
-- schema stores only identity, delivery state and sanitized operational
-- telemetry required by the production control plane.

create extension if not exists pgcrypto with schema extensions;

create schema if not exists nexa_private;
revoke all on schema nexa_private from public, anon, authenticated;

create table nexa_private.erp_replay_nonces (
    bridge_key text not null check (bridge_key ~ '^[0-9a-f]{64}$'),
    nonce_hash text not null check (nonce_hash ~ '^[0-9a-f]{64}$'),
    expires_at timestamptz not null,
    created_at timestamptz not null default pg_catalog.clock_timestamp(),
    primary key (bridge_key, nonce_hash)
);
create index erp_replay_nonces_expiry_idx
    on nexa_private.erp_replay_nonces (expires_at);
alter table nexa_private.erp_replay_nonces enable row level security;
alter table nexa_private.erp_replay_nonces force row level security;
revoke all on nexa_private.erp_replay_nonces
    from public, anon, authenticated, service_role;

create or replace function public.nexa_claim_erp_nonce(
    p_bridge_key text,
    p_nonce_hash text
)
returns boolean
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    inserted_count bigint;
begin
    if p_bridge_key is null or p_bridge_key !~ '^[0-9a-f]{64}$'
       or p_nonce_hash is null or p_nonce_hash !~ '^[0-9a-f]{64}$'
    then
        return false;
    end if;
    delete from nexa_private.erp_replay_nonces
     where expires_at <= clock_timestamp();
    insert into nexa_private.erp_replay_nonces (
        bridge_key, nonce_hash, expires_at
    ) values (
        p_bridge_key, p_nonce_hash, clock_timestamp() + interval '121 seconds'
    ) on conflict do nothing;
    get diagnostics inserted_count = row_count;
    return inserted_count = 1;
end;
$$;

create type public.np_tenant_kind as enum ('internal', 'customer', 'test', 'demo');
create type public.np_tenant_status as enum ('active', 'inactive', 'suspended', 'closed');
create type public.np_environment as enum ('prod', 'qa', 'dev', 'local');
create type public.np_installation_status as enum ('active', 'suspended', 'revoked');
create type public.np_credential_kind as enum ('hmac_sha256', 'opaque_token');
create type public.np_sync_kind as enum (
    'support_ticket',
    'heartbeat',
    'health',
    'version',
    'observability_batch',
    'risk',
    'incident',
    'fingerprint',
    'admin_recovery',
    'diagnostic_event'
);
create type public.np_sync_state as enum ('persisted', 'rejected');
create type public.np_health_state as enum (
    'healthy', 'normal', 'warning', 'high', 'critical',
    'degraded', 'unavailable', 'offline', 'unknown'
);
create type public.np_event_level as enum ('debug', 'info', 'warning', 'error', 'critical');
create type public.np_risk_severity as enum ('normal', 'low', 'warning', 'medium', 'high', 'critical');
create type public.np_risk_status as enum (
    'open', 'acknowledged', 'monitoring', 'mitigated', 'resolved', 'dismissed', 'closed'
);
create type public.np_incident_status as enum (
    'open', 'investigating', 'monitoring', 'resolved', 'closed'
);
create type public.np_ticket_status as enum (
    'open', 'in_progress', 'waiting_customer', 'resolved', 'closed', 'cancelled'
);
create type public.np_recovery_status as enum ('pending', 'authorized', 'consumed', 'denied', 'expired');
create type public.np_platform_role as enum ('platform_admin', 'nexpoint_control_admin');

create or replace function public.np_identifier_is_valid(value text, max_length integer default 160)
returns boolean
language sql
immutable
parallel safe
set search_path = pg_catalog, public
as $$
    select value is not null
       and length(value) between 1 and max_length
       and value ~ '^[A-Za-z0-9][-A-Za-z0-9._:@/+]*$';
$$;

create or replace function public.np_jsonb_is_sanitized(payload jsonb)
returns boolean
language plpgsql
immutable
parallel safe
set search_path = pg_catalog, public
as $$
declare
    stack jsonb[] := array[coalesce(payload, '{}'::jsonb)];
    node jsonb;
    child jsonb;
    field_name text;
    normalized_name text;
    marker text;
    forbidden_markers constant text[] := array[
        'password', 'passwd', 'passphrase', 'senha', 'token',
        'authorization', 'cookie', 'secret', 'segredo', 'credential',
        'servicerole', 'jwt', 'privatekey', 'apikey', 'hmac',
        'cpf', 'cnpj', 'document', 'customername', 'clientname', 'address',
        'paymentcard', 'cardnumber', 'cvv'
    ];
begin
    if payload is null or octet_length(payload::text) > 65536 then
        return false;
    end if;

    while cardinality(stack) > 0 loop
        node := stack[cardinality(stack)];
        stack := stack[1:cardinality(stack) - 1];

        if jsonb_typeof(node) = 'object' then
            for field_name, child in select key, value from jsonb_each(node)
            loop
                normalized_name := regexp_replace(lower(field_name), '[^a-z0-9]', '', 'g');
                foreach marker in array forbidden_markers loop
                    if strpos(normalized_name, marker) > 0 then
                        return false;
                    end if;
                end loop;
                stack := array_append(stack, child);
            end loop;
        elsif jsonb_typeof(node) = 'array' then
            for child in select value from jsonb_array_elements(node)
            loop
                stack := array_append(stack, child);
            end loop;
        end if;
    end loop;

    return true;
end;
$$;

create or replace function public.np_set_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
begin
    new.updated_at := clock_timestamp();
    return new;
end;
$$;

create table public.np_tenants (
    id uuid primary key default extensions.gen_random_uuid(),
    tenant_key text not null unique,
    display_name text not null,
    kind public.np_tenant_kind not null,
    status public.np_tenant_status not null default 'active',
    billing_exempt boolean not null default false,
    created_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    constraint np_tenants_key_valid check (public.np_identifier_is_valid(tenant_key, 80)),
    constraint np_tenants_name_length check (length(display_name) between 1 and 160),
    constraint np_noncommercial_tenant_not_billing_exempt check (
        kind not in ('test', 'demo') or billing_exempt = false
    )
);

create table public.np_platform_users (
    id uuid primary key default extensions.gen_random_uuid(),
    username text not null,
    display_name text not null,
    role public.np_platform_role not null,
    password_hash text not null,
    active boolean not null default true,
    last_login_at timestamptz,
    created_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    constraint np_platform_users_username_valid check (
        username = lower(username)
        and username ~ '^[a-z0-9][-a-z0-9@._+]{2,179}$'
    ),
    constraint np_platform_users_display_name_length check (
        length(display_name) between 1 and 160
    ),
    constraint np_platform_users_password_hash check (
        length(password_hash) between 40 and 512
        and (
            password_hash like 'scrypt$%'
            or password_hash like 'pbkdf2$%'
            or password_hash like 'argon2%'
            or password_hash ~ '^[$]2[aby][$]'
        )
    )
);

create unique index np_platform_users_username_ci_unique
    on public.np_platform_users (username);

create table public.np_platform_user_tenants (
    user_id uuid not null references public.np_platform_users(id) on delete cascade,
    tenant_id uuid not null references public.np_tenants(id) on delete cascade,
    created_at timestamptz not null default clock_timestamp(),
    primary key (user_id, tenant_id)
);

create table public.np_platform_audit_events (
    id uuid primary key default extensions.gen_random_uuid(),
    actor_user_id uuid references public.np_platform_users(id) on delete set null,
    action text not null,
    target_type text not null,
    target_id text,
    correlation_id text not null,
    metadata jsonb not null default '{}'::jsonb,
    occurred_at timestamptz not null default clock_timestamp(),
    constraint np_platform_audit_action_valid check (
        public.np_identifier_is_valid(action, 120)
        and public.np_identifier_is_valid(target_type, 80)
        and (target_id is null or public.np_identifier_is_valid(target_id, 160))
        and public.np_identifier_is_valid(correlation_id, 160)
    ),
    constraint np_platform_audit_metadata_sanitized check (
        public.np_jsonb_is_sanitized(metadata)
        and octet_length(metadata::text) <= 16384
    )
);

create table public.np_installations (
    id uuid primary key default extensions.gen_random_uuid(),
    tenant_id uuid not null references public.np_tenants(id) on delete restrict,
    installation_key text not null,
    label text not null,
    environment public.np_environment not null,
    channel public.np_environment not null,
    status public.np_installation_status not null default 'active',
    app_version text,
    build_id text,
    build_commit text,
    health_state public.np_health_state not null default 'unknown',
    last_seen_at timestamptz,
    created_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    constraint np_installations_tenant_id_id_unique unique (tenant_id, id),
    constraint np_installations_key_unique unique (installation_key),
    constraint np_installations_key_valid check (public.np_identifier_is_valid(installation_key, 120)),
    constraint np_installations_label_length check (length(label) between 1 and 160),
    constraint np_installations_version_length check (app_version is null or length(app_version) between 1 and 64),
    constraint np_installations_build_id_format check (
        build_id is null or public.np_identifier_is_valid(build_id, 80)
    ),
    constraint np_installations_commit_format check (
        build_commit is null or build_commit ~ '^(unknown|[0-9a-f]{7,40})$'
    ),
    constraint np_installations_prod_channel check (environment <> 'prod' or channel = 'prod'),
    constraint np_installations_qa_channel check (environment <> 'qa' or channel = 'qa')
);

create table public.np_installation_credentials (
    id uuid primary key default extensions.gen_random_uuid(),
    tenant_id uuid not null,
    installation_id uuid not null,
    key_id text not null,
    kind public.np_credential_kind not null,
    secret_digest bytea not null,
    valid_from timestamptz not null default clock_timestamp(),
    expires_at timestamptz,
    revoked_at timestamptz,
    created_at timestamptz not null default clock_timestamp(),
    constraint np_installation_credentials_installation_fk
        foreign key (tenant_id, installation_id)
        references public.np_installations(tenant_id, id) on delete cascade,
    constraint np_installation_credentials_key_unique unique (tenant_id, installation_id, key_id),
    constraint np_installation_credentials_key_valid check (public.np_identifier_is_valid(key_id, 100)),
    constraint np_installation_credentials_material check (octet_length(secret_digest) = 32),
    constraint np_installation_credentials_expiry check (expires_at is null or expires_at > valid_from),
    constraint np_installation_credentials_revocation check (revoked_at is null or revoked_at >= valid_from)
);

create table public.np_sync_envelopes (
    id uuid primary key default extensions.gen_random_uuid(),
    tenant_id uuid not null,
    installation_id uuid not null,
    idempotency_key text not null,
    correlation_id text not null,
    aggregate_type text not null,
    aggregate_id text not null,
    kind public.np_sync_kind not null,
    schema_version integer not null,
    occurred_at timestamptz not null,
    received_at timestamptz not null default clock_timestamp(),
    state public.np_sync_state not null default 'persisted',
    payload jsonb not null default '{}'::jsonb,
    -- Keep an immutable digest when the verbose payload is later redacted.  The
    -- digest is part of the durable idempotency tombstone and must never be
    -- recomputed from the redacted value.
    payload_hash bytea not null,
    payload_retention_until timestamptz not null,
    payload_redacted_at timestamptz,
    purge_after timestamptz not null,
    constraint np_sync_envelopes_installation_fk
        foreign key (tenant_id, installation_id)
        references public.np_installations(tenant_id, id) on delete restrict,
    constraint np_sync_envelopes_tenant_installation_id_unique unique (tenant_id, installation_id, id),
    constraint np_sync_envelopes_idempotent unique (tenant_id, installation_id, idempotency_key),
    constraint np_sync_envelopes_idempotency_valid check (public.np_identifier_is_valid(idempotency_key, 200)),
    constraint np_sync_envelopes_correlation_valid check (public.np_identifier_is_valid(correlation_id, 160)),
    constraint np_sync_envelopes_aggregate_type_valid check (
        public.np_identifier_is_valid(aggregate_type, 128)
    ),
    constraint np_sync_envelopes_aggregate_id_valid check (
        public.np_identifier_is_valid(aggregate_id, 128)
    ),
    constraint np_sync_envelopes_schema_version check (schema_version = 1),
    constraint np_sync_envelopes_payload_sanitized check (public.np_jsonb_is_sanitized(payload)),
    constraint np_sync_envelopes_payload_size check (octet_length(payload::text) <= 65536),
    constraint np_sync_envelopes_payload_hash check (octet_length(payload_hash) = 32),
    constraint np_sync_envelopes_retention check (
        payload_retention_until >= received_at
        and purge_after >= payload_retention_until + interval '30 days'
    ),
    constraint np_sync_envelopes_redaction check (
        payload_redacted_at is null or payload = '{}'::jsonb
    )
);

create table public.np_sync_acks (
    id uuid primary key default extensions.gen_random_uuid(),
    tenant_id uuid not null,
    installation_id uuid not null,
    envelope_id uuid not null,
    ack_key text not null,
    persisted_at timestamptz not null default clock_timestamp(),
    response_metadata jsonb not null default '{}'::jsonb,
    constraint np_sync_acks_envelope_fk
        foreign key (tenant_id, installation_id, envelope_id)
        references public.np_sync_envelopes(tenant_id, installation_id, id) on delete restrict,
    constraint np_sync_acks_one_per_envelope unique (envelope_id),
    constraint np_sync_acks_key_unique unique (tenant_id, installation_id, ack_key),
    constraint np_sync_acks_key_valid check (public.np_identifier_is_valid(ack_key, 200)),
    constraint np_sync_acks_metadata_sanitized check (public.np_jsonb_is_sanitized(response_metadata)),
    constraint np_sync_acks_metadata_size check (octet_length(response_metadata::text) <= 8192)
);

create table public.np_heartbeats (
    id uuid primary key default extensions.gen_random_uuid(),
    tenant_id uuid not null,
    installation_id uuid not null,
    idempotency_key text not null,
    correlation_id text not null,
    reported_at timestamptz not null,
    received_at timestamptz not null default clock_timestamp(),
    app_version text not null,
    build_id text not null,
    build_commit text,
    sync_status text not null,
    retention_until timestamptz not null default (clock_timestamp() + interval '30 days'),
    constraint np_heartbeats_installation_fk
        foreign key (tenant_id, installation_id)
        references public.np_installations(tenant_id, id) on delete restrict,
    constraint np_heartbeats_idempotent unique (tenant_id, installation_id, idempotency_key),
    constraint np_heartbeats_idempotency_valid check (public.np_identifier_is_valid(idempotency_key, 200)),
    constraint np_heartbeats_correlation_valid check (public.np_identifier_is_valid(correlation_id, 160)),
    constraint np_heartbeats_version_length check (length(app_version) between 1 and 64),
    constraint np_heartbeats_build_id_format check (
        public.np_identifier_is_valid(build_id, 80)
    ),
    constraint np_heartbeats_commit_format check (
        build_commit is null or build_commit ~ '^(unknown|[0-9a-f]{7,40})$'
    ),
    constraint np_heartbeats_sync_status check (sync_status in ('offline', 'syncing', 'synced', 'degraded')),
    constraint np_heartbeats_retention check (retention_until >= received_at + interval '7 days')
);

create table public.np_health_snapshots (
    id uuid primary key default extensions.gen_random_uuid(),
    tenant_id uuid not null,
    installation_id uuid not null,
    idempotency_key text not null,
    correlation_id text not null,
    reported_at timestamptz not null,
    received_at timestamptz not null default clock_timestamp(),
    app_state public.np_health_state not null default 'unknown',
    database_state public.np_health_state not null default 'unknown',
    migration_state public.np_health_state not null default 'unknown',
    outbox_state public.np_health_state not null default 'unknown',
    sync_state public.np_health_state not null default 'unknown',
    nexa_state public.np_health_state not null default 'unknown',
    risk_score integer not null default 0,
    recent_errors integer not null default 0,
    retry_count integer not null default 0,
    latency_ms integer,
    fingerprints jsonb not null default '[]'::jsonb,
    doctor_pass integer not null default 0,
    doctor_warn integer not null default 0,
    doctor_fail integer not null default 0,
    metadata jsonb not null default '{}'::jsonb,
    retention_until timestamptz not null default (clock_timestamp() + interval '90 days'),
    constraint np_health_snapshots_installation_fk
        foreign key (tenant_id, installation_id)
        references public.np_installations(tenant_id, id) on delete restrict,
    constraint np_health_snapshots_idempotent unique (tenant_id, installation_id, idempotency_key),
    constraint np_health_snapshots_idempotency_valid check (public.np_identifier_is_valid(idempotency_key, 200)),
    constraint np_health_snapshots_correlation_valid check (public.np_identifier_is_valid(correlation_id, 160)),
    constraint np_health_snapshots_doctor_counts check (
        doctor_pass >= 0 and doctor_warn >= 0 and doctor_fail >= 0
    ),
    constraint np_health_snapshots_operational_counts check (
        risk_score between 0 and 100
        and recent_errors >= 0
        and retry_count >= 0
        and (latency_ms is null or latency_ms between 0 and 3600000)
    ),
    constraint np_health_snapshots_fingerprints_array check (
        jsonb_typeof(fingerprints) = 'array'
        and octet_length(fingerprints::text) <= 16384
        and public.np_jsonb_is_sanitized(fingerprints)
    ),
    constraint np_health_snapshots_metadata_sanitized check (public.np_jsonb_is_sanitized(metadata)),
    constraint np_health_snapshots_metadata_size check (octet_length(metadata::text) <= 16384),
    constraint np_health_snapshots_retention check (retention_until >= received_at + interval '7 days')
);

create table public.np_installation_versions (
    id uuid primary key default extensions.gen_random_uuid(),
    tenant_id uuid not null,
    installation_id uuid not null,
    idempotency_key text not null,
    correlation_id text not null,
    version text not null,
    build_id text not null,
    build_commit text not null,
    environment public.np_environment not null,
    channel public.np_environment not null,
    reported_at timestamptz not null,
    received_at timestamptz not null default clock_timestamp(),
    constraint np_installation_versions_installation_fk
        foreign key (tenant_id, installation_id)
        references public.np_installations(tenant_id, id) on delete restrict,
    constraint np_installation_versions_idempotent unique (tenant_id, installation_id, idempotency_key),
    constraint np_installation_versions_idempotency_valid check (public.np_identifier_is_valid(idempotency_key, 200)),
    constraint np_installation_versions_correlation_valid check (public.np_identifier_is_valid(correlation_id, 160)),
    constraint np_installation_versions_version_length check (length(version) between 1 and 64),
    constraint np_installation_versions_build_id_format check (
        public.np_identifier_is_valid(build_id, 80)
    ),
    constraint np_installation_versions_commit_format check (
        build_commit ~ '^(unknown|[0-9a-f]{7,40})$'
    ),
    constraint np_installation_versions_prod_channel check (environment <> 'prod' or channel = 'prod'),
    constraint np_installation_versions_qa_channel check (environment <> 'qa' or channel = 'qa')
);

create table public.np_observability_events (
    id uuid primary key default extensions.gen_random_uuid(),
    tenant_id uuid not null,
    installation_id uuid not null,
    idempotency_key text not null,
    correlation_id text not null,
    event_name text not null,
    level public.np_event_level not null,
    component text not null,
    occurred_at timestamptz not null,
    received_at timestamptz not null default clock_timestamp(),
    fingerprint text,
    metadata jsonb not null default '{}'::jsonb,
    retention_until timestamptz not null default (clock_timestamp() + interval '180 days'),
    constraint np_observability_events_installation_fk
        foreign key (tenant_id, installation_id)
        references public.np_installations(tenant_id, id) on delete restrict,
    constraint np_observability_events_idempotent unique (tenant_id, installation_id, idempotency_key),
    constraint np_observability_events_idempotency_valid check (public.np_identifier_is_valid(idempotency_key, 200)),
    constraint np_observability_events_correlation_valid check (public.np_identifier_is_valid(correlation_id, 160)),
    constraint np_observability_events_name_valid check (public.np_identifier_is_valid(event_name, 160)),
    constraint np_observability_events_component_valid check (public.np_identifier_is_valid(component, 100)),
    constraint np_observability_events_fingerprint_valid check (
        fingerprint is null or public.np_identifier_is_valid(fingerprint, 160)
    ),
    constraint np_observability_events_metadata_sanitized check (public.np_jsonb_is_sanitized(metadata)),
    constraint np_observability_events_metadata_size check (octet_length(metadata::text) <= 32768),
    constraint np_observability_events_retention check (retention_until >= received_at + interval '30 days')
);

create table public.np_diagnostic_events (
    id uuid primary key default extensions.gen_random_uuid(),
    tenant_id uuid not null,
    installation_id uuid not null,
    idempotency_key text not null,
    correlation_id text not null,
    source_event_id text not null,
    check_name text not null,
    result public.np_health_state not null,
    level public.np_event_level not null,
    module text not null,
    component text not null,
    environment text not null,
    operation text not null,
    event_status text not null,
    duration_ms integer,
    error_code text,
    fingerprint text not null,
    retry_count integer not null default 0,
    schema_version integer not null default 1,
    app_version text,
    build text,
    occurred_at timestamptz not null,
    received_at timestamptz not null default clock_timestamp(),
    details jsonb not null default '{}'::jsonb,
    retention_until timestamptz not null default (clock_timestamp() + interval '180 days'),
    constraint np_diagnostic_events_installation_fk
        foreign key (tenant_id, installation_id)
        references public.np_installations(tenant_id, id) on delete restrict,
    constraint np_diagnostic_events_idempotent unique (tenant_id, installation_id, idempotency_key),
    constraint np_diagnostic_events_idempotency_valid check (public.np_identifier_is_valid(idempotency_key, 200)),
    constraint np_diagnostic_events_correlation_valid check (public.np_identifier_is_valid(correlation_id, 160)),
    constraint np_diagnostic_events_check_valid check (public.np_identifier_is_valid(check_name, 160)),
    constraint np_diagnostic_events_source_valid check (
        public.np_identifier_is_valid(source_event_id, 128)
    ),
    constraint np_diagnostic_events_codes_valid check (
        public.np_identifier_is_valid(module, 100)
        and public.np_identifier_is_valid(component, 100)
        and public.np_identifier_is_valid(environment, 40)
        and public.np_identifier_is_valid(operation, 100)
        and public.np_identifier_is_valid(event_status, 100)
        and (error_code is null or public.np_identifier_is_valid(error_code, 100))
        and public.np_identifier_is_valid(fingerprint, 160)
    ),
    constraint np_diagnostic_events_numbers check (
        (duration_ms is null or duration_ms between 0 and 3600000)
        and retry_count between 0 and 1000
        and schema_version = 1
    ),
    constraint np_diagnostic_events_build_lengths check (
        (app_version is null or length(app_version) between 1 and 80)
        and (build is null or length(build) between 1 and 80)
    ),
    constraint np_diagnostic_events_details_sanitized check (public.np_jsonb_is_sanitized(details)),
    constraint np_diagnostic_events_details_size check (octet_length(details::text) <= 32768),
    constraint np_diagnostic_events_retention check (retention_until >= received_at + interval '30 days')
);

create table public.np_fingerprints (
    id uuid primary key default extensions.gen_random_uuid(),
    tenant_id uuid not null,
    installation_id uuid not null,
    fingerprint text not null,
    category text not null,
    first_seen_at timestamptz not null,
    last_seen_at timestamptz not null,
    occurrence_count bigint not null default 1,
    sample_metadata jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default clock_timestamp(),
    constraint np_fingerprints_installation_fk
        foreign key (tenant_id, installation_id)
        references public.np_installations(tenant_id, id) on delete restrict,
    constraint np_fingerprints_unique unique (tenant_id, installation_id, fingerprint),
    constraint np_fingerprints_value_valid check (public.np_identifier_is_valid(fingerprint, 160)),
    constraint np_fingerprints_category_valid check (public.np_identifier_is_valid(category, 100)),
    constraint np_fingerprints_times check (last_seen_at >= first_seen_at),
    constraint np_fingerprints_count check (occurrence_count > 0),
    constraint np_fingerprints_metadata_sanitized check (public.np_jsonb_is_sanitized(sample_metadata)),
    constraint np_fingerprints_metadata_size check (octet_length(sample_metadata::text) <= 16384)
);

create table public.np_risks (
    id uuid primary key default extensions.gen_random_uuid(),
    tenant_id uuid not null,
    installation_id uuid not null,
    idempotency_key text not null,
    correlation_id text not null,
    source_risk_id text not null,
    fingerprint text not null,
    severity public.np_risk_severity not null,
    status public.np_risk_status not null default 'open',
    title text not null,
    module text not null default 'erp',
    score integer not null default 0,
    confidence text not null default 'medium',
    evidence jsonb not null default '[]'::jsonb,
    probable_cause text,
    detected_at timestamptz not null,
    resolved_at timestamptz,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    constraint np_risks_installation_fk
        foreign key (tenant_id, installation_id)
        references public.np_installations(tenant_id, id) on delete restrict,
    constraint np_risks_idempotent unique (tenant_id, installation_id, idempotency_key),
    constraint np_risks_source_unique unique (tenant_id, installation_id, source_risk_id),
    constraint np_risks_idempotency_valid check (public.np_identifier_is_valid(idempotency_key, 200)),
    constraint np_risks_correlation_valid check (public.np_identifier_is_valid(correlation_id, 160)),
    constraint np_risks_fingerprint_valid check (public.np_identifier_is_valid(fingerprint, 160)),
    constraint np_risks_source_valid check (public.np_identifier_is_valid(source_risk_id, 128)),
    constraint np_risks_module_valid check (public.np_identifier_is_valid(module, 100)),
    constraint np_risks_score check (score between 0 and 100),
    constraint np_risks_confidence check (confidence in ('low', 'medium', 'high')),
    constraint np_risks_evidence check (
        jsonb_typeof(evidence) = 'array'
        and octet_length(evidence::text) <= 16384
        and public.np_jsonb_is_sanitized(evidence)
    ),
    constraint np_risks_probable_cause_length check (
        probable_cause is null or length(probable_cause) between 1 and 500
    ),
    constraint np_risks_title_length check (length(title) between 1 and 240),
    constraint np_risks_resolution check (resolved_at is null or resolved_at >= detected_at),
    constraint np_risks_metadata_sanitized check (public.np_jsonb_is_sanitized(metadata)),
    constraint np_risks_metadata_size check (octet_length(metadata::text) <= 32768)
);

create table public.np_incidents (
    id uuid primary key default extensions.gen_random_uuid(),
    tenant_id uuid not null,
    installation_id uuid not null,
    idempotency_key text not null,
    correlation_id text not null,
    source_incident_id text not null,
    fingerprint text,
    status public.np_incident_status not null default 'open',
    severity public.np_risk_severity not null,
    title text not null,
    opened_at timestamptz not null,
    resolved_at timestamptz,
    summary jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    constraint np_incidents_installation_fk
        foreign key (tenant_id, installation_id)
        references public.np_installations(tenant_id, id) on delete restrict,
    constraint np_incidents_idempotent unique (tenant_id, installation_id, idempotency_key),
    constraint np_incidents_idempotency_valid check (public.np_identifier_is_valid(idempotency_key, 200)),
    constraint np_incidents_correlation_valid check (public.np_identifier_is_valid(correlation_id, 160)),
    constraint np_incidents_source_valid check (
        public.np_identifier_is_valid(source_incident_id, 128)
    ),
    constraint np_incidents_fingerprint_valid check (
        fingerprint is null or public.np_identifier_is_valid(fingerprint, 160)
    ),
    constraint np_incidents_title_length check (length(title) between 1 and 240),
    constraint np_incidents_resolution check (resolved_at is null or resolved_at >= opened_at),
    constraint np_incidents_summary_sanitized check (public.np_jsonb_is_sanitized(summary)),
    constraint np_incidents_summary_size check (octet_length(summary::text) <= 32768)
);

create table public.np_support_tickets (
    id uuid primary key default extensions.gen_random_uuid(),
    tenant_id uuid not null,
    installation_id uuid not null,
    idempotency_key text not null,
    correlation_id text not null,
    source_ticket_id text not null,
    category text not null,
    status public.np_ticket_status not null default 'open',
    priority text not null default 'normal',
    subject text not null,
    sanitized_description text not null,
    metadata jsonb not null default '{}'::jsonb,
    opened_at timestamptz not null,
    closed_at timestamptz,
    created_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    constraint np_support_tickets_installation_fk
        foreign key (tenant_id, installation_id)
        references public.np_installations(tenant_id, id) on delete restrict,
    constraint np_support_tickets_idempotent unique (tenant_id, installation_id, idempotency_key),
    constraint np_support_tickets_source_unique unique (tenant_id, installation_id, source_ticket_id),
    constraint np_support_tickets_tenant_installation_id_unique unique (tenant_id, installation_id, id),
    constraint np_support_tickets_idempotency_valid check (public.np_identifier_is_valid(idempotency_key, 200)),
    constraint np_support_tickets_correlation_valid check (public.np_identifier_is_valid(correlation_id, 160)),
    constraint np_support_tickets_source_valid check (
        public.np_identifier_is_valid(source_ticket_id, 128)
    ),
    constraint np_support_tickets_category_valid check (public.np_identifier_is_valid(category, 100)),
    constraint np_support_tickets_subject_length check (length(subject) between 1 and 240),
    constraint np_support_tickets_description_length check (length(sanitized_description) between 1 and 4000),
    constraint np_support_tickets_priority check (priority in ('normal', 'high')),
    constraint np_support_tickets_metadata_sanitized check (
        public.np_jsonb_is_sanitized(metadata)
        and octet_length(metadata::text) <= 65536
    ),
    constraint np_support_tickets_closed_at check (closed_at is null or closed_at >= opened_at)
);

create table public.np_support_ticket_events (
    id uuid primary key default extensions.gen_random_uuid(),
    tenant_id uuid not null,
    installation_id uuid not null,
    ticket_id uuid not null,
    idempotency_key text not null,
    event_type text not null,
    occurred_at timestamptz not null,
    metadata jsonb not null default '{}'::jsonb,
    constraint np_support_ticket_events_ticket_fk
        foreign key (tenant_id, installation_id, ticket_id)
        references public.np_support_tickets(tenant_id, installation_id, id) on delete restrict,
    constraint np_support_ticket_events_idempotent unique (tenant_id, installation_id, idempotency_key),
    constraint np_support_ticket_events_idempotency_valid check (public.np_identifier_is_valid(idempotency_key, 200)),
    constraint np_support_ticket_events_type_valid check (public.np_identifier_is_valid(event_type, 100)),
    constraint np_support_ticket_events_metadata_sanitized check (public.np_jsonb_is_sanitized(metadata)),
    constraint np_support_ticket_events_metadata_size check (octet_length(metadata::text) <= 16384)
);

create table public.np_admin_recovery_requests (
    id uuid primary key default extensions.gen_random_uuid(),
    tenant_id uuid not null,
    installation_id uuid not null,
    support_ticket_id uuid not null,
    idempotency_key text not null,
    correlation_id text not null,
    requester_fingerprint text not null,
    status public.np_recovery_status not null default 'pending',
    requested_at timestamptz not null,
    expires_at timestamptz not null,
    decided_at timestamptz,
    consumed_at timestamptz,
    created_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    constraint np_admin_recovery_requests_installation_fk
        foreign key (tenant_id, installation_id)
        references public.np_installations(tenant_id, id) on delete restrict,
    constraint np_admin_recovery_requests_ticket_fk
        foreign key (tenant_id, installation_id, support_ticket_id)
        references public.np_support_tickets(tenant_id, installation_id, id) on delete restrict,
    constraint np_admin_recovery_requests_ticket_unique unique (support_ticket_id),
    constraint np_admin_recovery_requests_tenant_installation_id_unique
        unique (tenant_id, installation_id, id),
    constraint np_admin_recovery_requests_idempotent unique (tenant_id, installation_id, idempotency_key),
    constraint np_admin_recovery_requests_idempotency_valid check (public.np_identifier_is_valid(idempotency_key, 200)),
    constraint np_admin_recovery_requests_correlation_valid check (public.np_identifier_is_valid(correlation_id, 160)),
    constraint np_admin_recovery_requests_fingerprint_valid check (
        public.np_identifier_is_valid(requester_fingerprint, 160)
    ),
    constraint np_admin_recovery_requests_expiry check (expires_at > requested_at),
    constraint np_admin_recovery_requests_decision check (decided_at is null or decided_at >= requested_at),
    constraint np_admin_recovery_requests_consumption check (consumed_at is null or consumed_at >= requested_at)
);

create table public.np_admin_reset_authorizations (
    id uuid primary key default extensions.gen_random_uuid(),
    tenant_id uuid not null,
    installation_id uuid not null,
    recovery_request_id uuid not null,
    support_ticket_id uuid not null,
    authorization_digest bytea not null,
    authorized_by uuid not null references public.np_platform_users(id) on delete restrict,
    authorized_at timestamptz not null default clock_timestamp(),
    expires_at timestamptz not null,
    consumed_at timestamptz,
    revoked_at timestamptz,
    attempt_count integer not null default 0,
    lockout_until timestamptz,
    constraint np_admin_reset_authorizations_request_fk
        foreign key (tenant_id, installation_id, recovery_request_id)
        references public.np_admin_recovery_requests(tenant_id, installation_id, id) on delete restrict,
    constraint np_admin_reset_authorizations_ticket_fk
        foreign key (tenant_id, installation_id, support_ticket_id)
        references public.np_support_tickets(tenant_id, installation_id, id) on delete restrict,
    constraint np_admin_reset_authorizations_digest check (
        octet_length(authorization_digest) = 32
    ),
    constraint np_admin_reset_authorizations_attempts check (
        attempt_count between 0 and 1000000
    ),
    constraint np_admin_reset_authorizations_expiry check (expires_at > authorized_at),
    constraint np_admin_reset_authorizations_consumed check (consumed_at is null or consumed_at >= authorized_at),
    constraint np_admin_reset_authorizations_revoked check (revoked_at is null or revoked_at >= authorized_at)
);

create table public.np_request_nonces (
    id uuid primary key default extensions.gen_random_uuid(),
    tenant_id uuid not null,
    installation_id uuid not null,
    nonce text not null,
    request_timestamp timestamptz not null,
    accepted_at timestamptz not null default clock_timestamp(),
    expires_at timestamptz not null,
    constraint np_request_nonces_installation_fk
        foreign key (tenant_id, installation_id)
        references public.np_installations(tenant_id, id) on delete cascade,
    constraint np_request_nonces_unique unique (tenant_id, installation_id, nonce),
    constraint np_request_nonces_value_valid check (public.np_identifier_is_valid(nonce, 200)),
    constraint np_request_nonces_expiry check (expires_at > accepted_at)
);

create or replace function public.np_enforce_environment_isolation()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
declare
    tenant_kind public.np_tenant_kind;
begin
    select kind into tenant_kind
      from public.np_tenants
     where id = new.tenant_id;

    if tenant_kind is null then
        raise exception 'tenant context is missing' using errcode = '23503';
    end if;
    if new.environment = 'prod' and tenant_kind in ('test', 'demo') then
        raise exception 'non-customer tenant cannot own a production installation' using errcode = '23514';
    end if;
    if new.environment = 'qa' and tenant_kind not in ('test', 'demo') then
        raise exception 'QA installation requires a test tenant' using errcode = '23514';
    end if;
    return new;
end;
$$;

create trigger np_installations_environment_guard
before insert or update of tenant_id, environment on public.np_installations
for each row execute function public.np_enforce_environment_isolation();

create trigger np_tenants_updated_at
before update on public.np_tenants
for each row execute function public.np_set_updated_at();
create trigger np_platform_users_updated_at
before update on public.np_platform_users
for each row execute function public.np_set_updated_at();
create trigger np_installations_updated_at
before update on public.np_installations
for each row execute function public.np_set_updated_at();
create trigger np_fingerprints_updated_at
before update on public.np_fingerprints
for each row execute function public.np_set_updated_at();
create trigger np_risks_updated_at
before update on public.np_risks
for each row execute function public.np_set_updated_at();
create trigger np_incidents_updated_at
before update on public.np_incidents
for each row execute function public.np_set_updated_at();
create trigger np_support_tickets_updated_at
before update on public.np_support_tickets
for each row execute function public.np_set_updated_at();
create trigger np_admin_recovery_requests_updated_at
before update on public.np_admin_recovery_requests
for each row execute function public.np_set_updated_at();

create index np_installations_tenant_status_idx
    on public.np_installations (tenant_id, status, environment);
create index np_installations_last_seen_idx
    on public.np_installations (tenant_id, last_seen_at desc);
create index np_installation_credentials_active_idx
    on public.np_installation_credentials (tenant_id, installation_id, valid_from desc)
    where revoked_at is null;
create index np_sync_envelopes_received_idx
    on public.np_sync_envelopes (tenant_id, installation_id, received_at desc);
create index np_sync_envelopes_correlation_idx
    on public.np_sync_envelopes (tenant_id, installation_id, correlation_id);
create index np_sync_envelopes_kind_state_idx
    on public.np_sync_envelopes (tenant_id, kind, state, received_at desc);
create index np_sync_envelopes_payload_retention_idx
    on public.np_sync_envelopes (payload_retention_until, id)
    where payload_redacted_at is null;
create index np_sync_envelopes_purge_idx
    on public.np_sync_envelopes (purge_after, id);
create index np_sync_acks_persisted_idx
    on public.np_sync_acks (tenant_id, installation_id, persisted_at desc);
create index np_heartbeats_latest_idx
    on public.np_heartbeats (tenant_id, installation_id, reported_at desc);
create index np_heartbeats_retention_idx on public.np_heartbeats (retention_until);
create index np_health_snapshots_latest_idx
    on public.np_health_snapshots (tenant_id, installation_id, reported_at desc);
create index np_health_snapshots_retention_idx on public.np_health_snapshots (retention_until);
create index np_installation_versions_latest_idx
    on public.np_installation_versions (tenant_id, installation_id, reported_at desc);
create index np_observability_events_lookup_idx
    on public.np_observability_events (tenant_id, installation_id, occurred_at desc);
create index np_observability_events_correlation_idx
    on public.np_observability_events (tenant_id, installation_id, correlation_id, occurred_at desc);
create index np_observability_events_fingerprint_idx
    on public.np_observability_events (tenant_id, fingerprint, occurred_at desc)
    where fingerprint is not null;
create index np_observability_events_retention_idx on public.np_observability_events (retention_until);
create index np_diagnostic_events_lookup_idx
    on public.np_diagnostic_events (tenant_id, installation_id, check_name, occurred_at desc);
create index np_diagnostic_events_correlation_idx
    on public.np_diagnostic_events (tenant_id, installation_id, correlation_id, occurred_at desc);
create index np_diagnostic_events_retention_idx on public.np_diagnostic_events (retention_until);
create index np_fingerprints_last_seen_idx
    on public.np_fingerprints (tenant_id, last_seen_at desc);
create index np_risks_status_idx
    on public.np_risks (tenant_id, status, severity, detected_at desc);
create index np_risks_correlation_idx
    on public.np_risks (tenant_id, installation_id, correlation_id);
create index np_incidents_status_idx
    on public.np_incidents (tenant_id, status, severity, opened_at desc);
create index np_incidents_correlation_idx
    on public.np_incidents (tenant_id, installation_id, correlation_id);
create index np_support_tickets_status_idx
    on public.np_support_tickets (tenant_id, status, opened_at desc);
create index np_support_tickets_correlation_idx
    on public.np_support_tickets (tenant_id, installation_id, correlation_id);
create index np_support_ticket_events_ticket_idx
    on public.np_support_ticket_events (tenant_id, ticket_id, occurred_at);
create index np_admin_recovery_requests_status_idx
    on public.np_admin_recovery_requests (tenant_id, installation_id, status, requested_at desc);
create index np_admin_recovery_requests_correlation_idx
    on public.np_admin_recovery_requests (tenant_id, installation_id, correlation_id);
create index np_admin_reset_authorizations_active_idx
    on public.np_admin_reset_authorizations (tenant_id, installation_id, expires_at)
    where consumed_at is null and revoked_at is null;
create unique index np_admin_reset_authorizations_one_active_request_idx
    on public.np_admin_reset_authorizations (recovery_request_id)
    where consumed_at is null and revoked_at is null;
create index np_request_nonces_expiry_idx on public.np_request_nonces (expires_at);
create index np_platform_user_tenants_tenant_idx
    on public.np_platform_user_tenants (tenant_id, user_id);
create index np_platform_audit_events_occurred_idx
    on public.np_platform_audit_events (occurred_at desc);

create or replace function public.np_parse_iso_timestamptz(value text)
returns timestamptz
language plpgsql
stable
security invoker
set search_path = pg_catalog
as $$
begin
    if value is null
       or length(value) > 40
       or value !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$'
    then
        return null;
    end if;
    return value::timestamptz;
exception
    when invalid_datetime_format or datetime_field_overflow then
        return null;
end;
$$;

create or replace function public.np_current_tenant_id()
returns uuid
language plpgsql
stable
security invoker
set search_path = pg_catalog
as $$
declare
    claims jsonb;
begin
    claims := nullif(current_setting('request.jwt.claims', true), '')::jsonb;
    return nullif(claims->>'tenant_id', '')::uuid;
exception
    when invalid_text_representation then
        return null;
end;
$$;

create or replace function public.np_admin_provision_installation(
    p_tenant_key text,
    p_display_name text,
    p_kind text,
    p_tenant_status text,
    p_installation_key text,
    p_label text,
    p_environment text,
    p_channel text,
    p_key_id text,
    p_secret_digest_hex text
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    v_tenant public.np_tenants%rowtype;
    v_installation public.np_installations%rowtype;
    v_credential public.np_installation_credentials%rowtype;
    v_kind public.np_tenant_kind;
    v_status public.np_tenant_status;
    v_environment public.np_environment;
    v_channel public.np_environment;
    v_digest bytea;
begin
    if not public.np_identifier_is_valid(p_tenant_key, 80)
       or not public.np_identifier_is_valid(p_installation_key, 120)
       or not public.np_identifier_is_valid(p_key_id, 100)
       or length(coalesce(p_display_name, '')) not between 1 and 160
       or length(coalesce(p_label, '')) not between 1 and 160
       or p_kind not in ('internal', 'customer', 'test', 'demo')
       or p_tenant_status not in ('active', 'inactive', 'suspended')
       or p_environment not in ('prod', 'qa', 'dev', 'local')
       or p_channel not in ('prod', 'qa', 'dev', 'local')
       or p_secret_digest_hex is null
       or p_secret_digest_hex !~ '^[0-9a-f]{64}$'
    then
        return jsonb_build_object('ok', false, 'code', 'invalid_payload');
    end if;

    v_kind := p_kind::public.np_tenant_kind;
    v_status := p_tenant_status::public.np_tenant_status;
    v_environment := p_environment::public.np_environment;
    v_channel := p_channel::public.np_environment;
    v_digest := decode(p_secret_digest_hex, 'hex');

    if (v_environment = 'prod' and (v_kind in ('test', 'demo') or v_channel <> 'prod'))
       or (v_environment = 'qa' and (v_kind not in ('test', 'demo') or v_channel <> 'qa'))
    then
        return jsonb_build_object('ok', false, 'code', 'environment_mismatch');
    end if;

    perform pg_advisory_xact_lock(
        hashtextextended('np-provision:' || p_installation_key, 0)
    );

    select * into v_tenant
      from public.np_tenants
     where tenant_key = p_tenant_key
     for update;
    if found and v_tenant.kind <> v_kind then
        return jsonb_build_object('ok', false, 'code', 'tenant_conflict');
    end if;

    select * into v_installation
      from public.np_installations
     where installation_key = p_installation_key
     for update;
    if found and (v_tenant.id is null or v_installation.tenant_id <> v_tenant.id) then
        return jsonb_build_object('ok', false, 'code', 'installation_conflict');
    end if;

    if v_installation.id is not null then
        select * into v_credential
          from public.np_installation_credentials
         where installation_id = v_installation.id
           and key_id = p_key_id
         for update;
        if found and (
            v_credential.kind <> 'hmac_sha256'
            or v_credential.secret_digest <> v_digest
            or v_credential.revoked_at is not null
            or (v_credential.expires_at is not null and v_credential.expires_at <= clock_timestamp())
        ) then
            return jsonb_build_object('ok', false, 'code', 'credential_conflict');
        end if;
    end if;

    if v_tenant.id is null then
        insert into public.np_tenants (tenant_key, display_name, kind, status)
        values (p_tenant_key, p_display_name, v_kind, v_status)
        returning * into v_tenant;
    else
        update public.np_tenants
           set display_name = p_display_name,
               status = v_status
         where id = v_tenant.id
        returning * into v_tenant;
    end if;

    if v_installation.id is null then
        insert into public.np_installations (
            tenant_id, installation_key, label, environment, channel, status
        ) values (
            v_tenant.id, p_installation_key, p_label, v_environment, v_channel, 'active'
        ) returning * into v_installation;
    else
        update public.np_installations
           set label = p_label,
               environment = v_environment,
               channel = v_channel,
               status = 'active'
         where id = v_installation.id
        returning * into v_installation;
    end if;

    if v_credential.id is null then
        insert into public.np_installation_credentials (
            tenant_id, installation_id, key_id, kind, secret_digest
        ) values (
            v_tenant.id, v_installation.id, p_key_id, 'hmac_sha256', v_digest
        ) returning * into v_credential;
    end if;

    insert into public.np_platform_audit_events (
        action, target_type, target_id, correlation_id, metadata
    ) values (
        'installation.provisioned', 'installation', p_installation_key,
        extensions.gen_random_uuid()::text,
        jsonb_build_object('tenant_key', p_tenant_key, 'key_id', p_key_id)
    );

    return jsonb_build_object(
        'ok', true,
        'tenant_id', v_tenant.id,
        'tenant_key', v_tenant.tenant_key,
        'installation_id', v_installation.id,
        'installation_key', v_installation.installation_key,
        'key_id', v_credential.key_id
    );
end;
$$;

create or replace function public.np_admin_rotate_installation_credential(
    p_installation_id text,
    p_key_id text,
    p_secret_digest_hex text
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    v_installation public.np_installations%rowtype;
    v_credential public.np_installation_credentials%rowtype;
    v_digest bytea;
    v_now timestamptz := clock_timestamp();
begin
    if not public.np_identifier_is_valid(p_installation_id, 120)
       or not public.np_identifier_is_valid(p_key_id, 100)
       or p_secret_digest_hex is null
       or p_secret_digest_hex !~ '^[0-9a-f]{64}$'
    then
        return jsonb_build_object('ok', false, 'code', 'invalid_payload');
    end if;
    v_digest := decode(p_secret_digest_hex, 'hex');
    select installation.* into v_installation
      from public.np_installations as installation
      join public.np_tenants as tenant on tenant.id = installation.tenant_id
     where (installation.installation_key = p_installation_id or installation.id::text = p_installation_id)
       and installation.status = 'active'
       and tenant.status = 'active';
    if not found then
        return jsonb_build_object('ok', false, 'code', 'installation_invalid');
    end if;
    perform pg_advisory_xact_lock(hashtextextended('np-sync:' || v_installation.id::text, 0));
    select * into v_installation
      from public.np_installations
     where id = v_installation.id
       and status = 'active'
       and environment = 'prod'
       and channel = 'prod'
      for update;
    if not found then
        return jsonb_build_object('ok', false, 'code', 'installation_invalid');
    end if;

    select * into v_credential
      from public.np_installation_credentials
     where installation_id = v_installation.id and key_id = p_key_id
     for update;
    if found then
        if v_credential.kind = 'hmac_sha256'
           and v_credential.secret_digest = v_digest
           and v_credential.revoked_at is null
           and (v_credential.expires_at is null or v_credential.expires_at > v_now)
        then
            return jsonb_build_object(
                'ok', true, 'installation_id', v_installation.id,
                'installation_key', v_installation.installation_key,
                'key_id', p_key_id, 'duplicate', true
            );
        end if;
        return jsonb_build_object('ok', false, 'code', 'credential_conflict');
    end if;

    update public.np_installation_credentials
       set revoked_at = v_now
     where installation_id = v_installation.id
       and revoked_at is null
       and valid_from <= v_now
       and (expires_at is null or expires_at > v_now);

    insert into public.np_installation_credentials (
        tenant_id, installation_id, key_id, kind, secret_digest, valid_from
    ) values (
        v_installation.tenant_id, v_installation.id, p_key_id,
        'hmac_sha256', v_digest, v_now
    ) returning * into v_credential;

    insert into public.np_platform_audit_events (
        action, target_type, target_id, correlation_id, metadata
    ) values (
        'credential.rotated', 'installation', v_installation.installation_key,
        extensions.gen_random_uuid()::text, jsonb_build_object('key_id', p_key_id)
    );

    return jsonb_build_object(
        'ok', true, 'installation_id', v_installation.id,
        'installation_key', v_installation.installation_key,
        'key_id', v_credential.key_id, 'duplicate', false
    );
end;
$$;

create or replace function public.np_admin_revoke_installation_credential(
    p_installation_id text,
    p_key_id text
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    v_installation public.np_installations%rowtype;
    v_credential public.np_installation_credentials%rowtype;
    v_was_revoked boolean;
begin
    if not public.np_identifier_is_valid(p_installation_id, 120)
       or not public.np_identifier_is_valid(p_key_id, 100)
    then
        return jsonb_build_object('ok', false, 'code', 'invalid_payload');
    end if;
    select * into v_installation
      from public.np_installations
     where installation_key = p_installation_id or id::text = p_installation_id;
    if not found then
        return jsonb_build_object('ok', false, 'code', 'installation_invalid');
    end if;
    perform pg_advisory_xact_lock(hashtextextended('np-sync:' || v_installation.id::text, 0));
    select * into v_installation
      from public.np_installations
     where id = v_installation.id
     for update;
    select * into v_credential
      from public.np_installation_credentials
     where installation_id = v_installation.id and key_id = p_key_id
     for update;
    if not found then
        return jsonb_build_object('ok', false, 'code', 'credential_invalid');
    end if;
    v_was_revoked := v_credential.revoked_at is not null;
    if not v_was_revoked then
        update public.np_installation_credentials
           set revoked_at = clock_timestamp()
         where id = v_credential.id;
        insert into public.np_platform_audit_events (
            action, target_type, target_id, correlation_id, metadata
        ) values (
            'credential.revoked', 'installation', v_installation.installation_key,
            extensions.gen_random_uuid()::text, jsonb_build_object('key_id', p_key_id)
        );
    end if;
    return jsonb_build_object(
        'ok', true, 'installation_id', v_installation.id,
        'installation_key', v_installation.installation_key,
        'key_id', p_key_id, 'duplicate', v_was_revoked
    );
end;
$$;

create or replace function public.erp_ingest_sync_batch(
    p_installation_id text,
    p_secret_hash text,
    p_nonce_hash text,
    p_sent_at timestamptz,
    p_envelopes jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    v_installation public.np_installations%rowtype;
    v_tenant public.np_tenants%rowtype;
    v_existing public.np_sync_envelopes%rowtype;
    v_item jsonb;
    v_payload jsonb;
    v_event_type text;
    v_aggregate_type text;
    v_aggregate_id text;
    v_idempotency_key text;
    v_correlation_id text;
    v_schema_version integer;
    v_occurred_at timestamptz;
    v_count integer;
    v_seen_keys text[] := array[]::text[];
    v_envelope_id uuid;
    v_nonce_id uuid;
    v_duplicate boolean;
    v_acks jsonb := '[]'::jsonb;
    v_health text;
    v_level text;
    v_now timestamptz := clock_timestamp();
begin
    if not public.np_identifier_is_valid(p_installation_id, 120)
       or p_secret_hash is null or p_secret_hash !~ '^[0-9a-f]{64}$'
       or p_nonce_hash is null or p_nonce_hash !~ '^[0-9a-f]{64}$'
       or p_sent_at is null
       or abs(extract(epoch from (v_now - p_sent_at))) > 300
       or p_envelopes is null
       or jsonb_typeof(p_envelopes) <> 'array'
       or jsonb_array_length(p_envelopes) not between 1 and 25
       or octet_length(p_envelopes::text) > 262144
    then
        return jsonb_build_object('ok', false, 'code', 'invalid_payload');
    end if;

    select installation.* into v_installation
      from public.np_installations as installation
      join public.np_tenants as tenant on tenant.id = installation.tenant_id
     where installation.installation_key = p_installation_id
       and installation.status = 'active'
       and installation.environment = 'prod'
       and installation.channel = 'prod'
       and tenant.status = 'active'
       and tenant.kind in ('internal', 'customer');
    if not found then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;

    perform pg_advisory_xact_lock(
        hashtextextended('np-sync:' || v_installation.id::text, 0)
    );
    select * into v_installation
      from public.np_installations
     where id = v_installation.id
       and status = 'active'
       and environment = 'prod'
       and channel = 'prod'
     for update;
    select * into v_tenant
      from public.np_tenants
     where id = v_installation.tenant_id
       and status = 'active'
       and kind in ('internal', 'customer');
    if v_installation.id is null or v_tenant.id is null then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;
    if not exists (
        select 1
          from public.np_installation_credentials as credential
         where credential.tenant_id = v_tenant.id
           and credential.installation_id = v_installation.id
           and credential.kind = 'hmac_sha256'
           and credential.secret_digest = decode(p_secret_hash, 'hex')
           and credential.valid_from <= v_now
           and (credential.expires_at is null or credential.expires_at > v_now)
           and credential.revoked_at is null
    ) then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;

    select count(*)::integer into v_count
      from public.np_request_nonces
     where tenant_id = v_tenant.id
       and installation_id = v_installation.id
        and nonce like 'sync:%'
       and accepted_at >= v_now - interval '1 minute';
    if v_count >= 120 then
        return jsonb_build_object('ok', false, 'code', 'rate_limited');
    end if;
    if exists (
        select 1 from public.np_request_nonces
         where tenant_id = v_tenant.id
           and installation_id = v_installation.id
            and nonce = 'sync:' || p_nonce_hash
    ) then
        return jsonb_build_object('ok', false, 'code', 'replay');
    end if;

    for v_item in
        select element
          from jsonb_array_elements(p_envelopes) as batch(element)
    loop
        if jsonb_typeof(v_item) <> 'object'
           or exists (
               select 1 from jsonb_object_keys(v_item) as keys(key_name)
                where key_name not in (
                    'event_type', 'aggregate_type', 'aggregate_id', 'payload',
                    'schema_version', 'idempotency_key'
                )
           )
           or jsonb_typeof(v_item->'payload') <> 'object'
            or v_item->'schema_version' <> '1'::jsonb
        then
            return jsonb_build_object('ok', false, 'code', 'invalid_payload');
        end if;

        v_event_type := v_item->>'event_type';
        v_aggregate_type := v_item->>'aggregate_type';
        v_aggregate_id := v_item->>'aggregate_id';
        v_idempotency_key := v_item->>'idempotency_key';
        v_schema_version := (v_item->>'schema_version')::integer;
        v_payload := v_item->'payload';
        v_correlation_id := coalesce(nullif(v_payload->>'correlation_id', ''), v_idempotency_key);

        if v_event_type not in (
                'support_ticket', 'health', 'heartbeat',
                'risk', 'incident', 'diagnostic_event'
           )
           or not public.np_identifier_is_valid(v_aggregate_type, 128)
           or not public.np_identifier_is_valid(v_aggregate_id, 128)
           or not public.np_identifier_is_valid(v_idempotency_key, 200)
           or not public.np_identifier_is_valid(v_correlation_id, 160)
           or not public.np_jsonb_is_sanitized(v_payload)
           or octet_length(v_payload::text) > 65536
           or v_payload->>'tenant_id' is distinct from v_tenant.tenant_key
           or v_payload->>'installation_id' is distinct from v_installation.installation_key
           or (
                v_payload ? 'environment'
                and lower(v_payload->>'environment') not in ('prod', 'production')
           )
           or (
                v_payload ? 'channel'
                and lower(v_payload->>'channel') not in ('prod', 'production')
           )
           or v_idempotency_key = any (v_seen_keys)
        then
            return jsonb_build_object('ok', false, 'code', 'invalid_payload');
        end if;
        v_seen_keys := array_append(v_seen_keys, v_idempotency_key);

        select * into v_existing
          from public.np_sync_envelopes
         where tenant_id = v_tenant.id
           and installation_id = v_installation.id
           and idempotency_key = v_idempotency_key;
        if found and (
            v_existing.kind::text <> v_event_type
            or v_existing.aggregate_type <> v_aggregate_type
            or v_existing.aggregate_id <> v_aggregate_id
            or v_existing.schema_version <> v_schema_version
            or v_existing.payload_hash <> extensions.digest(v_payload::text, 'sha256')
        ) then
            return jsonb_build_object('ok', false, 'code', 'idempotency_conflict');
        end if;

        v_occurred_at := public.np_parse_iso_timestamptz(coalesce(
            v_payload->>'timestamp', v_payload->>'occurred_at',
            v_payload->>'captured_at', v_payload->>'created_at',
            v_payload->>'last_seen', v_payload->>'first_seen_at'
        ));
        if v_occurred_at is null then
            if coalesce(
                v_payload->>'timestamp', v_payload->>'occurred_at',
                v_payload->>'captured_at', v_payload->>'created_at',
                v_payload->>'last_seen', v_payload->>'first_seen_at'
            ) is not null then
                return jsonb_build_object('ok', false, 'code', 'invalid_payload');
            end if;
            v_occurred_at := v_now;
        end if;

        if v_event_type = 'heartbeat' then
            v_health := lower(coalesce(v_payload->>'health', 'unknown'));
            if length(coalesce(v_payload->>'version', '')) not between 1 and 64
               or not public.np_identifier_is_valid(v_payload->>'build', 80)
               or coalesce(v_payload->>'commit', 'unknown') !~ '^(unknown|[0-9a-f]{7,40})$'
               or lower(coalesce(v_payload->>'environment', '')) not in (
                    'production', 'prod', 'qa', 'dev', 'local'
               )
               or lower(coalesce(v_payload->>'channel', v_payload->>'environment', ''))
                    not in ('production', 'prod', 'qa', 'dev', 'local')
               or (case lower(v_payload->>'environment')
                       when 'production' then 'prod'
                       else lower(v_payload->>'environment')
                   end) <> v_installation.environment::text
               or (case lower(coalesce(v_payload->>'channel', v_payload->>'environment'))
                       when 'production' then 'prod'
                       else lower(coalesce(v_payload->>'channel', v_payload->>'environment'))
                   end) <> v_installation.channel::text
               or v_health not in (
                    'healthy', 'normal', 'warning', 'high', 'critical',
                    'degraded', 'unavailable', 'offline', 'unknown'
               )
            then
                return jsonb_build_object('ok', false, 'code', 'invalid_payload');
            end if;
        elsif v_event_type = 'health' then
            v_health := lower(coalesce(v_payload->>'status', 'unknown'));
            if v_health not in (
                    'healthy', 'normal', 'warning', 'high', 'critical',
                    'degraded', 'unavailable', 'offline', 'unknown'
               )
               or coalesce(v_payload->>'risk_score', '0') !~ '^[0-9]{1,3}$'
               or (coalesce(v_payload->>'risk_score', '0'))::integer not between 0 and 100
               or coalesce(v_payload->>'recent_errors', '0') !~ '^[0-9]{1,9}$'
               or coalesce(v_payload->>'retry_count', '0') !~ '^[0-9]{1,9}$'
               or exists (
                    select 1
                      from jsonb_each_text(v_payload) as state_fields(field_name, field_value)
                     where field_name in (
                         'database_state', 'migration_state', 'outbox_state',
                         'sync_state', 'nexa_state'
                     )
                       and lower(field_value) not in (
                         'healthy', 'normal', 'warning', 'high', 'critical',
                         'degraded', 'unavailable', 'offline', 'unknown'
                       )
               )
               or exists (
                    select 1
                      from jsonb_each_text(v_payload) as count_fields(field_name, field_value)
                     where field_name in ('doctor_pass', 'doctor_warn', 'doctor_fail')
                       and field_value !~ '^[0-9]{1,9}$'
               )
               or (
                    v_payload ? 'latency_ms'
                    and v_payload->'latency_ms' <> 'null'::jsonb
                    and (
                        coalesce(v_payload->>'latency_ms', '') !~ '^[0-9]{1,7}$'
                        or (v_payload->>'latency_ms')::integer > 3600000
                    )
               )
               or (
                    v_payload ? 'fingerprints'
                    and (
                        jsonb_typeof(v_payload->'fingerprints') <> 'array'
                        or octet_length((v_payload->'fingerprints')::text) > 16384
                        or exists (
                            select 1
                              from jsonb_array_elements_text(v_payload->'fingerprints') as fingerprint(value)
                             where not public.np_identifier_is_valid(fingerprint.value, 160)
                        )
                    )
               )
               or (
                    v_payload ? 'active_risk_fingerprints'
                    and (
                        jsonb_typeof(v_payload->'active_risk_fingerprints') <> 'array'
                        or octet_length((v_payload->'active_risk_fingerprints')::text) > 16384
                        or exists (
                            select 1
                              from jsonb_array_elements_text(v_payload->'active_risk_fingerprints') as fingerprint(value)
                             where not public.np_identifier_is_valid(fingerprint.value, 160)
                        )
                    )
               )
            then
                return jsonb_build_object('ok', false, 'code', 'invalid_payload');
            end if;
        elsif v_event_type = 'diagnostic_event' then
            v_level := upper(coalesce(v_payload->>'level', v_payload->>'severity', ''));
            if coalesce(v_payload->>'event_id', v_aggregate_id) <> v_aggregate_id
               or not public.np_identifier_is_valid(v_aggregate_id, 128)
               or (
                    v_payload ? 'fingerprint'
                    and v_payload->'fingerprint' <> 'null'::jsonb
                    and not public.np_identifier_is_valid(v_payload->>'fingerprint', 160)
               )
               or v_level not in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
               or not public.np_identifier_is_valid(coalesce(v_payload->>'module', 'erp'), 100)
               or not public.np_identifier_is_valid(coalesce(v_payload->>'component', 'application'), 100)
               or not public.np_identifier_is_valid(v_payload->>'environment', 40)
               or (case lower(v_payload->>'environment')
                       when 'production' then 'prod'
                       else lower(v_payload->>'environment')
                   end) <> v_installation.environment::text
               or not public.np_identifier_is_valid(coalesce(v_payload->>'operation', 'unknown'), 100)
               or not public.np_identifier_is_valid(coalesce(v_payload->>'status', 'observed'), 100)
               or (v_payload ? 'error_code' and v_payload->'error_code' <> 'null'::jsonb
                   and not public.np_identifier_is_valid(v_payload->>'error_code', 100))
               or coalesce(v_payload->>'retry_count', '0') !~ '^[0-9]{1,4}$'
               or (coalesce(v_payload->>'retry_count', '0'))::integer > 1000
               or (v_payload ? 'duration_ms' and v_payload->'duration_ms' <> 'null'::jsonb
                   and (coalesce(v_payload->>'duration_ms', '') !~ '^[0-9]{1,7}$'
                        or (v_payload->>'duration_ms')::integer > 3600000))
            then
                return jsonb_build_object('ok', false, 'code', 'invalid_payload');
            end if;
        elsif v_event_type = 'risk' then
            v_level := lower(coalesce(v_payload->>'level', 'normal'));
            if not public.np_identifier_is_valid(v_payload->>'fingerprint', 160)
               or not public.np_identifier_is_valid(coalesce(v_payload->>'module', 'erp'), 100)
               or v_level not in ('normal', 'low', 'warning', 'medium', 'high', 'critical')
               or coalesce(v_payload->>'score', '0') !~ '^[0-9]{1,3}$'
               or (coalesce(v_payload->>'score', '0'))::integer not between 0 and 100
               or lower(coalesce(v_payload->>'confidence', 'medium')) not in ('low', 'medium', 'high')
               or (v_payload ? 'evidence' and jsonb_typeof(v_payload->'evidence') <> 'array')
               or length(coalesce(v_payload->>'probable_cause', '')) > 500
            then
                return jsonb_build_object('ok', false, 'code', 'invalid_payload');
            end if;
        elsif v_event_type = 'support_ticket' then
            if not public.np_identifier_is_valid(v_payload->>'protocol', 100)
               or not public.np_identifier_is_valid(v_payload->>'created_by', 160)
               or not public.np_identifier_is_valid(v_payload->>'category', 100)
               or length(coalesce(v_payload->>'subject', '')) not between 1 and 240
               or length(coalesce(v_payload->>'description', '')) not between 1 and 4000
               or lower(coalesce(v_payload->>'priority', 'normal')) not in ('normal', 'high')
            then
                return jsonb_build_object('ok', false, 'code', 'invalid_payload');
            end if;
        elsif v_event_type = 'incident' then
            v_level := lower(coalesce(v_payload->>'severity', 'warning'));
            if length(coalesce(v_payload->>'title', '')) not between 1 and 240
               or v_level not in ('normal', 'low', 'warning', 'medium', 'high', 'critical')
               or (v_payload ? 'fingerprint' and v_payload->'fingerprint' <> 'null'::jsonb
                   and not public.np_identifier_is_valid(v_payload->>'fingerprint', 160))
            then
                return jsonb_build_object('ok', false, 'code', 'invalid_payload');
            end if;
        end if;
    end loop;

    begin
        insert into public.np_request_nonces (
            tenant_id, installation_id, nonce, request_timestamp, accepted_at, expires_at
        ) values (
            v_tenant.id, v_installation.id, 'sync:' || p_nonce_hash, p_sent_at,
            v_now, v_now + interval '10 minutes'
        ) on conflict (tenant_id, installation_id, nonce) do nothing
        returning id into v_nonce_id;
        if v_nonce_id is null then
            return jsonb_build_object('ok', false, 'code', 'replay');
        end if;

        for v_item in
            select element
              from jsonb_array_elements(p_envelopes) as batch(element)
        loop
            v_event_type := v_item->>'event_type';
            v_aggregate_type := v_item->>'aggregate_type';
            v_aggregate_id := v_item->>'aggregate_id';
            v_idempotency_key := v_item->>'idempotency_key';
            v_schema_version := (v_item->>'schema_version')::integer;
            v_payload := v_item->'payload';
            v_correlation_id := coalesce(nullif(v_payload->>'correlation_id', ''), v_idempotency_key);
            v_occurred_at := coalesce(public.np_parse_iso_timestamptz(coalesce(
                v_payload->>'timestamp', v_payload->>'occurred_at',
                v_payload->>'captured_at', v_payload->>'created_at',
                v_payload->>'last_seen', v_payload->>'first_seen_at'
            )), v_now);

            select * into v_existing
              from public.np_sync_envelopes
             where tenant_id = v_tenant.id
               and installation_id = v_installation.id
               and idempotency_key = v_idempotency_key
             for update;
            v_duplicate := found;
            if v_duplicate then
                v_envelope_id := v_existing.id;
            else
                insert into public.np_sync_envelopes (
                    tenant_id, installation_id, idempotency_key, correlation_id,
                    aggregate_type, aggregate_id, kind, schema_version,
                    occurred_at, payload, payload_hash,
                    payload_retention_until, purge_after
                ) values (
                    v_tenant.id, v_installation.id, v_idempotency_key, v_correlation_id,
                    v_aggregate_type, v_aggregate_id, v_event_type::public.np_sync_kind,
                    v_schema_version, v_occurred_at, v_payload,
                    extensions.digest(v_payload::text, 'sha256'),
                    v_now + case v_event_type
                        when 'heartbeat' then interval '30 days'
                        when 'health' then interval '90 days'
                        else interval '180 days'
                    end,
                    v_now + interval '730 days'
                ) returning id into v_envelope_id;

                if v_event_type = 'heartbeat' then
                    v_health := lower(coalesce(v_payload->>'health', 'unknown'));
                    insert into public.np_heartbeats (
                        id, tenant_id, installation_id, idempotency_key, correlation_id,
                        reported_at, app_version, build_id, build_commit, sync_status
                    ) values (
                        v_envelope_id, v_tenant.id, v_installation.id,
                        v_idempotency_key, v_correlation_id, v_occurred_at,
                        v_payload->>'version', v_payload->>'build',
                        coalesce(nullif(v_payload->>'commit', ''), 'unknown'), 'synced'
                    );
                    update public.np_installations
                       set app_version = v_payload->>'version',
                            build_id = v_payload->>'build',
                            build_commit = coalesce(nullif(v_payload->>'commit', ''), 'unknown'),
                           health_state = v_health::public.np_health_state,
                           last_seen_at = v_occurred_at
                      where id = v_installation.id;
                    insert into public.np_installation_versions (
                        tenant_id, installation_id, idempotency_key, correlation_id,
                        version, build_id, build_commit, environment, channel, reported_at
                    ) values (
                        v_tenant.id, v_installation.id, v_idempotency_key,
                        v_correlation_id, v_payload->>'version', v_payload->>'build',
                        coalesce(nullif(v_payload->>'commit', ''), 'unknown'),
                        (case lower(v_payload->>'environment')
                            when 'production' then 'prod'
                            else lower(v_payload->>'environment')
                         end)::public.np_environment,
                        (case lower(coalesce(v_payload->>'channel', v_payload->>'environment'))
                            when 'production' then 'prod'
                            else lower(coalesce(v_payload->>'channel', v_payload->>'environment'))
                         end)::public.np_environment,
                        v_occurred_at
                    );
                elsif v_event_type = 'health' then
                    v_health := lower(coalesce(v_payload->>'status', 'unknown'));
                    insert into public.np_health_snapshots (
                        id, tenant_id, installation_id, idempotency_key, correlation_id,
                        reported_at, app_state, database_state, migration_state,
                        outbox_state, sync_state, nexa_state,
                        risk_score, recent_errors, retry_count,
                        latency_ms, fingerprints, doctor_pass, doctor_warn,
                        doctor_fail, metadata
                    ) values (
                        v_envelope_id, v_tenant.id, v_installation.id,
                        v_idempotency_key, v_correlation_id, v_occurred_at,
                        v_health::public.np_health_state,
                        lower(coalesce(v_payload->>'database_state', 'unknown'))::public.np_health_state,
                        lower(coalesce(v_payload->>'migration_state', 'unknown'))::public.np_health_state,
                        lower(coalesce(v_payload->>'outbox_state', 'unknown'))::public.np_health_state,
                        lower(coalesce(v_payload->>'sync_state', 'unknown'))::public.np_health_state,
                        lower(coalesce(v_payload->>'nexa_state', 'unknown'))::public.np_health_state,
                        coalesce((v_payload->>'risk_score')::integer, 0),
                        coalesce((v_payload->>'recent_errors')::integer, 0),
                        coalesce((v_payload->>'retry_count')::integer, 0),
                        nullif(v_payload->>'latency_ms', '')::integer,
                        coalesce(v_payload->'fingerprints', '[]'::jsonb),
                        coalesce((v_payload->>'doctor_pass')::integer, 0),
                        coalesce((v_payload->>'doctor_warn')::integer, 0),
                        coalesce((v_payload->>'doctor_fail')::integer, 0),
                        coalesce(v_payload->'details', '{}'::jsonb)
                    );
                    update public.np_installations
                       set health_state = v_health::public.np_health_state,
                           last_seen_at = v_occurred_at
                     where id = v_installation.id;
                    if v_payload ? 'active_risk_fingerprints' then
                        update public.np_risks as risk
                           set status = 'mitigated',
                               resolved_at = coalesce(risk.resolved_at, v_occurred_at),
                               updated_at = v_now
                         where risk.tenant_id = v_tenant.id
                           and risk.installation_id = v_installation.id
                           and risk.status in ('open', 'acknowledged', 'monitoring')
                           and not exists (
                               select 1
                                 from jsonb_array_elements_text(
                                     v_payload->'active_risk_fingerprints'
                                 ) as active(value)
                                where active.value = risk.fingerprint
                           );
                    end if;
                elsif v_event_type = 'diagnostic_event' then
                    v_level := upper(coalesce(v_payload->>'level', v_payload->>'severity'));
                    v_health := lower(coalesce(v_payload->>'status', 'unknown'));
                    if v_health not in (
                        'healthy', 'normal', 'warning', 'high', 'critical',
                        'degraded', 'unavailable', 'offline', 'unknown'
                    ) then
                        v_health := 'unknown';
                    end if;
                    insert into public.np_diagnostic_events (
                        id, tenant_id, installation_id, idempotency_key, correlation_id,
                        source_event_id, check_name, result, level, module, component,
                        environment, operation, event_status, duration_ms, error_code,
                        fingerprint, retry_count, schema_version, app_version, build,
                        occurred_at, details
                    ) values (
                        v_envelope_id, v_tenant.id, v_installation.id,
                        v_idempotency_key, v_correlation_id, v_aggregate_id,
                        coalesce(v_payload->>'event_type', 'diagnostic.event'),
                        v_health::public.np_health_state,
                        lower(v_level)::public.np_event_level,
                        coalesce(v_payload->>'module', 'erp'),
                        coalesce(v_payload->>'component', 'application'),
                        coalesce(v_payload->>'environment', 'local'),
                        coalesce(v_payload->>'operation', 'unknown'),
                        coalesce(v_payload->>'status', 'observed'),
                        nullif(v_payload->>'duration_ms', '')::integer,
                        nullif(v_payload->>'error_code', ''),
                        v_payload->>'fingerprint',
                        coalesce((v_payload->>'retry_count')::integer, 0),
                        coalesce((v_payload->>'schema_version')::integer, 1),
                        nullif(v_payload->>'app_version', ''),
                        nullif(v_payload->>'build', ''),
                        v_occurred_at,
                        coalesce(v_payload->'metadata', v_payload->'details', '{}'::jsonb)
                    );
                    insert into public.np_observability_events (
                        id, tenant_id, installation_id, idempotency_key,
                        correlation_id, event_name, level, component,
                        occurred_at, fingerprint, metadata
                    ) values (
                        v_envelope_id, v_tenant.id, v_installation.id,
                        v_idempotency_key, v_correlation_id,
                        coalesce(v_payload->>'event_type', 'diagnostic.event'),
                        lower(v_level)::public.np_event_level,
                        coalesce(v_payload->>'component', 'application'),
                        v_occurred_at, nullif(v_payload->>'fingerprint', ''),
                        coalesce(v_payload->'metadata', v_payload->'details', '{}'::jsonb)
                        || jsonb_build_object(
                            'environment', coalesce(v_payload->>'environment', 'local'),
                            'operation', coalesce(v_payload->>'operation', 'unknown'),
                            'status', coalesce(v_payload->>'status', 'observed'),
                            'duration_ms', v_payload->'duration_ms',
                            'error_code', v_payload->'error_code',
                            'retry_count', coalesce(v_payload->'retry_count', '0'::jsonb),
                            'schema_version', coalesce(v_payload->'schema_version', '1'::jsonb),
                            'app_version', v_payload->'app_version',
                            'build', v_payload->'build',
                            'module', coalesce(v_payload->>'module', 'erp'),
                            'user_pseudonym', v_payload->'user_pseudonym',
                            'session_id', v_payload->'session_id',
                            'request_id', v_payload->'request_id'
                        )
                    );
                    if nullif(v_payload->>'fingerprint', '') is not null then
                        insert into public.np_fingerprints (
                            tenant_id, installation_id, fingerprint, category,
                            first_seen_at, last_seen_at, occurrence_count, sample_metadata
                        ) values (
                            v_tenant.id, v_installation.id, v_payload->>'fingerprint',
                            coalesce(v_payload->>'event_type', 'diagnostic'),
                            v_occurred_at, v_occurred_at, 1,
                            coalesce(v_payload->'metadata', '{}'::jsonb)
                        )
                        on conflict (tenant_id, installation_id, fingerprint)
                        do update set
                            last_seen_at = greatest(
                                public.np_fingerprints.last_seen_at,
                                excluded.last_seen_at
                            ),
                            occurrence_count = public.np_fingerprints.occurrence_count + 1,
                            sample_metadata = excluded.sample_metadata,
                            updated_at = v_now;
                    end if;
                elsif v_event_type = 'risk' then
                    v_level := lower(coalesce(v_payload->>'level', 'normal'));
                    insert into public.np_risks (
                        id, tenant_id, installation_id, idempotency_key, correlation_id,
                        source_risk_id, fingerprint, severity, title, module, score,
                        confidence, evidence, probable_cause, detected_at, metadata
                    ) values (
                        v_envelope_id, v_tenant.id, v_installation.id,
                        v_idempotency_key, v_correlation_id, v_aggregate_id,
                        v_payload->>'fingerprint', v_level::public.np_risk_severity,
                        left(coalesce(v_payload->>'title',
                            coalesce(v_payload->>'module', 'erp') || ': ' || (v_payload->>'fingerprint')), 240),
                        coalesce(v_payload->>'module', 'erp'),
                        coalesce((v_payload->>'score')::integer, 0),
                        lower(coalesce(v_payload->>'confidence', 'medium')),
                        coalesce(v_payload->'evidence', '[]'::jsonb),
                        nullif(v_payload->>'probable_cause', ''),
                        v_occurred_at,
                        jsonb_build_object('source', 'erp')
                    )
                    on conflict (tenant_id, installation_id, source_risk_id)
                    do update set
                        idempotency_key = excluded.idempotency_key,
                        correlation_id = excluded.correlation_id,
                        fingerprint = excluded.fingerprint,
                        severity = excluded.severity,
                        status = case
                            when public.np_risks.status in (
                                'mitigated', 'resolved', 'dismissed', 'closed'
                            ) then 'open'::public.np_risk_status
                            else public.np_risks.status
                        end,
                        title = excluded.title,
                        module = excluded.module,
                        score = excluded.score,
                        confidence = excluded.confidence,
                        evidence = excluded.evidence,
                        probable_cause = excluded.probable_cause,
                        detected_at = least(public.np_risks.detected_at, excluded.detected_at),
                        resolved_at = null,
                        metadata = excluded.metadata,
                        updated_at = v_now;
                    insert into public.np_fingerprints (
                        tenant_id, installation_id, fingerprint, category,
                        first_seen_at, last_seen_at, occurrence_count, sample_metadata
                    ) values (
                        v_tenant.id, v_installation.id, v_payload->>'fingerprint',
                        'risk', v_occurred_at, v_occurred_at, 1,
                        jsonb_build_object(
                            'module', coalesce(v_payload->>'module', 'erp'),
                            'severity', v_level
                        )
                    )
                    on conflict (tenant_id, installation_id, fingerprint)
                    do update set
                        last_seen_at = greatest(
                            public.np_fingerprints.last_seen_at,
                            excluded.last_seen_at
                        ),
                        occurrence_count = public.np_fingerprints.occurrence_count + 1,
                        sample_metadata = excluded.sample_metadata,
                        updated_at = v_now;
                elsif v_event_type = 'incident' then
                    v_level := lower(coalesce(v_payload->>'severity', 'warning'));
                    if v_level = 'low' then v_level := 'normal'; end if;
                    if v_level = 'medium' then v_level := 'warning'; end if;
                    insert into public.np_incidents (
                        id, tenant_id, installation_id, idempotency_key, correlation_id,
                        source_incident_id, fingerprint, severity, title, opened_at, summary
                    ) values (
                        v_envelope_id, v_tenant.id, v_installation.id,
                        v_idempotency_key, v_correlation_id, v_aggregate_id,
                        nullif(v_payload->>'fingerprint', ''),
                        v_level::public.np_risk_severity,
                        v_payload->>'title', v_occurred_at,
                        coalesce(v_payload->'summary', jsonb_build_object('source', 'erp'))
                    );
                elsif v_event_type = 'support_ticket' then
                    insert into public.np_support_tickets (
                        id, tenant_id, installation_id, idempotency_key, correlation_id,
                        source_ticket_id, category, priority, subject,
                        sanitized_description, opened_at, metadata
                    ) values (
                        v_envelope_id, v_tenant.id, v_installation.id,
                        v_idempotency_key, v_correlation_id, v_aggregate_id,
                        v_payload->>'category', lower(coalesce(v_payload->>'priority', 'normal')),
                        v_payload->>'subject', v_payload->>'description', v_occurred_at,
                        v_payload - array[
                            'tenant_id', 'installation_id', 'subject', 'description',
                            'category', 'priority', 'correlation_id'
                        ]
                    );
                    if v_payload->>'category' = 'admin_access_recovery' then
                        insert into public.np_admin_recovery_requests (
                            tenant_id, installation_id, support_ticket_id,
                            idempotency_key, correlation_id, requester_fingerprint,
                            requested_at, expires_at
                        ) values (
                            v_tenant.id, v_installation.id, v_envelope_id,
                            v_idempotency_key, v_correlation_id, v_payload->>'created_by',
                            v_occurred_at, greatest(v_occurred_at, v_now) + interval '30 days'
                        );
                    end if;
                end if;

                insert into public.np_sync_acks (
                    tenant_id, installation_id, envelope_id, ack_key, response_metadata
                ) values (
                    v_tenant.id, v_installation.id, v_envelope_id, v_idempotency_key,
                    jsonb_build_object('schema_version', v_schema_version)
                );
            end if;

            if not exists (
                select 1 from public.np_sync_acks
                 where envelope_id = v_envelope_id and ack_key = v_idempotency_key
            ) then
                raise exception 'persisted envelope has no durable acknowledgement'
                    using errcode = '23514';
            end if;
            v_acks := v_acks || jsonb_build_array(jsonb_build_object(
                'idempotency_key', v_idempotency_key,
                'remote_id', v_envelope_id::text,
                'schema_version', v_schema_version,
                'duplicate', v_duplicate
            ));
        end loop;

        delete from public.np_request_nonces
         where expires_at < v_now - interval '1 day';
    exception
        when check_violation
          or not_null_violation
          or foreign_key_violation
          or invalid_text_representation
          or datetime_field_overflow
          or numeric_value_out_of_range
          or string_data_right_truncation
        then
            return jsonb_build_object('ok', false, 'code', 'invalid_payload');
    end;

    return jsonb_build_object('ok', true, 'acks', v_acks);
end;
$$;

create or replace function public.np_admin_authorize_reset(
    p_ticket_id text,
    p_authorized_by text,
    p_lifetime_minutes integer default 15,
    p_tenant_id text default null,
    p_installation_id text default null
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    v_ticket public.np_support_tickets%rowtype;
    v_request public.np_admin_recovery_requests%rowtype;
    v_actor public.np_platform_users%rowtype;
    v_authorization public.np_admin_reset_authorizations%rowtype;
    v_ticket_is_uuid boolean;
    v_now timestamptz := clock_timestamp();
    v_expires_at timestamptz;
begin
    v_ticket_is_uuid := p_ticket_id ~
        '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$';
    if not public.np_identifier_is_valid(p_ticket_id, 128)
       or p_authorized_by is null
       or p_authorized_by !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       or p_lifetime_minutes is null
       or p_lifetime_minutes not between 5 and 60
       or (
            not v_ticket_is_uuid
            and (
                not public.np_identifier_is_valid(p_tenant_id, 80)
                or not public.np_identifier_is_valid(p_installation_id, 120)
            )
       )
       or ((p_tenant_id is null) <> (p_installation_id is null))
    then
        return jsonb_build_object('ok', false, 'code', 'invalid_payload');
    end if;

    perform pg_advisory_xact_lock(hashtextextended(
        'np-reset:' || coalesce(p_tenant_id || ':' || p_installation_id || ':', '') || p_ticket_id,
        0
    ));
    select * into v_actor
      from public.np_platform_users
     where id = p_authorized_by::uuid
       and role = 'platform_admin'
       and active
     for update;
    if not found then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;

    if v_ticket_is_uuid then
        select ticket.* into v_ticket
          from public.np_support_tickets as ticket
         where ticket.id = p_ticket_id::uuid
           and (
                p_tenant_id is null
                or exists (
                    select 1
                      from public.np_tenants as tenant
                      join public.np_installations as installation
                        on installation.tenant_id = tenant.id
                     where tenant.id = ticket.tenant_id
                       and installation.id = ticket.installation_id
                       and tenant.tenant_key = p_tenant_id
                       and installation.installation_key = p_installation_id
                )
           )
         for update;
    else
        select ticket.* into v_ticket
          from public.np_support_tickets as ticket
          join public.np_tenants as tenant on tenant.id = ticket.tenant_id
          join public.np_installations as installation
            on installation.id = ticket.installation_id
           and installation.tenant_id = ticket.tenant_id
         where ticket.source_ticket_id = p_ticket_id
           and tenant.tenant_key = p_tenant_id
           and installation.installation_key = p_installation_id
         for update of ticket;
    end if;
    if not found
       or v_ticket.category <> 'admin_access_recovery'
       or v_ticket.status not in ('open', 'in_progress', 'waiting_customer')
    then
        return jsonb_build_object('ok', false, 'code', 'recovery_request_invalid');
    end if;

    select * into v_request
      from public.np_admin_recovery_requests
     where support_ticket_id = v_ticket.id
     for update;
    if not found or v_request.status not in ('pending', 'authorized') then
        return jsonb_build_object('ok', false, 'code', 'recovery_request_invalid');
    end if;
    if v_request.expires_at <= v_now then
        update public.np_admin_recovery_requests
           set status = 'expired', decided_at = coalesce(decided_at, v_now)
         where id = v_request.id;
        return jsonb_build_object('ok', false, 'code', 'recovery_request_expired');
    end if;

    v_expires_at := v_now + make_interval(mins => p_lifetime_minutes);
    update public.np_admin_reset_authorizations
       set revoked_at = v_now
     where recovery_request_id = v_request.id
       and consumed_at is null
       and revoked_at is null;

    insert into public.np_admin_reset_authorizations (
        tenant_id, installation_id, recovery_request_id, support_ticket_id,
        authorization_digest, authorized_by, authorized_at, expires_at
    ) values (
        v_ticket.tenant_id, v_ticket.installation_id, v_request.id, v_ticket.id,
        extensions.digest(extensions.gen_random_bytes(32), 'sha256'),
        v_actor.id, v_now, v_expires_at
    ) returning * into v_authorization;

    update public.np_admin_recovery_requests
       set status = 'authorized', decided_at = v_now
     where id = v_request.id;
    insert into public.np_support_ticket_events (
        tenant_id, installation_id, ticket_id, idempotency_key,
        event_type, occurred_at, metadata
    ) values (
        v_ticket.tenant_id, v_ticket.installation_id, v_ticket.id,
        'admin-reset-authorized:' || v_authorization.id::text,
        'admin.recovery.authorized', v_now,
        jsonb_build_object(
            'authorized_by', v_actor.id,
            'expires_at', v_expires_at
        )
    );
    insert into public.np_platform_audit_events (
        actor_user_id, action, target_type, target_id, correlation_id, metadata
    ) values (
        v_actor.id, 'admin.recovery.authorized', 'support_ticket',
        v_ticket.source_ticket_id, extensions.gen_random_uuid()::text,
        jsonb_build_object('expires_at', v_expires_at)
    );

    return jsonb_build_object(
        'ok', true,
        'authorization_id', v_authorization.id,
        'ticket_id', v_ticket.source_ticket_id,
        'tenant_id', v_ticket.tenant_id,
        'installation_id', v_ticket.installation_id,
        'status', 'active',
        'authorized_by', v_actor.id,
        'created_at', v_authorization.authorized_at,
        'expires_at', v_authorization.expires_at
    );
end;
$$;

create or replace function public.np_admin_consume_reset(
    p_installation_id text,
    p_requester_ref text
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    v_installation public.np_installations%rowtype;
    v_ticket public.np_support_tickets%rowtype;
    v_request public.np_admin_recovery_requests%rowtype;
    v_authorization public.np_admin_reset_authorizations%rowtype;
    v_now timestamptz := clock_timestamp();
begin
    if not public.np_identifier_is_valid(p_installation_id, 120)
       or not public.np_identifier_is_valid(p_requester_ref, 160)
    then
        return jsonb_build_object('ok', false, 'code', 'invalid_payload');
    end if;
    select * into v_installation
      from public.np_installations
     where installation_key = p_installation_id;
    if not found then
        return jsonb_build_object('ok', false, 'code', 'installation_invalid');
    end if;
    perform pg_advisory_xact_lock(hashtextextended('np-reset-install:' || v_installation.id::text, 0));

    update public.np_admin_reset_authorizations
       set revoked_at = v_now
     where installation_id = v_installation.id
       and consumed_at is null
       and revoked_at is null
       and expires_at <= v_now;

    select authorization_row.*
      into v_authorization
      from public.np_admin_reset_authorizations as authorization_row
      join public.np_admin_recovery_requests as recovery_row
        on recovery_row.id = authorization_row.recovery_request_id
      join public.np_support_tickets as ticket_row
        on ticket_row.id = authorization_row.support_ticket_id
     where authorization_row.installation_id = v_installation.id
       and authorization_row.consumed_at is null
       and authorization_row.revoked_at is null
       and authorization_row.expires_at > v_now
       and recovery_row.status = 'authorized'
       and recovery_row.requester_fingerprint = p_requester_ref
       and recovery_row.tenant_id = authorization_row.tenant_id
       and recovery_row.installation_id = authorization_row.installation_id
       and ticket_row.tenant_id = authorization_row.tenant_id
       and ticket_row.installation_id = authorization_row.installation_id
       and ticket_row.category = 'admin_access_recovery'
       and ticket_row.status in ('open', 'in_progress', 'waiting_customer')
     order by authorization_row.authorized_at desc, authorization_row.id desc
     limit 1
     for update of authorization_row;
    if not found then
        return jsonb_build_object('ok', false, 'code', 'authorization_unavailable');
    end if;

    select * into v_request
      from public.np_admin_recovery_requests
     where id = v_authorization.recovery_request_id
       and status = 'authorized'
       and requester_fingerprint = p_requester_ref
       and tenant_id = v_authorization.tenant_id
       and installation_id = v_authorization.installation_id
     for update;
    if not found then
        return jsonb_build_object('ok', false, 'code', 'authorization_unavailable');
    end if;

    select * into v_ticket
      from public.np_support_tickets
     where id = v_authorization.support_ticket_id
       and tenant_id = v_authorization.tenant_id
       and installation_id = v_authorization.installation_id
       and category = 'admin_access_recovery'
       and status in ('open', 'in_progress', 'waiting_customer')
     for update;
    if not found then
        return jsonb_build_object('ok', false, 'code', 'authorization_unavailable');
    end if;

    update public.np_admin_reset_authorizations
       set consumed_at = v_now, attempt_count = 0, lockout_until = null
     where id = v_authorization.id
       and consumed_at is null
       and revoked_at is null;
    if not found then
        return jsonb_build_object('ok', false, 'code', 'authorization_unavailable');
    end if;
    update public.np_admin_recovery_requests
       set status = 'consumed', consumed_at = v_now
     where id = v_request.id;
    insert into public.np_support_ticket_events (
        tenant_id, installation_id, ticket_id, idempotency_key,
        event_type, occurred_at, metadata
    ) values (
        v_ticket.tenant_id, v_ticket.installation_id, v_ticket.id,
        'admin-reset-consumed:' || v_authorization.id::text,
        'admin.recovery.remote_consumed', v_now,
        jsonb_build_object('grant_id', v_authorization.id)
    );

    return jsonb_build_object(
        'ok', true,
        'authorization_id', v_authorization.id,
        'ticket_id', v_ticket.source_ticket_id,
        'tenant_id', v_ticket.tenant_id,
        'installation_id', v_ticket.installation_id,
        'status', 'consumed',
        'authorized_by', v_authorization.authorized_by,
        'created_at', v_authorization.authorized_at,
        'expires_at', v_authorization.expires_at,
        'used_at', v_now
    );
end;
$$;

create or replace function public.np_authorize_nexa_request(
    p_tenant_id text,
    p_installation_id text,
    p_secret_hash text,
    p_nonce_hash text,
    p_sent_at timestamptz
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    v_installation public.np_installations%rowtype;
    v_tenant public.np_tenants%rowtype;
    v_nonce_id uuid;
    v_now timestamptz := clock_timestamp();
    v_recent integer;
begin
    if not public.np_identifier_is_valid(p_tenant_id, 80)
       or not public.np_identifier_is_valid(p_installation_id, 120)
       or p_secret_hash is null or p_secret_hash !~ '^[0-9a-f]{64}$'
       or p_nonce_hash is null or p_nonce_hash !~ '^[0-9a-f]{64}$'
       or p_sent_at is null
       or abs(extract(epoch from (v_now - p_sent_at))) > 60
    then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;

    select installation.*
      into v_installation
      from public.np_installations as installation
      join public.np_tenants as tenant on tenant.id = installation.tenant_id
     where tenant.tenant_key = p_tenant_id
       and installation.installation_key = p_installation_id
       and tenant.status = 'active'
       and installation.status = 'active'
       and tenant.kind in ('internal', 'customer')
       and installation.environment = 'prod'
       and installation.channel = 'prod';
    if not found then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;
    select tenant.* into v_tenant
      from public.np_tenants as tenant
     where tenant.id = v_installation.tenant_id
       and tenant.tenant_key = p_tenant_id
       and tenant.status = 'active'
       and tenant.kind in ('internal', 'customer');
    if not found then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;

    perform pg_advisory_xact_lock(
        hashtextextended('np-nexa:' || v_installation.id::text, 0)
    );
    select * into v_installation
      from public.np_installations
     where id = v_installation.id
       and status = 'active'
       and environment = 'prod'
       and channel = 'prod'
     for update;
    select * into v_tenant
      from public.np_tenants
     where id = v_installation.tenant_id
       and tenant_key = p_tenant_id
       and status = 'active'
       and kind in ('internal', 'customer');
    if v_installation.id is null or v_tenant.id is null then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;
    if not exists (
        select 1
          from public.np_installation_credentials as credential
         where credential.tenant_id = v_tenant.id
           and credential.installation_id = v_installation.id
           and credential.kind = 'hmac_sha256'
           and credential.secret_digest = decode(p_secret_hash, 'hex')
           and credential.valid_from <= v_now
           and (credential.expires_at is null or credential.expires_at > v_now)
           and credential.revoked_at is null
    ) then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;

    select count(*)::integer into v_recent
      from public.np_request_nonces
     where tenant_id = v_tenant.id
       and installation_id = v_installation.id
       and nonce like 'nexa:%'
       and accepted_at >= v_now - interval '1 minute';
    if v_recent >= 60 then
        return jsonb_build_object('ok', false, 'code', 'rate_limited');
    end if;

    insert into public.np_request_nonces (
        tenant_id, installation_id, nonce, request_timestamp, accepted_at, expires_at
    ) values (
        v_tenant.id, v_installation.id, 'nexa:' || p_nonce_hash,
        p_sent_at, v_now, v_now + interval '10 minutes'
    ) on conflict (tenant_id, installation_id, nonce) do nothing
    returning id into v_nonce_id;
    if v_nonce_id is null then
        return jsonb_build_object('ok', false, 'code', 'replay');
    end if;

    delete from public.np_request_nonces where expires_at < v_now - interval '1 day';
    return jsonb_build_object(
        'ok', true,
        'tenant_id', v_tenant.tenant_key,
        'installation_id', v_installation.installation_key,
        'tenant_uuid', v_tenant.id,
        'installation_uuid', v_installation.id
    );
end;
$$;

create or replace function public.np_admin_get_reset_authorization(
    p_ticket_id text,
    p_tenant_id text default null,
    p_installation_id text default null
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    v_authorization public.np_admin_reset_authorizations%rowtype;
    v_ticket public.np_support_tickets%rowtype;
    v_status text;
    v_ticket_is_uuid boolean;
begin
    v_ticket_is_uuid := p_ticket_id ~
        '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$';
    if not public.np_identifier_is_valid(p_ticket_id, 128)
       or (
            not v_ticket_is_uuid
            and (
                not public.np_identifier_is_valid(p_tenant_id, 80)
                or not public.np_identifier_is_valid(p_installation_id, 120)
            )
       )
       or ((p_tenant_id is null) <> (p_installation_id is null))
    then
        return '{}'::jsonb;
    end if;
    if v_ticket_is_uuid then
        select ticket.* into v_ticket
          from public.np_support_tickets as ticket
         where ticket.id = p_ticket_id::uuid
           and (
                p_tenant_id is null
                or exists (
                    select 1
                      from public.np_tenants as tenant
                      join public.np_installations as installation
                        on installation.tenant_id = tenant.id
                     where tenant.id = ticket.tenant_id
                       and installation.id = ticket.installation_id
                       and tenant.tenant_key = p_tenant_id
                       and installation.installation_key = p_installation_id
                )
           );
    else
        select ticket.* into v_ticket
          from public.np_support_tickets as ticket
          join public.np_tenants as tenant on tenant.id = ticket.tenant_id
          join public.np_installations as installation
            on installation.id = ticket.installation_id
           and installation.tenant_id = ticket.tenant_id
         where ticket.source_ticket_id = p_ticket_id
           and tenant.tenant_key = p_tenant_id
           and installation.installation_key = p_installation_id;
    end if;
    if not found then
        return '{}'::jsonb;
    end if;
    select authorization_row.* into v_authorization
      from public.np_admin_reset_authorizations as authorization_row
     where authorization_row.support_ticket_id = v_ticket.id
     order by authorization_row.authorized_at desc, authorization_row.id desc
     limit 1;
    if not found then
        return '{}'::jsonb;
    end if;
    v_status := case
        when v_authorization.consumed_at is not null then 'consumed'
        when v_authorization.revoked_at is not null then 'revoked'
        when v_authorization.expires_at <= clock_timestamp() then 'expired'
        else 'active'
    end;
    return jsonb_build_object(
        'id', v_authorization.id,
        'ticket_id', v_ticket.source_ticket_id,
        'tenant_id', v_authorization.tenant_id,
        'installation_id', v_authorization.installation_id,
        'status', v_status,
        'authorized_by', v_authorization.authorized_by,
        'created_at', v_authorization.authorized_at,
        'expires_at', v_authorization.expires_at,
        'used_at', v_authorization.consumed_at
    );
end;
$$;

create or replace function public.np_erp_access_reset_authorization(
    p_tenant_id text,
    p_installation_id text,
    p_secret_hash text,
    p_nonce_hash text,
    p_sent_at timestamptz,
    p_action text,
    p_requester_ref text,
    p_authorization_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    v_installation public.np_installations%rowtype;
    v_tenant public.np_tenants%rowtype;
    v_ticket public.np_support_tickets%rowtype;
    v_request public.np_admin_recovery_requests%rowtype;
    v_authorization public.np_admin_reset_authorizations%rowtype;
    v_now timestamptz := clock_timestamp();
    v_nonce_id uuid;
    v_recent integer;
    v_request_matches integer;
begin
    if not public.np_identifier_is_valid(p_tenant_id, 80)
       or not public.np_identifier_is_valid(p_installation_id, 120)
       or p_secret_hash is null or p_secret_hash !~ '^[0-9a-f]{64}$'
       or p_nonce_hash is null or p_nonce_hash !~ '^[0-9a-f]{64}$'
       or p_sent_at is null
       or abs(extract(epoch from (v_now - p_sent_at))) > 60
       or p_action not in ('status', 'consume')
       or not public.np_identifier_is_valid(p_requester_ref, 160)
       or (p_action = 'status' and p_authorization_id is not null)
       or (p_action = 'consume' and p_authorization_id is null)
    then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;

    select installation.* into v_installation
      from public.np_installations as installation
      join public.np_tenants as tenant on tenant.id = installation.tenant_id
     where tenant.tenant_key = p_tenant_id
       and installation.installation_key = p_installation_id
       and tenant.status = 'active'
       and tenant.kind in ('internal', 'customer')
       and installation.status = 'active'
       and installation.environment = 'prod'
       and installation.channel = 'prod';
    if not found then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;

    perform pg_advisory_xact_lock(
        hashtextextended('np-recovery-api:' || v_installation.id::text, 0)
    );
    select * into v_installation
      from public.np_installations
     where id = v_installation.id
       and status = 'active'
       and environment = 'prod'
       and channel = 'prod'
     for update;
    select * into v_tenant
      from public.np_tenants
     where id = v_installation.tenant_id
       and tenant_key = p_tenant_id
       and status = 'active'
       and kind in ('internal', 'customer');
    if v_installation.id is null or v_tenant.id is null then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;
    if not exists (
        select 1
          from public.np_installation_credentials as credential
         where credential.tenant_id = v_tenant.id
           and credential.installation_id = v_installation.id
           and credential.kind = 'hmac_sha256'
           and credential.secret_digest = decode(p_secret_hash, 'hex')
           and credential.valid_from <= v_now
           and (credential.expires_at is null or credential.expires_at > v_now)
           and credential.revoked_at is null
    ) then
        return jsonb_build_object('ok', false, 'code', 'unauthorized');
    end if;

    select count(*)::integer into v_recent
      from public.np_request_nonces
     where tenant_id = v_tenant.id
       and installation_id = v_installation.id
       and nonce like 'recovery:%'
       and accepted_at >= v_now - interval '1 minute';
    if v_recent >= 30 then
        return jsonb_build_object('ok', false, 'code', 'rate_limited');
    end if;
    insert into public.np_request_nonces (
        tenant_id, installation_id, nonce, request_timestamp, accepted_at, expires_at
    ) values (
        v_tenant.id, v_installation.id, 'recovery:' || p_nonce_hash,
        p_sent_at, v_now, v_now + interval '10 minutes'
    ) on conflict (tenant_id, installation_id, nonce) do nothing
    returning id into v_nonce_id;
    if v_nonce_id is null then
        return jsonb_build_object('ok', false, 'code', 'replay');
    end if;

    select count(*)::integer into v_request_matches
      from public.np_admin_recovery_requests as request
      join public.np_support_tickets as ticket
        on ticket.id = request.support_ticket_id
       and ticket.tenant_id = request.tenant_id
       and ticket.installation_id = request.installation_id
     where request.tenant_id = v_tenant.id
       and request.installation_id = v_installation.id
       and request.requester_fingerprint = p_requester_ref
       and request.status in ('pending', 'authorized')
       and request.expires_at > v_now
       and ticket.category = 'admin_access_recovery'
       and ticket.status in ('open', 'in_progress', 'waiting_customer');
    if v_request_matches > 1 then
        return jsonb_build_object('ok', false, 'code', 'ambiguous_context');
    end if;
    if v_request_matches = 0 then
        if p_action = 'status' then
            return jsonb_build_object('ok', true, 'action', 'status', 'authorization', null);
        end if;
        return jsonb_build_object('ok', false, 'code', 'authorization_unavailable');
    end if;
    select request.* into v_request
      from public.np_admin_recovery_requests as request
      join public.np_support_tickets as ticket
        on ticket.id = request.support_ticket_id
       and ticket.tenant_id = request.tenant_id
       and ticket.installation_id = request.installation_id
     where request.tenant_id = v_tenant.id
       and request.installation_id = v_installation.id
       and request.requester_fingerprint = p_requester_ref
       and request.status in ('pending', 'authorized')
       and request.expires_at > v_now
       and ticket.category = 'admin_access_recovery'
       and ticket.status in ('open', 'in_progress', 'waiting_customer')
     for update of request;
    select ticket.* into v_ticket
      from public.np_support_tickets as ticket
     where ticket.id = v_request.support_ticket_id
       and ticket.tenant_id = v_tenant.id
       and ticket.installation_id = v_installation.id
     for update;

    if p_action = 'consume' then
        update public.np_admin_reset_authorizations
           set revoked_at = v_now
         where recovery_request_id = v_request.id
           and consumed_at is null
           and revoked_at is null
           and expires_at <= v_now;
        select authz.* into v_authorization
          from public.np_admin_reset_authorizations as authz
         where authz.recovery_request_id = v_request.id
           and authz.id = p_authorization_id
           and authz.support_ticket_id = v_ticket.id
           and authz.tenant_id = v_tenant.id
           and authz.installation_id = v_installation.id
           and authz.consumed_at is null
           and authz.revoked_at is null
           and authz.expires_at > v_now
           and v_request.status = 'authorized'
           and v_ticket.status in ('open', 'in_progress', 'waiting_customer')
         order by authz.authorized_at desc, authz.id desc
         limit 1
         for update;
        if not found then
            return jsonb_build_object('ok', false, 'code', 'authorization_unavailable');
        end if;
        update public.np_admin_reset_authorizations
           set consumed_at = v_now, attempt_count = 0, lockout_until = null
         where id = v_authorization.id
           and consumed_at is null
           and revoked_at is null;
        if not found then
            return jsonb_build_object('ok', false, 'code', 'authorization_unavailable');
        end if;
        update public.np_admin_recovery_requests
           set status = 'consumed', consumed_at = v_now
         where id = v_request.id and status = 'authorized';
        insert into public.np_support_ticket_events (
            tenant_id, installation_id, ticket_id, idempotency_key,
            event_type, occurred_at, metadata
        ) values (
            v_tenant.id, v_installation.id, v_ticket.id,
            'admin-reset-consumed:' || v_authorization.id::text,
            'admin.recovery.remote_consumed', v_now,
            jsonb_build_object('grant_id', v_authorization.id)
        );
        v_authorization.consumed_at := v_now;
    else
        select authz.* into v_authorization
          from public.np_admin_reset_authorizations as authz
         where authz.recovery_request_id = v_request.id
           and authz.support_ticket_id = v_ticket.id
           and authz.tenant_id = v_tenant.id
           and authz.installation_id = v_installation.id
           and authz.consumed_at is null
           and authz.revoked_at is null
           and authz.expires_at > v_now
           and v_request.status = 'authorized'
         order by authz.authorized_at desc, authz.id desc
         limit 1;
        if not found then
            return jsonb_build_object('ok', true, 'action', 'status', 'authorization', null);
        end if;
    end if;

    return jsonb_build_object(
        'ok', true,
        'action', p_action,
        'authorization', jsonb_build_object(
            'authorization_id', v_authorization.id,
            'ticket_id', v_ticket.source_ticket_id,
            'tenant_id', v_tenant.tenant_key,
            'installation_id', v_installation.installation_key,
            'authorized_by', v_authorization.authorized_by,
            'created_at', v_authorization.authorized_at,
            'expires_at', v_authorization.expires_at,
            'consumed_at', v_authorization.consumed_at
        )
    );
end;
$$;

create or replace function public.np_admin_prune_expired_telemetry(
    p_limit integer default 1000
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
    v_heartbeats integer := 0;
    v_health integer := 0;
    v_observability integer := 0;
    v_diagnostics integer := 0;
    v_nonces integer := 0;
    v_payloads_redacted integer := 0;
    v_acks_purged integer := 0;
    v_envelopes_purged integer := 0;
begin
    if p_limit is null or p_limit not between 1 and 10000 then
        return jsonb_build_object('ok', false, 'code', 'invalid_payload');
    end if;

    with targets as (
        select envelope.id
          from public.np_sync_envelopes as envelope
         where envelope.payload_redacted_at is null
           and envelope.payload_retention_until < clock_timestamp()
           and not exists (
               select 1 from public.np_incidents as incident
                where incident.tenant_id = envelope.tenant_id
                  and incident.installation_id = envelope.installation_id
                  and incident.correlation_id = envelope.correlation_id
                  and incident.status not in ('resolved', 'closed')
           )
         order by envelope.payload_retention_until, envelope.id
         limit p_limit
    )
    update public.np_sync_envelopes as envelope
       set payload = '{}'::jsonb,
           payload_redacted_at = clock_timestamp()
      from targets
     where envelope.id = targets.id;
    get diagnostics v_payloads_redacted = row_count;

    with targets as (
        select id from public.np_heartbeats
         where retention_until < clock_timestamp()
         order by retention_until, id limit p_limit
    )
    delete from public.np_heartbeats as heartbeat
     using targets where heartbeat.id = targets.id;
    get diagnostics v_heartbeats = row_count;

    with targets as (
        select id from public.np_health_snapshots
         where retention_until < clock_timestamp()
         order by retention_until, id limit p_limit
    )
    delete from public.np_health_snapshots as health
     using targets where health.id = targets.id;
    get diagnostics v_health = row_count;

    with targets as (
        select event.id from public.np_observability_events as event
         where event.retention_until < clock_timestamp()
           and not exists (
               select 1 from public.np_incidents as incident
                where incident.tenant_id = event.tenant_id
                  and incident.installation_id = event.installation_id
                  and incident.correlation_id = event.correlation_id
                  and incident.status not in ('resolved', 'closed')
           )
         order by event.retention_until, event.id limit p_limit
    )
    delete from public.np_observability_events as event
     using targets where event.id = targets.id;
    get diagnostics v_observability = row_count;

    with targets as (
        select event.id from public.np_diagnostic_events as event
         where event.retention_until < clock_timestamp()
           and not exists (
               select 1 from public.np_incidents as incident
                where incident.tenant_id = event.tenant_id
                  and incident.installation_id = event.installation_id
                  and incident.correlation_id = event.correlation_id
                  and incident.status not in ('resolved', 'closed')
           )
         order by event.retention_until, event.id limit p_limit
    )
    delete from public.np_diagnostic_events as event
     using targets where event.id = targets.id;
    get diagnostics v_diagnostics = row_count;

    -- Keep a redacted idempotency tombstone for at least 30 days. After the
    -- two-year envelope horizon, prune ACK first and envelope second so the
    -- tables cannot grow forever. Active incidents retain their evidence.
    with targets as (
        select envelope.id
          from public.np_sync_envelopes as envelope
         where envelope.payload_redacted_at is not null
           and envelope.payload_redacted_at < clock_timestamp() - interval '30 days'
           and envelope.purge_after < clock_timestamp()
           and not exists (
               select 1 from public.np_incidents as incident
                where incident.tenant_id = envelope.tenant_id
                  and incident.installation_id = envelope.installation_id
                  and incident.correlation_id = envelope.correlation_id
                  and incident.status not in ('resolved', 'closed')
           )
         order by envelope.purge_after, envelope.id
         limit p_limit
    )
    delete from public.np_sync_acks as ack
     using targets where ack.envelope_id = targets.id;
    get diagnostics v_acks_purged = row_count;

    with targets as (
        select envelope.id
          from public.np_sync_envelopes as envelope
         where envelope.payload_redacted_at is not null
           and envelope.payload_redacted_at < clock_timestamp() - interval '30 days'
           and envelope.purge_after < clock_timestamp()
           and not exists (
               select 1 from public.np_sync_acks as ack
                where ack.envelope_id = envelope.id
           )
           and not exists (
               select 1 from public.np_incidents as incident
                where incident.tenant_id = envelope.tenant_id
                  and incident.installation_id = envelope.installation_id
                  and incident.correlation_id = envelope.correlation_id
                  and incident.status not in ('resolved', 'closed')
           )
         order by envelope.purge_after, envelope.id
         limit p_limit
    )
    delete from public.np_sync_envelopes as envelope
     using targets where envelope.id = targets.id;
    get diagnostics v_envelopes_purged = row_count;

    with targets as (
        select id from public.np_request_nonces
         where expires_at < clock_timestamp()
         order by expires_at, id limit p_limit
    )
    delete from public.np_request_nonces as nonce
     using targets where nonce.id = targets.id;
    get diagnostics v_nonces = row_count;

    return jsonb_build_object(
        'ok', true,
        'heartbeats', v_heartbeats,
        'health_snapshots', v_health,
        'observability_events', v_observability,
        'diagnostic_events', v_diagnostics,
        'nonces', v_nonces,
        'payloads_redacted', v_payloads_redacted,
        'acks_purged', v_acks_purged,
        'envelopes_purged', v_envelopes_purged
    );
end;
$$;

-- The public schema is API-visible in Supabase.  Every ERP table therefore
-- starts fail-closed: RLS is enabled and forced, and no client role receives a
-- policy or table privilege.  Only trusted backend code using service_role may
-- reach the tables or the narrowly defined RPCs below.
do $$
declare
    relation_name text;
    protected_relations constant text[] := array[
        'np_tenants',
        'np_platform_users',
        'np_platform_user_tenants',
        'np_platform_audit_events',
        'np_installations',
        'np_installation_credentials',
        'np_sync_envelopes',
        'np_sync_acks',
        'np_heartbeats',
        'np_health_snapshots',
        'np_installation_versions',
        'np_observability_events',
        'np_diagnostic_events',
        'np_fingerprints',
        'np_risks',
        'np_incidents',
        'np_support_tickets',
        'np_support_ticket_events',
        'np_admin_recovery_requests',
        'np_admin_reset_authorizations',
        'np_request_nonces'
    ];
begin
    foreach relation_name in array protected_relations loop
        execute format('alter table public.%I enable row level security', relation_name);
        execute format('alter table public.%I force row level security', relation_name);
        execute format(
            'revoke all privileges on table public.%I from public, anon, authenticated',
            relation_name
        );
        execute format(
            'grant select, insert, update, delete on table public.%I to service_role',
            relation_name
        );
    end loop;
end;
$$;

-- Authenticated Data API access is currently withheld by table grants. These
-- explicit policies remain as defense in depth and make any future selective
-- grant tenant-scoped by default. Missing or malformed tenant context returns
-- no rows and cannot create or change rows.
create policy np_tenant_scope
    on public.np_tenants
    for all
    to authenticated
    using (id = (select public.np_current_tenant_id()))
    with check (id = (select public.np_current_tenant_id()));

do $$
declare
    relation_name text;
    tenant_relations constant text[] := array[
        'np_installations',
        'np_installation_credentials',
        'np_sync_envelopes',
        'np_sync_acks',
        'np_heartbeats',
        'np_health_snapshots',
        'np_installation_versions',
        'np_observability_events',
        'np_diagnostic_events',
        'np_fingerprints',
        'np_risks',
        'np_incidents',
        'np_support_tickets',
        'np_support_ticket_events',
        'np_admin_recovery_requests',
        'np_admin_reset_authorizations',
        'np_request_nonces'
    ];
begin
    foreach relation_name in array tenant_relations loop
        execute format(
            'create policy np_tenant_scope on public.%I for all to authenticated '
            'using (tenant_id = (select public.np_current_tenant_id())) '
            'with check (tenant_id = (select public.np_current_tenant_id()))',
            relation_name
        );
    end loop;
end;
$$;

create policy np_platform_backend_only
    on public.np_platform_users
    for all
    to authenticated
    using (false)
    with check (false);
create policy np_platform_backend_only
    on public.np_platform_user_tenants
    for all
    to authenticated
    using (false)
    with check (false);
create policy np_platform_backend_only
    on public.np_platform_audit_events
    for all
    to authenticated
    using (false)
    with check (false);

revoke all privileges on function public.np_identifier_is_valid(text, integer)
    from public, anon, authenticated;
revoke all privileges on function public.nexa_claim_erp_nonce(text, text)
    from public, anon, authenticated, service_role;
revoke all privileges on function public.np_jsonb_is_sanitized(jsonb)
    from public, anon, authenticated;
revoke all privileges on function public.np_set_updated_at()
    from public, anon, authenticated;
revoke all privileges on function public.np_enforce_environment_isolation()
    from public, anon, authenticated;
revoke all privileges on function public.np_parse_iso_timestamptz(text)
    from public, anon, authenticated;
revoke all privileges on function public.np_current_tenant_id()
    from public, anon, authenticated;
revoke all privileges on function public.np_admin_provision_installation(
    text, text, text, text, text, text, text, text, text, text
) from public, anon, authenticated;
revoke all privileges on function public.np_admin_rotate_installation_credential(
    text, text, text
) from public, anon, authenticated;
revoke all privileges on function public.np_admin_revoke_installation_credential(text, text)
    from public, anon, authenticated;
revoke all privileges on function public.erp_ingest_sync_batch(
    text, text, text, timestamptz, jsonb
) from public, anon, authenticated;
revoke all privileges on function public.np_admin_authorize_reset(
    text, text, integer, text, text
)
    from public, anon, authenticated;
revoke all privileges on function public.np_admin_consume_reset(text, text)
    from public, anon, authenticated;
revoke all privileges on function public.np_authorize_nexa_request(
    text, text, text, text, timestamptz
) from public, anon, authenticated;
revoke all privileges on function public.np_admin_get_reset_authorization(text, text, text)
    from public, anon, authenticated;
revoke all privileges on function public.np_erp_access_reset_authorization(
    text, text, text, text, timestamptz, text, text, uuid
) from public, anon, authenticated;
revoke all privileges on function public.np_admin_prune_expired_telemetry(integer)
    from public, anon, authenticated;

grant execute on function public.np_identifier_is_valid(text, integer) to service_role;
grant execute on function public.nexa_claim_erp_nonce(text, text) to service_role;
grant execute on function public.np_jsonb_is_sanitized(jsonb) to service_role;
grant execute on function public.np_set_updated_at() to service_role;
grant execute on function public.np_enforce_environment_isolation() to service_role;
grant execute on function public.np_parse_iso_timestamptz(text) to service_role;
grant execute on function public.np_current_tenant_id() to authenticated, service_role;
grant execute on function public.np_admin_provision_installation(
    text, text, text, text, text, text, text, text, text, text
) to service_role;
grant execute on function public.np_admin_rotate_installation_credential(
    text, text, text
) to service_role;
grant execute on function public.np_admin_revoke_installation_credential(text, text)
    to service_role;
grant execute on function public.erp_ingest_sync_batch(
    text, text, text, timestamptz, jsonb
) to service_role;
grant execute on function public.np_admin_authorize_reset(text, text, integer, text, text)
    to service_role;
grant execute on function public.np_admin_consume_reset(text, text)
    to service_role;
grant execute on function public.np_authorize_nexa_request(
    text, text, text, text, timestamptz
) to service_role;
grant execute on function public.np_admin_get_reset_authorization(text, text, text)
    to service_role;
grant execute on function public.np_erp_access_reset_authorization(
    text, text, text, text, timestamptz, text, text, uuid
) to service_role;
grant execute on function public.np_admin_prune_expired_telemetry(integer)
    to service_role;

alter default privileges in schema public revoke execute on functions from public;
alter default privileges in schema public revoke all privileges on tables from anon, authenticated;

comment on table public.np_installation_credentials is
    'Credential verifiers/references for trusted backend use only; never exposed to ERP frontend.';
comment on table public.np_sync_envelopes is
    'Sanitized delivery envelopes. Presence alone is not an ACK; np_sync_acks confirms persistence.';
comment on table public.np_admin_reset_authorizations is
    'One-use internal reset authorization. Digest is never returned to the end user.';
comment on table public.np_request_nonces is
    'Persistent replay protection; retained through the accepted request window and across restarts.';
comment on column public.np_support_tickets.sanitized_description is
    'Technical description sanitized by the trusted ingestion boundary before persistence.';
