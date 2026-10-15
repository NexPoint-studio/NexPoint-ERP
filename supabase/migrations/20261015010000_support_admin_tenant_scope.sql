-- Defense in depth: mirror Control Center authorized_tenant_ids for support RPCs.
-- No data/schema changes; platform_admin remains global.
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
 if not exists(select 1 from public.np_platform_users u where u.id=p_actor_id and u.active
   and (u.role='platform_admin' or (u.role='nexpoint_control_admin' and exists(
     select 1 from public.np_platform_user_tenants s where s.user_id=u.id and s.tenant_id=t.tenant_id)))) then
  return jsonb_build_object('ok',false,'code','not_found'); end if;
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
 if not exists(select 1 from public.np_platform_users u where u.id=p_actor_id and u.active
   and (u.role='platform_admin' or (u.role='nexpoint_control_admin' and exists(
     select 1 from public.np_platform_user_tenants s where s.user_id=u.id and s.tenant_id=t.tenant_id)))) then
  return jsonb_build_object('ok',false,'code','not_found'); end if;
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

create or replace function public.np_admin_support_attachment(p_actor_id uuid,p_ticket_id uuid,p_action text,p_attachment_id uuid,
 p_name text default '',p_content_type text default '',p_sha256 text default '',p_data_base64 text default '')
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare t public.np_support_tickets;
begin
 if not exists(select 1 from public.np_platform_users where id=p_actor_id and active and role in ('platform_admin','nexpoint_control_admin')) then
  return jsonb_build_object('ok',false,'code','unauthorized'); end if;
 select * into t from public.np_support_tickets where id=p_ticket_id;
 if not found then return jsonb_build_object('ok',false,'code','not_found'); end if;
 if not exists(select 1 from public.np_platform_users u where u.id=p_actor_id and u.active
   and (u.role='platform_admin' or (u.role='nexpoint_control_admin' and exists(
     select 1 from public.np_platform_user_tenants s where s.user_id=u.id and s.tenant_id=t.tenant_id)))) then
  return jsonb_build_object('ok',false,'code','not_found'); end if;
 return public.np_support_attachment_operation(t.tenant_id,t.installation_id,t.id,p_action,p_attachment_id,p_name,p_content_type,p_sha256,p_data_base64);
end; $$;

revoke all on function public.np_admin_support_attachment(uuid,uuid,text,uuid,text,text,text,text) from public,anon,authenticated;
grant execute on function public.np_admin_support_attachment(uuid,uuid,text,uuid,text,text,text,text) to service_role;
