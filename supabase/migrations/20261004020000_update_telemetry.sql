-- Additive: old clients may omit update telemetry. Distribution channel remains
-- server-owned; an authenticated heartbeat can report state, never change scope.
alter table public.np_installations
    add column update_state text,
    add column update_reported_at timestamptz,
    add constraint np_installations_update_state_valid check (
        update_state is null or update_state in (
            'IDLE', 'CHECKING', 'CURRENT', 'AVAILABLE', 'DOWNLOADING', 'READY',
            'INSTALLING', 'RESTARTING', 'SUCCEEDED', 'FAILED', 'ROLLED_BACK', 'ROLLBACK_REQUIRED'
        )
    );

create function public.np_capture_update_heartbeat() returns trigger
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
       set update_state = v_state, update_reported_at = v_observed
     where id = new.installation_id and tenant_id = new.tenant_id
       and update_channel = v_channel
       and (update_reported_at is null or v_observed > update_reported_at);
    return new;
end;
$$;
revoke all on function public.np_capture_update_heartbeat() from public, anon, authenticated, service_role;
create trigger np_capture_update_heartbeat after insert on public.np_heartbeats
    for each row execute function public.np_capture_update_heartbeat();
