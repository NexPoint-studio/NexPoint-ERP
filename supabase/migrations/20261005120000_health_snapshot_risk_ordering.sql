-- A delayed/bucketed health snapshot cannot resolve a risk detected after it.
-- Keep the existing constraint and authenticated ingestion/nonce/ACK contract.
do $migration$
declare
    definition text;
    original text := $needle$and risk.status in ('open', 'acknowledged', 'monitoring')$needle$;
    replacement text := $replacement$and risk.status in ('open', 'acknowledged', 'monitoring')
                           and risk.detected_at <= v_occurred_at$replacement$;
begin
    select pg_get_functiondef(
        'public.erp_ingest_sync_batch(text,text,text,timestamptz,jsonb)'::regprocedure
    ) into definition;
    -- Match a single stable token, independent of CRLF/LF checkout conversion.
    if (length(definition) - length(replace(definition, original, ''))) / length(original) <> 1
       or position('and risk.detected_at <= v_occurred_at' in definition) <> 0 then
        raise exception 'unexpected health ingestion contract';
    end if;
    execute replace(definition, original, replacement);
end $migration$;
