-- Public support conversation. Existing ticket/event tables retain RLS and composite ownership FKs.
create or replace function public.np_support_public_ticket_page(t public.np_support_tickets, include_messages boolean, page_offset integer)
returns jsonb language sql stable security definer set search_path = pg_catalog as $$
 select jsonb_build_object('ticket_id',t.source_ticket_id,'protocol',coalesce(t.metadata->>'protocol',t.idempotency_key),
 'subject',t.subject,'description',t.sanitized_description,'category',t.category,'priority',t.priority,
 'status',case when t.status='cancelled' then 'closed' else t.status::text end,'opened_at',t.opened_at,'updated_at',t.updated_at,
 'messages',case when include_messages then coalesce((select jsonb_agg(m.item order by m.occurred_at,m.id) from
   (select e.id,e.occurred_at,jsonb_build_object('id',e.id,'event_type',e.event_type,
     'body',case when e.event_type in ('client_reply','public_reply') then coalesce(e.metadata->>'body','') else '' end,
     'status',coalesce(e.metadata->>'to_status',''),'created_at',e.occurred_at) item
    from public.np_support_ticket_events e where e.tenant_id=t.tenant_id and e.installation_id=t.installation_id
      and e.ticket_id=t.id and e.event_type in ('client_reply','public_reply','public_status')
    order by e.occurred_at asc,e.id asc limit 200 offset page_offset) m),'[]'::jsonb) else '[]'::jsonb end,
 'messages_truncated',include_messages and (select count(*)>200+page_offset from public.np_support_ticket_events e
 where e.tenant_id=t.tenant_id and e.installation_id=t.installation_id and e.ticket_id=t.id
 and e.event_type in ('client_reply','public_reply','public_status')));
$$;

create or replace function public.np_support_public_ticket(t public.np_support_tickets, include_messages boolean)
returns jsonb language sql stable security definer set search_path = pg_catalog as $$
 select public.np_support_public_ticket_page(t,include_messages,0);
$$;
revoke all on function public.np_support_public_ticket_page(public.np_support_tickets,boolean,integer) from public,anon,authenticated;
grant execute on function public.np_support_public_ticket_page(public.np_support_tickets,boolean,integer) to service_role;

create or replace function public.np_erp_support(p_tenant_id text,p_installation_id text,p_secret_hash text,
 p_nonce_hash text,p_sent_at timestamptz,p_input jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare i public.np_installations; t public.np_support_tickets; e public.np_support_ticket_events;
 nonce_id uuid; instant timestamptz := clock_timestamp(); action text := p_input->>'action';
 operation text := p_input->>'operation_id'; event_kind text; data jsonb; page_offset integer:=0;
begin
    if p_secret_hash is null or p_secret_hash !~ '^[0-9a-f]{64}$'
       or p_nonce_hash is null or p_nonce_hash !~ '^[0-9a-f]{64}$'
       or p_sent_at is null or abs(extract(epoch from instant-p_sent_at)) > 60
 then
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
        and installation_id = i.id and nonce like 'support:%' and accepted_at >= instant - interval '1 minute') >= 60 then
        return jsonb_build_object('ok', false, 'code', 'rate_limited');
    end if;
    insert into public.np_request_nonces(tenant_id, installation_id, nonce, request_timestamp, accepted_at, expires_at)
        values(i.tenant_id, i.id, 'support:' || p_nonce_hash, p_sent_at, instant, instant + interval '10 minutes')
        on conflict (tenant_id, installation_id, nonce) do nothing returning id into nonce_id;
    if nonce_id is null then return jsonb_build_object('ok', false, 'code', 'replay'); end if;
    if p_input->>'tenant_id' is distinct from p_tenant_id or p_input->>'installation_id' is distinct from p_installation_id
      or p_input->>'schema_version' is distinct from '1' or action is null or action not in ('list','detail','history','reply','status') then
      return jsonb_build_object('ok',false,'code','invalid_payload'); end if;
    if action in ('list','history') then
      if coalesce(p_input->>'body','') !~ '^(0|[1-9][0-9]{0,5})?$' then
        return jsonb_build_object('ok',false,'code','invalid_payload'); end if;
      page_offset:=coalesce(nullif(p_input->>'body',''),'0')::integer;
      if page_offset>100000 then return jsonb_build_object('ok',false,'code','invalid_payload'); end if;
    end if;
    if action='list' then
      return jsonb_build_object('ok',true,'tickets',coalesce((select jsonb_agg(public.np_support_public_ticket(x,false))
        from (select * from public.np_support_tickets where tenant_id=i.tenant_id and installation_id=i.id
        order by updated_at desc,id limit 100 offset page_offset) x),'[]'::jsonb),'tickets_truncated',
        (select count(*)>100+page_offset from public.np_support_tickets where tenant_id=i.tenant_id and installation_id=i.id));
    end if;
    select * into t from public.np_support_tickets where tenant_id=i.tenant_id and installation_id=i.id
      and source_ticket_id=p_input->>'ticket_id' for update;
    if not found then return jsonb_build_object('ok',false,'code','not_found'); end if;
    if action in ('reply','status') then
      if operation is null or operation !~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$' then
        return jsonb_build_object('ok',false,'code','invalid_payload'); end if;
      event_kind := case when action='reply' then 'client_reply' else 'public_status' end;
      data := case when action='reply' then jsonb_build_object('body',p_input->>'body','to_status','open')
        else jsonb_build_object('body','','to_status',p_input->>'status') end;
      if (action='reply' and (coalesce(length(btrim(p_input->>'body')),0) not between 1 and 4000
          or not public.np_jsonb_is_sanitized(data)))
         or (action='status' and coalesce(p_input->>'status','') not in ('open','resolved','closed')) then
        return jsonb_build_object('ok',false,'code','invalid_payload'); end if;
      select * into e from public.np_support_ticket_events where tenant_id=i.tenant_id and installation_id=i.id
        and idempotency_key='client:'||operation;
      if found then
        if e.ticket_id<>t.id or e.event_type<>event_kind or e.metadata<>data then
          return jsonb_build_object('ok',false,'code','conflict'); end if;
      else
        if action='reply' and t.status in ('closed','cancelled') then
          return jsonb_build_object('ok',false,'code','conflict'); end if;
        if action='status' and p_input->>'status'='open' and t.status not in ('closed','resolved','cancelled') then
          return jsonb_build_object('ok',false,'code','conflict'); end if;
        insert into public.np_support_ticket_events(tenant_id,installation_id,ticket_id,idempotency_key,event_type,occurred_at,metadata)
          values(i.tenant_id,i.id,t.id,'client:'||operation,event_kind,instant,data);
        update public.np_support_tickets set status=(data->>'to_status')::public.np_ticket_status,
          closed_at=case when data->>'to_status'='closed' then instant else null end where id=t.id returning * into t;
      end if;
    end if;
    return jsonb_build_object('ok',true,'tickets',jsonb_build_array(public.np_support_public_ticket_page(t,true,page_offset)),'tickets_truncated',false);
