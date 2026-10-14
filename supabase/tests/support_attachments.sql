\set ON_ERROR_STOP on
begin;
do $test$
declare provision jsonb; other jsonb; aid uuid; ticket uuid; response jsonb; input jsonb; data text;
 digest text; attachment uuid:='11111111-1111-4111-8111-111111111111'; admin_id uuid;
begin
 provision:=public.np_admin_provision_installation('qa-attach-a','QA attachment','internal','active','qa-attach-install-a','QA','prod','prod','qa-attach-key',repeat('a',64));
 other:=public.np_admin_provision_installation('qa-attach-b','QA attachment other','internal','active','qa-attach-install-b','QA','prod','prod','qa-attach-key-b',repeat('b',64));
 aid:=(provision->>'installation_id')::uuid;
 insert into public.np_support_tickets(tenant_id,installation_id,idempotency_key,correlation_id,source_ticket_id,category,subject,sanitized_description,opened_at)
 values((provision->>'tenant_id')::uuid,aid,'qa-attach-ticket','qa-correlation','qa-attach-ticket','general','QA attachment','Fixture',clock_timestamp()) returning id into ticket;
 data:=replace(encode(decode('89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de0000000c49444154089963606060000000040001f61738550000000049454e44ae426082','hex'),'base64'),E'\n','');
 digest:=encode(extensions.digest(decode(data,'base64'),'sha256'),'hex');
 input:=jsonb_build_object('schema_version',1,'tenant_id','qa-attach-a','installation_id','qa-attach-install-a','action','upload',
 'ticket_id','qa-attach-ticket','attachment_id',attachment,'name','screen.png','content_type','image/png','sha256',digest,'data_base64',data);
 response:=public.np_erp_support_attachment('qa-attach-a','qa-attach-install-a',repeat('a',64),md5('1')||md5('1'),clock_timestamp(),input);
 if response->>'ok'<>'true' or response->'attachments'->0->>'data_base64'<>'' then raise exception 'upload metadata only'; end if;
 response:=public.np_erp_support_attachment('qa-attach-a','qa-attach-install-a',repeat('a',64),md5('2')||md5('2'),clock_timestamp(),input);
 if response->>'ok'<>'true' or (select count(*) from public.np_support_attachments where ticket_id=ticket)<>1 then raise exception 'upload duplicate'; end if;
 response:=public.np_erp_support_attachment('qa-attach-a','qa-attach-install-a',repeat('a',64),md5('3')||md5('3'),clock_timestamp(),input||'{"action":"download"}');
 if response->>'ok'<>'true' or response->'attachments'->0->>'data_base64'<>data then raise exception 'download'; end if;
 response:=public.np_erp_support_attachment('qa-attach-b','qa-attach-install-b',repeat('b',64),md5('4')||md5('4'),clock_timestamp(),input||'{"tenant_id":"qa-attach-b","installation_id":"qa-attach-install-b","action":"download"}');
 if response->>'code'<>'not_found' then raise exception 'cross tenant'; end if;
 response:=public.np_erp_support_attachment('qa-attach-a','qa-attach-install-a',repeat('a',64),md5('5')||md5('5'),clock_timestamp(),input||'{"name":"../screen.png"}');
 if response->>'code'<>'invalid_payload' then raise exception 'traversal'; end if;
 response:=public.np_erp_support_attachment('qa-attach-a','qa-attach-install-a',repeat('a',64),md5('6')||md5('6'),clock_timestamp(),input||jsonb_build_object('sha256',repeat('a',64)));
 if response->>'code'<>'invalid_payload' then raise exception 'hash'; end if;
 for idx in 2..4 loop
  response:=public.np_erp_support_attachment('qa-attach-a','qa-attach-install-a',repeat('a',64),md5('upload'||idx)||md5('upload'||idx),clock_timestamp(),
   input||jsonb_build_object('attachment_id',extensions.gen_random_uuid()));
  if idx<4 and response->>'ok'<>'true' then raise exception 'allowed count'; end if;
  if idx=4 and response->>'code'<>'attachment_limit' then raise exception 'limit'; end if;
 end loop;
 if has_table_privilege('anon','public.np_support_attachments','select') or has_table_privilege('authenticated','public.np_support_attachments','select')
 or has_function_privilege('authenticated','public.np_support_attachment_operation(uuid,uuid,uuid,text,uuid,text,text,text,text)','execute') then raise exception 'private RLS/grants'; end if;
 if not exists(select 1 from pg_class where oid='public.np_support_attachments'::regclass and relrowsecurity and relforcerowsecurity) then raise exception 'RLS enabled'; end if;
 insert into public.np_platform_users(username,display_name,role,password_hash,active)
 values('qa-attach-admin','QA','platform_admin','scrypt$'||repeat('a',64),true) returning id into admin_id;
 perform public.np_admin_provision_installation('qa-attach-a','QA attachment','internal','active','qa-attach-remaining','QA','prod','prod','qa-attach-remaining-key',repeat('c',64));
 response:=public.np_admin_reassociate_installation(admin_id,extensions.gen_random_uuid(),'qa-attach-a','qa-attach-moved',
 'QA moved','qa-attach-install-a','QA',repeat('a',64),repeat('b',64));
 if response->>'ok'<>'true' then raise exception 'handoff failed %',response->>'code'; end if;
 if exists(select 1 from public.np_support_attachments x join public.np_support_tickets t on t.id=x.ticket_id
  where x.ticket_id=ticket and (x.tenant_id<>t.tenant_id or x.installation_id<>t.installation_id)) then raise exception 'attachment handoff preservation'; end if;
 raise notice 'support attachment ownership/hash/traversal/idempotency/limit/RLS PASS';
end $test$;
rollback;
