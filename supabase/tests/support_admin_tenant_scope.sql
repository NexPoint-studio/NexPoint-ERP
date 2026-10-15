\set ON_ERROR_STOP on
begin;
do $test$
declare a jsonb; b jsonb; ta uuid; tb uuid; scoped uuid; global_admin uuid; inactive uuid;
 actor uuid; target uuid; response jsonb; expected text; before_count bigint; idx integer:=0;
begin
 a:=public.np_admin_provision_installation('qa-scope-a','QA scope A','internal','active','qa-scope-install-a','QA','prod','prod','qa-scope-key-a',repeat('a',64));
 b:=public.np_admin_provision_installation('qa-scope-b','QA scope B','internal','active','qa-scope-install-b','QA','prod','prod','qa-scope-key-b',repeat('b',64));
 insert into public.np_support_tickets(tenant_id,installation_id,idempotency_key,correlation_id,source_ticket_id,category,subject,sanitized_description,opened_at)
 values((a->>'tenant_id')::uuid,(a->>'installation_id')::uuid,'scope-a','scope-a','scope-a','general','QA','Fixture',clock_timestamp()) returning id into ta;
 insert into public.np_support_tickets(tenant_id,installation_id,idempotency_key,correlation_id,source_ticket_id,category,subject,sanitized_description,opened_at)
 values((b->>'tenant_id')::uuid,(b->>'installation_id')::uuid,'scope-b','scope-b','scope-b','general','QA','Fixture',clock_timestamp()) returning id into tb;
 insert into public.np_platform_users(username,display_name,role,password_hash,active)
 values('qa-scope-limited','QA','nexpoint_control_admin','scrypt$'||repeat('a',64),true) returning id into scoped;
 insert into public.np_platform_users(username,display_name,role,password_hash,active)
 values('qa-scope-global','QA','platform_admin','scrypt$'||repeat('a',64),true) returning id into global_admin;
 insert into public.np_platform_users(username,display_name,role,password_hash,active)
 values('qa-scope-inactive','QA','platform_admin','scrypt$'||repeat('a',64),false) returning id into inactive;
 insert into public.np_platform_user_tenants(user_id,tenant_id) values(scoped,(a->>'tenant_id')::uuid);
 foreach actor in array array[scoped,global_admin,inactive,extensions.gen_random_uuid()] loop
  foreach target in array array[ta,tb] loop
   idx:=idx+1;
   expected:=case when actor=inactive or actor not in (scoped,global_admin) then 'unauthorized'
    when actor=scoped and target=tb then 'not_found' else null end;
   select count(*) into before_count from public.np_support_ticket_events where ticket_id=target;
   response:=public.np_admin_support_reply(target,actor,'scope-message-'||idx,'QA message');
   if expected is null then
    if response->>'ok' is distinct from 'true' then raise exception 'reply allowed failed %',response; end if;
   elsif response->>'code' is distinct from expected then raise exception 'reply denied failed %',response; end if;
   response:=public.np_admin_support_status(target,actor,'in_progress');
   if expected is null then
    if response->>'ok' is distinct from 'true' then raise exception 'status allowed failed %',response; end if;
   elsif response->>'code' is distinct from expected then raise exception 'status denied failed %',response; end if;
   response:=public.np_admin_support_attachment(actor,target,'list',null);
   if expected is null then
    if response->>'ok' is distinct from 'true' then raise exception 'attachment allowed failed %',response; end if;
   elsif response->>'code' is distinct from expected then raise exception 'attachment denied failed %',response; end if;
   if expected is not null and (select count(*) from public.np_support_ticket_events where ticket_id=target)<>before_count then raise exception 'denied mutation'; end if;
  end loop;
 end loop;
 delete from public.np_platform_user_tenants where user_id=scoped;
 if public.np_admin_support_reply(ta,scoped,'revoked-scope','QA')->>'code' is distinct from 'not_found'
  or public.np_admin_support_status(ta,scoped,'open')->>'code' is distinct from 'not_found'
  or public.np_admin_support_attachment(scoped,ta,'list',null)->>'code' is distinct from 'not_found' then raise exception 'revoked scope'; end if;
 if has_function_privilege('anon','public.np_admin_support_reply(uuid,uuid,text,text)','execute')
  or has_function_privilege('authenticated','public.np_admin_support_status(uuid,uuid,text)','execute')
  or has_function_privilege('authenticated','public.np_admin_support_attachment(uuid,uuid,text,uuid,text,text,text,text)','execute') then raise exception 'RPC grants'; end if;
 raise notice 'support admin scope PASS: 24 role/tenant operations, revoked scope, no denied mutation, private grants';
end $test$;
rollback;