end;
$$;
revoke all on function public.np_support_public_ticket(public.np_support_tickets,boolean) from public,anon,authenticated;
revoke all on function public.np_erp_support(text,text,text,text,timestamptz,jsonb) from public,anon,authenticated;
grant execute on function public.np_support_public_ticket(public.np_support_tickets,boolean),
 public.np_erp_support(text,text,text,text,timestamptz,jsonb) to service_role;

-- Internal Control Center writes use the same transactional event/status boundary.
create or replace function public.np_admin_support_reply(p_ticket_id uuid,p_actor_id uuid,p_message_id text,p_body text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare t public.np_support_tickets; e public.np_support_ticket_events; data jsonb; instant timestamptz:=clock_timestamp();
begin
 if not exists(select 1 from public.np_platform_users where id=p_actor_id and active
   and role in ('platform_admin','nexpoint_control_admin')) then return jsonb_build_object('ok',false,'code','unauthorized'); end if;
 if not public.np_identifier_is_valid(p_message_id,160) or coalesce(length(btrim(p_body)),0) not between 1 and 4000 then
   return jsonb_build_object('ok',false,'code','invalid_payload'); end if;
 data:=jsonb_build_object('body',p_body,'author_id',p_actor_id,'to_status','waiting_customer');
 if not public.np_jsonb_is_sanitized(data) then return jsonb_build_object('ok',false,'code','invalid_payload'); end if;
 select * into t from public.np_support_tickets where id=p_ticket_id for update;
 if not found then return jsonb_build_object('ok',false,'code','not_found'); end if;
 select * into e from public.np_support_ticket_events where tenant_id=t.tenant_id and installation_id=t.installation_id
   and idempotency_key='public:'||p_message_id;
 if found then
   if e.ticket_id<>t.id or e.event_type<>'public_reply' or e.metadata<>data then
     return jsonb_build_object('ok',false,'code','conflict'); end if;
 else
   if t.status in ('closed','cancelled') then return jsonb_build_object('ok',false,'code','conflict'); end if;
   insert into public.np_support_ticket_events(tenant_id,installation_id,ticket_id,idempotency_key,event_type,occurred_at,metadata)
     values(t.tenant_id,t.installation_id,t.id,'public:'||p_message_id,'public_reply',instant,data) returning * into e;
   update public.np_support_tickets set status='waiting_customer',closed_at=null where id=t.id;
 end if;
 return jsonb_build_object('ok',true,'message_id',e.id,'created_at',e.occurred_at);
end; $$;
revoke all on function public.np_admin_support_reply(uuid,uuid,text,text) from public,anon,authenticated;
grant execute on function public.np_admin_support_reply(uuid,uuid,text,text) to service_role;

create or replace function public.np_admin_support_status(p_ticket_id uuid,p_actor_id uuid,p_status text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare t public.np_support_tickets; instant timestamptz:=clock_timestamp();
begin
 if not exists(select 1 from public.np_platform_users where id=p_actor_id and active
   and role in ('platform_admin','nexpoint_control_admin')) then return jsonb_build_object('ok',false,'code','unauthorized'); end if;
 if p_status is null or p_status not in ('open','in_progress','waiting_customer','resolved','closed') then
   return jsonb_build_object('ok',false,'code','invalid_payload'); end if;
 select * into t from public.np_support_tickets where id=p_ticket_id for update;
 if not found then return jsonb_build_object('ok',false,'code','not_found'); end if;
 if t.status::text<>p_status then
  insert into public.np_support_ticket_events(tenant_id,installation_id,ticket_id,idempotency_key,event_type,occurred_at,metadata)
   values(t.tenant_id,t.installation_id,t.id,'status:'||extensions.gen_random_uuid(),'public_status',instant,
    jsonb_build_object('from_status',t.status,'to_status',p_status,'actor_id',p_actor_id));
  update public.np_support_tickets set status=p_status::public.np_ticket_status,
   closed_at=case when p_status='closed' then instant else null end where id=t.id;
 end if;
 return jsonb_build_object('ok',true);
end; $$;
revoke all on function public.np_admin_support_status(uuid,uuid,text) from public,anon,authenticated;
grant execute on function public.np_admin_support_status(uuid,uuid,text) to service_role;
