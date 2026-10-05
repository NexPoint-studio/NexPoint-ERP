\set ON_ERROR_STOP on

-- Disposable fixtures only. All changes, including successful ACKs, roll back.
begin;
do $test$
declare
    provision jsonb;
    result jsonb;
    batch jsonb;
    tenant_id uuid;
    fixture_installation_id uuid;
    snapshot_time timestamptz := date_trunc('minute', clock_timestamp()) - interval '5 minutes';
begin
    provision := public.np_admin_provision_installation(
        'qa_health_order_tenant', 'QA health ordering', 'internal', 'active',
        'qa_health_order_install', 'QA disposable', 'prod', 'prod',
        'qa_health_order_key', repeat('d', 64));
    if provision->>'ok' <> 'true' then
        raise exception 'health ordering fixture provisioning failed';
    end if;
    tenant_id := (provision->>'tenant_id')::uuid;
    fixture_installation_id := (provision->>'installation_id')::uuid;
    insert into public.np_risks (
        tenant_id, installation_id, idempotency_key, correlation_id,
        source_risk_id, fingerprint, severity, title, detected_at
    ) values
        (tenant_id, fixture_installation_id, 'health-order:older', 'health-order',
         'older', 'older', 'warning', 'Older omitted risk', snapshot_time - interval '1 minute'),
        (tenant_id, fixture_installation_id, 'health-order:newer', 'health-order',
         'newer', 'newer', 'warning', 'Risk after snapshot', snapshot_time + interval '15 seconds'),
        (tenant_id, fixture_installation_id, 'health-order:active', 'health-order',
         'active', 'active', 'warning', 'Still active risk', snapshot_time - interval '1 minute');

    batch := jsonb_build_array(jsonb_build_object(
        'event_type', 'health', 'aggregate_type', 'health', 'aggregate_id', 'health-order-1',
        'schema_version', 1, 'idempotency_key', 'health-order:1',
        'payload', jsonb_build_object(
            'tenant_id', 'qa_health_order_tenant', 'installation_id', 'qa_health_order_install',
            'status', 'warning', 'captured_at', to_char(snapshot_time at time zone 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
            'active_risk_fingerprints', jsonb_build_array('active'))));
    result := public.erp_ingest_sync_batch('qa_health_order_install', repeat('d',64),
        repeat('1',64), clock_timestamp(), batch);
    if result->>'ok' <> 'true' or jsonb_array_length(result->'acks') <> 1 then
        raise exception 'stale health must receive durable ACK, got %', result->>'code';
    end if;
    if not exists(select 1 from public.np_risks r where r.installation_id=fixture_installation_id
        and r.source_risk_id='newer' and r.status='open' and r.resolved_at is null) then
        raise exception 'stale snapshot closed a newer risk';
    end if;
    if not exists(select 1 from public.np_risks r where r.installation_id=fixture_installation_id
        and r.source_risk_id='older' and r.status='mitigated' and r.resolved_at=snapshot_time) then
        raise exception 'older omitted risk did not resolve at the snapshot time';
    end if;
    if not exists(select 1 from public.np_risks r where r.installation_id=fixture_installation_id
        and r.source_risk_id='active' and r.status='open' and r.resolved_at is null) then
        raise exception 'snapshot closed an explicitly active risk';
    end if;
    result := public.erp_ingest_sync_batch('qa_health_order_install', repeat('d',64),
        repeat('2',64), clock_timestamp(), batch);
    if result->>'ok' <> 'true' or result->'acks'->0->>'duplicate' <> 'true' then
        raise exception 'health ACK retry lost idempotency';
    end if;

    batch := jsonb_set(batch, '{0,idempotency_key}', '"health-order:2"');
    batch := jsonb_set(batch, '{0,aggregate_id}', '"health-order-2"');
    batch := jsonb_set(batch, '{0,payload,captured_at}', to_jsonb(to_char(
        (snapshot_time + interval '1 minute') at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')));
    result := public.erp_ingest_sync_batch('qa_health_order_install', repeat('d',64),
        repeat('3',64), clock_timestamp(), batch);
    if result->>'ok' <> 'true' then
        raise exception 'later health snapshot failed';
    end if;
    if not exists(select 1 from public.np_risks r where r.installation_id=fixture_installation_id
        and r.source_risk_id='newer' and r.status='mitigated'
        and r.resolved_at=snapshot_time + interval '1 minute') then
        raise exception 'later snapshot did not resolve the omitted risk';
    end if;
    if exists(select 1 from public.np_risks r where r.installation_id=fixture_installation_id
        and r.resolved_at < r.detected_at) then
        raise exception 'risk chronology constraint was weakened';
    end if;
end $test$;
select 'health snapshot ordering: PASS';
rollback;
