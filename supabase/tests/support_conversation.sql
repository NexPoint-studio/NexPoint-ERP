\set ON_ERROR_STOP on
begin;
do $test$
declare a jsonb; b jsonb; aid uuid; bid uuid; ticket uuid; page_ticket uuid; admin_id uuid; response jsonb; input jsonb;
 n integer:=0; result_text text;
begin
 a:=public.np_admin_provision_installation('qa-support-a','QA Support A','internal','active','qa-support-install-a','QA','prod','prod','qa-key-a',repeat('a',64));
 b:=public.np_admin_provision_installation('qa-support-b','QA Support B','internal','active','qa-support-install-b','QA','prod','prod','qa-key-b',repeat('b',64));
 if a->>'ok'<>'true' or b->>'ok'<>'true' then raise exception 'fixture provisioning'; end if;
 aid:=(a->>'installation_id')::uuid; bid:=(b->>'installation_id')::uuid;
 insert into public.np_support_tickets(tenant_id,installation_id,idempotency_key,correlation_id,source_ticket_id,category,subject,sanitized_description,opened_at)
 values((a->>'tenant_id')::uuid,aid,'qa-ticket','qa-correlation','qa-ticket','general','QA support','Fixture',clock_timestamp()) returning id into ticket;
 insert into public.np_support_ticket_events(tenant_id,installation_id,ticket_id,idempotency_key,event_type,occurred_at,metadata)
 values((a->>'tenant_id')::uuid,aid,ticket,'private-qa','internal_note',clock_timestamp(),'{"body":"PRIVATE_CANARY"}');
 input:=jsonb_build_object('schema_version',1,'tenant_id','qa-support-a','installation_id','qa-support-install-a','action','detail','ticket_id','qa-ticket','operation_id','','body','','status','');
 response:=public.np_erp_support('qa-support-a','qa-support-install-a',repeat('a',64),md5('1')||md5('1'),clock_timestamp(),input);
 if response->>'ok'<>'true' or response::text like '%PRIVATE_CANARY%' or jsonb_array_length(response->'tickets'->0->'messages')<>0 then raise exception 'public projection'; end if;
 response:=public.np_erp_support('qa-support-b','qa-support-install-b',repeat('b',64),md5('2')||md5('2'),clock_timestamp(),input||'{"tenant_id":"qa-support-b","installation_id":"qa-support-install-b"}');
 if response->>'code'<>'not_found' then raise exception 'cross tenant'; end if;
 response:=public.np_erp_support('qa-support-b','qa-support-install-a',repeat('a',64),md5('3')||md5('3'),clock_timestamp(),input);
 if response->>'code'<>'unauthorized' then raise exception 'spoof'; end if;
 input:=input||'{"action":"reply","body":"QA public reply","operation_id":"11111111-1111-4111-8111-111111111111"}';
 response:=public.np_erp_support('qa-support-a','qa-support-install-a',repeat('a',64),md5('4')||md5('4'),clock_timestamp(),input);
 if response->>'ok'<>'true' or jsonb_array_length(response->'tickets'->0->'messages')<>1 then raise exception 'reply'; end if;
 response:=public.np_erp_support('qa-support-a','qa-support-install-a',repeat('a',64),md5('5')||md5('5'),clock_timestamp(),input);
 if response->>'ok'<>'true' or jsonb_array_length(response->'tickets'->0->'messages')<>1 then raise exception 'idempotency'; end if;
 response:=public.np_erp_support('qa-support-a','qa-support-install-a',repeat('a',64),md5('5')||md5('5'),clock_timestamp(),input);
 if response->>'code'<>'replay' then raise exception 'replay'; end if;
 response:=public.np_erp_support('qa-support-a','qa-support-install-a',repeat('a',64),md5('6')||md5('6'),clock_timestamp(),input||'{"body":"Changed"}');
 if response->>'code'<>'conflict' then raise exception 'operation conflict'; end if;
 input:=input||'{"action":"status","body":"","status":"closed","operation_id":"22222222-2222-4222-8222-222222222222"}';
 response:=public.np_erp_support('qa-support-a','qa-support-install-a',repeat('a',64),md5('7')||md5('7'),clock_timestamp(),input);
 if response->'tickets'->0->>'status'<>'closed' then raise exception 'close'; end if;
 input:=input||'{"status":"open","operation_id":"33333333-3333-4333-8333-333333333333"}';
 response:=public.np_erp_support('qa-support-a','qa-support-install-a',repeat('a',64),md5('8')||md5('8'),clock_timestamp(),input);
 if response->'tickets'->0->>'status'<>'open' then raise exception 'reopen'; end if;
 insert into public.np_platform_users(username,display_name,role,password_hash,active)
 values('qa-support-admin','QA','platform_admin','scrypt$'||repeat('a',64),true) returning id into admin_id;
 response:=public.np_admin_support_reply(ticket,admin_id,'qa-public-message','QA support answer');
 if response->>'ok'<>'true' then raise exception 'admin reply'; end if;
 response:=public.np_admin_support_reply(ticket,admin_id,'qa-public-message','QA support answer');
 if response->>'ok'<>'true' then raise exception 'admin duplicate'; end if;
 response:=public.np_admin_support_reply(ticket,admin_id,'qa-public-message','Changed');
 if response->>'code'<>'conflict' then raise exception 'admin collision'; end if;
 if (select count(*) from public.np_support_ticket_events where ticket_id=ticket and event_type='public_reply')<>1 then raise exception 'duplicate admin'; end if;
 if has_function_privilege('anon','public.np_erp_support(text,text,text,text,timestamptz,jsonb)','execute')
 or has_function_privilege('authenticated','public.np_admin_support_reply(uuid,uuid,text,text)','execute') then raise exception 'RPC grants'; end if;
 insert into public.np_support_tickets(tenant_id,installation_id,idempotency_key,correlation_id,source_ticket_id,category,subject,sanitized_description,opened_at)
 values((a->>'tenant_id')::uuid,aid,'qa-page','qa-page','qa-page','general','QA pages','Fixture',clock_timestamp()) returning id into page_ticket;
 insert into public.np_support_ticket_events(tenant_id,installation_id,ticket_id,idempotency_key,event_type,occurred_at,metadata)
 select (a->>'tenant_id')::uuid,aid,page_ticket,'qa-page-'||idx,'public_reply',clock_timestamp()+idx*interval '1 second',
 jsonb_build_object('body','message-'||idx) from generate_series(1,201) idx;
 input:=jsonb_build_object('schema_version',1,'tenant_id','qa-support-a','installation_id','qa-support-install-a','action','history',
 'ticket_id','qa-page','operation_id','','body','0','status','');
 response:=public.np_erp_support('qa-support-a','qa-support-install-a',repeat('a',64),md5('page1')||md5('page1'),clock_timestamp(),input);
 if jsonb_array_length(response->'tickets'->0->'messages')<>200 or response->'tickets'->0->>'messages_truncated'<>'true'
 or response->'tickets'->0->'messages'->0->>'body'<>'message-1' then raise exception 'history page1'; end if;
 response:=public.np_erp_support('qa-support-a','qa-support-install-a',repeat('a',64),md5('page2')||md5('page2'),clock_timestamp(),input||'{"body":"200"}');
 if jsonb_array_length(response->'tickets'->0->'messages')<>1 or response->'tickets'->0->>'messages_truncated'<>'false'
 or response->'tickets'->0->'messages'->0->>'body'<>'message-201' then raise exception 'history page2'; end if;
 insert into public.np_support_tickets(tenant_id,installation_id,idempotency_key,correlation_id,source_ticket_id,category,subject,sanitized_description,opened_at)
 select (a->>'tenant_id')::uuid,aid,'list-page-'||idx,'qa-page','list-page-'||idx,'general','QA pages','Fixture',clock_timestamp()
 from generate_series(1,100) idx;
 response:=public.np_erp_support('qa-support-a','qa-support-install-a',repeat('a',64),md5('list1')||md5('list1'),clock_timestamp(),input||'{"action":"list","ticket_id":"","body":"0"}');
 if jsonb_array_length(response->'tickets')<>100 or response->>'tickets_truncated'<>'true' then raise exception 'ticket page1'; end if;
 response:=public.np_erp_support('qa-support-a','qa-support-install-a',repeat('a',64),md5('list2')||md5('list2'),clock_timestamp(),input||'{"action":"list","ticket_id":"","body":"100"}');
 if jsonb_array_length(response->'tickets')<>2 or response->>'tickets_truncated'<>'false' then raise exception 'ticket page2'; end if;
 raise notice 'support conversation isolation/replay/idempotency/status/internal-note tests PASS';
end $test$;
rollback;
