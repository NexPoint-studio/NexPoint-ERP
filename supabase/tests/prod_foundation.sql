\set ON_ERROR_STOP on

begin;

create extension if not exists pgtap with schema extensions;
select extensions.plan(1);

do $$
declare
    result jsonb;
    replay_result jsonb;
    duplicate_result jsonb;
    first_remote_id text;
    tenant_a uuid;
    installation_a uuid;
    platform_admin uuid;
    heartbeat_batch jsonb;
    recovery_batch jsonb;
    authorization_projection jsonb;
    ticket_uuid uuid;
begin
    if (select count(*) from pg_tables where schemaname = 'public' and tablename like 'np_%') <> 21 then
        raise exception 'unexpected ERP cloud table count';
    end if;
    if exists (
        select 1
          from pg_class as relation
          join pg_namespace as namespace on namespace.oid = relation.relnamespace
         where namespace.nspname = 'public'
           and relation.relname like 'np_%'
           and relation.relkind = 'r'
           and (not relation.relrowsecurity or not relation.relforcerowsecurity)
    ) then
        raise exception 'RLS is not enabled and forced on every ERP cloud table';
    end if;
    if (select count(*) from pg_policies where schemaname = 'public' and tablename like 'np_%') <> 21 then
        raise exception 'every ERP cloud table must have an explicit policy';
    end if;
    if exists (
        select 1 from pg_policies
         where schemaname = 'public' and tablename like 'np_%'
           and lower(coalesce(qual, '')) in ('true', '(true)')
    ) then
        raise exception 'unrestricted RLS policy found';
    end if;
    if exists (
        select 1 from information_schema.role_table_grants
         where table_schema = 'public' and table_name like 'np_%'
           and grantee in ('anon', 'authenticated')
    ) then
        raise exception 'Data API client role received direct ERP table grants';
    end if;
    if has_function_privilege(
        'anon',
        'public.erp_ingest_sync_batch(text,text,text,timestamptz,jsonb)',
        'EXECUTE'
    ) or has_function_privilege(
        'authenticated',
        'public.erp_ingest_sync_batch(text,text,text,timestamptz,jsonb)',
        'EXECUTE'
    ) then
        raise exception 'sync ingestion RPC is exposed to a client role';
    end if;
    if has_function_privilege(
        'anon',
        'public.np_authorize_nexa_request(text,text,text,text,timestamptz)',
        'EXECUTE'
    ) or has_function_privilege(
        'authenticated',
        'public.np_authorize_nexa_request(text,text,text,text,timestamptz)',
        'EXECUTE'
    ) or has_function_privilege(
        'anon',
        'public.np_admin_get_reset_authorization(text,text,text)',
        'EXECUTE'
    ) or has_function_privilege(
        'authenticated',
        'public.np_admin_get_reset_authorization(text,text,text)',
        'EXECUTE'
    ) then
        raise exception 'trusted backend RPC is exposed to a client role';
    end if;

    result := public.np_admin_provision_installation(
        'tenant_prod_a', 'Tenant PROD A', 'internal', 'active',
        'installation_prod_a', 'Windows principal', 'prod', 'prod',
        'key_prod_a_001', repeat('a', 64)
    );
    if result->>'ok' <> 'true' then
        raise exception 'PROD provisioning failed: %', result;
    end if;
    tenant_a := (result->>'tenant_id')::uuid;
    installation_a := (result->>'installation_id')::uuid;

    result := public.np_admin_provision_installation(
        'tenant_qa_b', 'Tenant QA B', 'test', 'active',
        'installation_qa_b', 'QA isolado', 'qa', 'qa',
        'key_qa_b_001', repeat('b', 64)
    );
    if result->>'ok' <> 'true' then
        raise exception 'QA provisioning failed: %', result;
    end if;

    result := public.np_admin_provision_installation(
        'tenant_invalid_prod', 'Tenant invalido', 'test', 'active',
        'installation_invalid_prod', 'Invalida', 'prod', 'prod',
        'key_invalid_001', repeat('c', 64)
    );
    if result->>'code' <> 'environment_mismatch'
       or exists (select 1 from public.np_tenants where tenant_key = 'tenant_invalid_prod')
    then
        raise exception 'QA/PROD environment isolation failed: %', result;
    end if;

    heartbeat_batch := jsonb_build_array(jsonb_build_object(
        'event_type', 'heartbeat',
        'aggregate_type', 'installation',
        'aggregate_id', 'heartbeat_test_001',
        'payload', jsonb_build_object(
            'tenant_id', 'tenant_prod_a',
            'installation_id', 'installation_prod_a',
            'version', '1.0.0',
            'build', 'abcdef0',
            'environment', 'production',
            'health', 'healthy',
            'correlation_id', 'correlation_test_001',
            'last_seen', to_char(
                clock_timestamp() at time zone 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
            )
        ),
        'schema_version', 1,
        'idempotency_key', 'heartbeat:test:001'
    ));

    result := public.erp_ingest_sync_batch(
        'installation_prod_a', repeat('a', 64), repeat('d', 64),
        clock_timestamp(),
        jsonb_set(heartbeat_batch, '{0,payload,tenant_id}', '"tenant_qa_b"'::jsonb)
    );
    if result->>'code' <> 'invalid_payload' then
        raise exception 'cross-tenant payload was accepted: %', result;
    end if;

    result := public.erp_ingest_sync_batch(
        'installation_prod_a', repeat('f', 64), repeat('e', 64),
        clock_timestamp(), heartbeat_batch
    );
    if result->>'code' <> 'unauthorized' then
        raise exception 'wrong installation credential was accepted: %', result;
    end if;

    result := public.erp_ingest_sync_batch(
        'installation_qa_b', repeat('b', 64), repeat('f', 64),
        clock_timestamp(),
        jsonb_set(
            jsonb_set(
                jsonb_set(heartbeat_batch, '{0,payload,tenant_id}', '"tenant_qa_b"'::jsonb),
                '{0,payload,installation_id}', '"installation_qa_b"'::jsonb
            ),
            '{0,payload,environment}', '"qa"'::jsonb
        )
    );
    if result->>'code' <> 'unauthorized' then
        raise exception 'sync accepted a QA installation: %', result;
    end if;

    result := public.erp_ingest_sync_batch(
        'installation_prod_a', repeat('a', 64), repeat('9', 64),
        clock_timestamp(),
        jsonb_set(heartbeat_batch, '{0,payload,channel}', '"qa"'::jsonb)
    );
    if result->>'code' <> 'invalid_payload' then
        raise exception 'sync accepted a non-PROD payload channel: %', result;
    end if;

    insert into public.np_request_nonces (
        tenant_id, installation_id, nonce, request_timestamp, accepted_at, expires_at
    )
    select tenant_a, installation_a, 'nexa:test:' || value::text,
           clock_timestamp(), clock_timestamp(), clock_timestamp() + interval '10 minutes'
      from generate_series(1, 120) as series(value);

    result := public.erp_ingest_sync_batch(
        'installation_prod_a', repeat('a', 64), repeat('1', 64),
        clock_timestamp(), heartbeat_batch
    );
    if result->>'ok' <> 'true'
       or jsonb_array_length(result->'acks') <> 1
       or result->'acks'->0->>'duplicate' <> 'false'
    then
        raise exception 'signed batch persistence failed: %', result;
    end if;
    first_remote_id := result->'acks'->0->>'remote_id';
    if not exists (
        select 1 from public.np_request_nonces
         where tenant_id = tenant_a
           and installation_id = installation_a
           and nonce = 'sync:' || repeat('1', 64)
    ) then
        raise exception 'sync replay nonce was not namespaced';
    end if;

    update public.np_sync_envelopes
       set received_at = clock_timestamp() - interval '800 days',
           payload_retention_until = clock_timestamp() - interval '700 days',
           purge_after = clock_timestamp() - interval '600 days'
     where id = first_remote_id::uuid;
    result := public.np_admin_prune_expired_telemetry(1000);
    if result->>'payloads_redacted' <> '1'
       or result->>'acks_purged' <> '0'
       or result->>'envelopes_purged' <> '0'
       or not exists (
            select 1 from public.np_sync_envelopes
             where id = first_remote_id::uuid
               and payload = '{}'::jsonb
               and payload_redacted_at is not null
               and octet_length(payload_hash) = 32
       )
       or not exists (
            select 1 from public.np_sync_acks where envelope_id = first_remote_id::uuid
       )
    then
        raise exception 'retention did not preserve ACK/idempotency tombstones: %', result;
    end if;

    replay_result := public.erp_ingest_sync_batch(
        'installation_prod_a', repeat('a', 64), repeat('1', 64),
        clock_timestamp(), heartbeat_batch
    );
    if replay_result->>'code' <> 'replay' then
        raise exception 'persistent replay guard failed: %', replay_result;
    end if;

    duplicate_result := public.erp_ingest_sync_batch(
        'installation_prod_a', repeat('a', 64), repeat('2', 64),
        clock_timestamp(), heartbeat_batch
    );
    if duplicate_result->>'ok' <> 'true'
       or duplicate_result->'acks'->0->>'duplicate' <> 'true'
       or duplicate_result->'acks'->0->>'remote_id' <> first_remote_id
    then
        raise exception 'idempotent duplicate ACK failed: %', duplicate_result;
    end if;

    if (select count(*) from public.np_sync_envelopes where installation_id = installation_a) <> 1
       or (select count(*) from public.np_sync_acks where installation_id = installation_a) <> 1
       or (select count(*) from public.np_heartbeats where installation_id = installation_a) <> 1
       or (select count(*) from public.np_installation_versions where installation_id = installation_a) <> 1
    then
        raise exception 'idempotent persistence produced duplicate rows';
    end if;
    update public.np_sync_envelopes
       set payload_redacted_at = clock_timestamp() - interval '31 days'
     where id = first_remote_id::uuid;
    result := public.np_admin_prune_expired_telemetry(1000);
    if result->>'acks_purged' <> '1'
       or result->>'envelopes_purged' <> '1'
       or exists (select 1 from public.np_sync_envelopes where id = first_remote_id::uuid)
       or exists (select 1 from public.np_sync_acks where envelope_id = first_remote_id::uuid)
       or not exists (
            select 1 from public.np_heartbeats
             where tenant_id = tenant_a and installation_id = installation_a
       )
    then
        raise exception 'long-term ACK/envelope retention pruning failed: %', result;
    end if;
    delete from public.np_request_nonces
     where tenant_id = tenant_a
       and installation_id = installation_a
       and nonce like 'nexa:test:%';
    if not exists (
        select 1 from public.np_installations
         where id = installation_a
           and app_version = '1.0.0'
           and build_id = 'abcdef0'
           and build_commit = 'unknown'
           and environment = 'prod'
           and channel = 'prod'
    ) then
        raise exception 'heartbeat version/build projection failed';
    end if;

    result := public.np_authorize_nexa_request(
        'tenant_prod_a', 'installation_prod_a', repeat('a', 64),
        repeat('4', 64), clock_timestamp()
    );
    if result->>'ok' <> 'true'
       or result->>'tenant_id' <> 'tenant_prod_a'
       or result->>'installation_id' <> 'installation_prod_a'
    then
        raise exception 'Nexa installation authorization failed: %', result;
    end if;
    result := public.np_authorize_nexa_request(
        'tenant_prod_a', 'installation_prod_a', repeat('a', 64),
        repeat('4', 64), clock_timestamp()
    );
    if result->>'code' <> 'replay' then
        raise exception 'Nexa persistent replay protection failed: %', result;
    end if;
    result := public.np_authorize_nexa_request(
        'tenant_qa_b', 'installation_qa_b', repeat('b', 64),
        repeat('5', 64), clock_timestamp()
    );
    if result->>'code' <> 'unauthorized' then
        raise exception 'Nexa accepted a QA installation in PROD contract: %', result;
    end if;
    result := public.np_authorize_nexa_request(
        'tenant_prod_a', 'installation_prod_a', repeat('a', 64),
        repeat('6', 64), clock_timestamp() - interval '2 minutes'
    );
    if result->>'code' <> 'unauthorized' then
        raise exception 'Nexa accepted an expired request: %', result;
    end if;
    if not public.nexa_claim_erp_nonce(repeat('7', 64), repeat('8', 64))
       or public.nexa_claim_erp_nonce(repeat('7', 64), repeat('8', 64))
    then
        raise exception 'legacy Nexa bridge replay protection failed';
    end if;

    recovery_batch := jsonb_build_array(jsonb_build_object(
        'event_type', 'support_ticket',
        'aggregate_type', 'support_ticket',
        'aggregate_id', 'ticket_recovery_001',
        'payload', jsonb_build_object(
            'tenant_id', 'tenant_prod_a',
            'installation_id', 'installation_prod_a',
            'protocol', 'NXP-TEST-001',
            'created_by', 'requester_test_001',
            'subject', 'Validacao controlada de recovery',
            'category', 'admin_access_recovery',
            'description', 'Registro tecnico temporario sem dados pessoais.',
            'priority', 'normal',
            'correlation_id', 'correlation_recovery_001',
            'created_at', to_char(
                clock_timestamp() at time zone 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
            )
        ),
        'schema_version', 1,
        'idempotency_key', 'support-ticket:test:001'
    ));
    result := public.erp_ingest_sync_batch(
        'installation_prod_a', repeat('a', 64), repeat('3', 64),
        clock_timestamp(), recovery_batch
    );
    if result->>'ok' <> 'true' then
        raise exception 'recovery request ingestion failed: %', result;
    end if;
    select id into ticket_uuid
      from public.np_support_tickets
     where tenant_id = tenant_a
       and installation_id = installation_a
       and source_ticket_id = 'ticket_recovery_001';

    insert into public.np_platform_users (
        username, display_name, role, password_hash
    ) values (
        'admin_test', 'Admin temporario', 'platform_admin',
        'argon2id$TEST_ONLY_NOT_A_REAL_PASSWORD_HASH_000000000000'
    ) returning id into platform_admin;

    result := public.np_admin_authorize_reset(
        'ticket_recovery_001', platform_admin::text, 15
    );
    if result->>'code' <> 'invalid_payload' then
        raise exception 'source ticket authorization did not require tenant/install context: %', result;
    end if;
    if public.np_admin_get_reset_authorization('ticket_recovery_001') <> '{}'::jsonb then
        raise exception 'source ticket lookup did not require tenant/install context';
    end if;

    result := public.np_admin_authorize_reset(
        'ticket_recovery_001', platform_admin::text, 15,
        'tenant_prod_a', 'installation_prod_a'
    );
    if result->>'ok' <> 'true' or result->>'status' <> 'active' then
        raise exception 'admin recovery authorization failed: %', result;
    end if;
    authorization_projection := public.np_admin_get_reset_authorization(
        'ticket_recovery_001', 'tenant_prod_a', 'installation_prod_a'
    );
    if authorization_projection->>'status' <> 'active'
       or authorization_projection->>'ticket_id' <> 'ticket_recovery_001'
       or authorization_projection ? 'code_digest'
       or authorization_projection ? 'secret_digest'
    then
        raise exception 'admin recovery safe projection failed: %', authorization_projection;
    end if;
    authorization_projection := public.np_admin_get_reset_authorization(ticket_uuid::text);
    if authorization_projection->>'status' <> 'active'
       or authorization_projection->>'ticket_id' <> 'ticket_recovery_001'
    then
        raise exception 'global ticket UUID lookup failed: %', authorization_projection;
    end if;
    result := public.np_erp_access_reset_authorization(
        'tenant_prod_a', 'installation_prod_a', repeat('a', 64), repeat('a', 64),
        clock_timestamp(), 'status', 'requester_test_001', null
    );
    if result->>'ok' <> 'true'
       or result->'authorization'->>'authorization_id' <>
          authorization_projection->>'id'
    then
        raise exception 'remote recovery status failed: %', result;
    end if;
    result := public.np_erp_access_reset_authorization(
        'tenant_prod_a', 'installation_prod_a', repeat('a', 64), repeat('b', 64),
        clock_timestamp(), 'consume', 'requester_test_001',
        '99999999-9999-4999-8999-999999999999'::uuid
    );
    if result->>'code' <> 'authorization_unavailable' then
        raise exception 'remote recovery accepted the wrong authorization id: %', result;
    end if;
    result := public.np_erp_access_reset_authorization(
        'tenant_prod_a', 'installation_prod_a', repeat('a', 64), repeat('c', 64),
        clock_timestamp(), 'consume', 'requester_test_001',
        (authorization_projection->>'id')::uuid
    );
    if result->>'ok' <> 'true'
       or result->>'action' <> 'consume'
       or result->'authorization'->>'consumed_at' is null
    then
        raise exception 'atomic remote recovery consumption failed: %', result;
    end if;
    result := public.np_erp_access_reset_authorization(
        'tenant_prod_a', 'installation_prod_a', repeat('a', 64), repeat('d', 64),
        clock_timestamp(), 'consume', 'requester_test_001',
        (authorization_projection->>'id')::uuid
    );
    if result->>'code' <> 'authorization_unavailable' then
        raise exception 'one-use remote recovery authorization was reused: %', result;
    end if;
    authorization_projection := public.np_admin_get_reset_authorization(
        'ticket_recovery_001', 'tenant_prod_a', 'installation_prod_a'
    );
    if authorization_projection->>'status' <> 'consumed'
       or authorization_projection->>'used_at' is null
    then
        raise exception 'consumed recovery projection failed: %', authorization_projection;
    end if;

    if not exists (
        select 1 from public.np_tenants
         where id = tenant_a and kind = 'internal' and status = 'active'
    ) then
        raise exception 'explicit internal tenant classification was not persisted';
    end if;
end;
$$;

select format('{"tenant_id":"%s"}', id) as tenant_a_claims
  from public.np_tenants where tenant_key = 'tenant_prod_a' \gset
select format('{"tenant_id":"%s"}', id) as tenant_b_claims
  from public.np_tenants where tenant_key = 'tenant_qa_b' \gset

grant select on public.np_tenants, public.np_installations, public.np_platform_users
    to authenticated;
set local role authenticated;

select set_config('request.jwt.claims', '{}', true);
do $$
begin
    if (select count(*) from public.np_tenants) <> 0
       or (select count(*) from public.np_installations) <> 0
       or (select count(*) from public.np_platform_users) <> 0
    then
        raise exception 'missing tenant context did not fail closed';
    end if;
end;
$$;

select set_config('request.jwt.claims', :'tenant_a_claims', true);
do $$
begin
    if (select array_agg(tenant_key order by tenant_key) from public.np_tenants)
           is distinct from array['tenant_prod_a']::text[]
       or (select array_agg(installation_key order by installation_key) from public.np_installations)
           is distinct from array['installation_prod_a']::text[]
       or (select count(*) from public.np_platform_users) <> 0
    then
        raise exception 'PROD tenant RLS scope failed';
    end if;
end;
$$;

select set_config('request.jwt.claims', :'tenant_b_claims', true);
do $$
begin
    if (select array_agg(tenant_key order by tenant_key) from public.np_tenants)
           is distinct from array['tenant_qa_b']::text[]
       or (select array_agg(installation_key order by installation_key) from public.np_installations)
           is distinct from array['installation_qa_b']::text[]
    then
        raise exception 'QA tenant RLS scope failed';
    end if;
end;
$$;

reset role;
select extensions.pass('ERP Cloud PROD foundation contracts hold');
select * from extensions.finish();
rollback;
