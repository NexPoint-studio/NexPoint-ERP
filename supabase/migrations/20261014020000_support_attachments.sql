-- Bounded private binary attachments; no public bucket or filesystem path.
create table public.np_support_attachments (
 id uuid primary key, tenant_id uuid not null, installation_id uuid not null, ticket_id uuid not null,
 name text not null, content_type text not null, sha256 text not null, data bytea not null,
 created_at timestamptz not null default clock_timestamp(),
 foreign key(tenant_id,installation_id,ticket_id) references public.np_support_tickets(tenant_id,installation_id,id) on update cascade on delete restrict,
 check(length(name) between 1 and 120 and name ~ '^[A-Za-z0-9_][A-Za-z0-9_. ()-]*$' and position('..' in name)=0),
 check(content_type in ('image/png','image/jpeg','image/webp')),
 check(octet_length(data) between 12 and 1048576), check(sha256 ~ '^[0-9a-f]{64}$'),
 check(sha256=encode(extensions.digest(data,'sha256'),'hex'))
);
create index np_support_attachments_ticket_idx on public.np_support_attachments(tenant_id,installation_id,ticket_id,created_at);
alter table public.np_support_attachments enable row level security;
alter table public.np_support_attachments force row level security;
revoke all on public.np_support_attachments from public,anon,authenticated;
grant select,insert on public.np_support_attachments to service_role;

create function public.np_support_attachment_operation(p_tenant uuid,p_installation uuid,p_ticket uuid,p_action text,
 p_attachment_id uuid,p_name text,p_content_type text,p_sha256 text,p_data_base64 text)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare t public.np_support_tickets; a public.np_support_attachments; bytes bytea; result jsonb;
begin
 select * into t from public.np_support_tickets where id=p_ticket and tenant_id=p_tenant and installation_id=p_installation for update;
 if not found then return jsonb_build_object('ok',false,'code','not_found'); end if;
 if p_action not in ('upload','download','list') or p_action is null then return jsonb_build_object('ok',false,'code','invalid_payload'); end if;
 if p_action='upload' then
  if p_attachment_id is null or p_data_base64 is null or length(p_data_base64)>1398104 then
   return jsonb_build_object('ok',false,'code','invalid_payload'); end if;
  begin bytes:=decode(p_data_base64,'base64'); exception when invalid_parameter_value then
   return jsonb_build_object('ok',false,'code','invalid_payload'); end;
  if octet_length(bytes) not between 12 and 1048576 or p_name is null or length(p_name)>120
   or p_name !~ '^[A-Za-z0-9_][A-Za-z0-9_. ()-]*$' or position('..' in p_name)>0
   or p_name<>btrim(p_name) or p_name ~* '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])\.'
   or p_sha256 is distinct from encode(extensions.digest(bytes,'sha256'),'hex')
   or not coalesce((p_content_type='image/png' and lower(p_name) like '%.png' and substring(bytes from 1 for 8)=decode('89504e470d0a1a0a','hex'))
    or (p_content_type='image/jpeg' and lower(p_name) ~ '\.(jpg|jpeg)$' and substring(bytes from 1 for 3)=decode('ffd8ff','hex'))
    or (p_content_type='image/webp' and lower(p_name) like '%.webp' and substring(bytes from 1 for 4)=decode('52494646','hex')
     and substring(bytes from 9 for 4)=decode('57454250','hex')),false) then
    return jsonb_build_object('ok',false,'code','invalid_payload'); end if;
  if (p_content_type='image/png' and (octet_length(bytes)<45 or substring(bytes from 9 for 8)<>decode('0000000d49484452','hex')
    or substring(bytes from octet_length(bytes)-11 for 12)<>decode('0000000049454e44ae426082','hex')))
   or (p_content_type='image/jpeg' and substring(bytes from octet_length(bytes)-1 for 2)<>decode('ffd9','hex'))
   or (p_content_type='image/webp' and (octet_length(bytes)<20 or substring(bytes from 13 for 4) not in
       (decode('56503820','hex'),decode('5650384c','hex'),decode('56503858','hex')))) then
    return jsonb_build_object('ok',false,'code','invalid_payload'); end if;
  select * into a from public.np_support_attachments where id=p_attachment_id;
  if found then
   if a.tenant_id<>p_tenant or a.installation_id<>p_installation or a.ticket_id<>p_ticket or a.sha256<>p_sha256
    or a.name<>p_name or a.content_type<>p_content_type then return jsonb_build_object('ok',false,'code','conflict'); end if;
  else
   if (select count(*) from public.np_support_attachments where ticket_id=t.id)>=3 then
    return jsonb_build_object('ok',false,'code','attachment_limit'); end if;
   insert into public.np_support_attachments(id,tenant_id,installation_id,ticket_id,name,content_type,sha256,data)
    values(p_attachment_id,p_tenant,p_installation,p_ticket,p_name,p_content_type,p_sha256,bytes);
  end if;
 end if;
 select coalesce(jsonb_agg(jsonb_build_object('id',x.id,'name',x.name,'content_type',x.content_type,'sha256',x.sha256,
  'size_bytes',octet_length(x.data),'data_base64',case when p_action='download' then replace(encode(x.data,'base64'),E'\n','') else '' end,
  'created_at',x.created_at) order by x.created_at,x.id),'[]'::jsonb) into result
 from public.np_support_attachments x where x.ticket_id=t.id and x.tenant_id=p_tenant and x.installation_id=p_installation
 and (p_action='list' or x.id=p_attachment_id);
 if p_action='download' and jsonb_array_length(result)=0 then return jsonb_build_object('ok',false,'code','not_found'); end if;
 return jsonb_build_object('ok',true,'attachments',result);
end; $$;

create function public.np_erp_support_attachment(p_tenant_id text,p_installation_id text,p_secret_hash text,p_nonce_hash text,p_sent_at timestamptz,p_input jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare authorized jsonb; t public.np_support_tickets;
begin
 if p_input->>'tenant_id' is distinct from p_tenant_id or p_input->>'installation_id' is distinct from p_installation_id then
  return jsonb_build_object('ok',false,'code','unauthorized'); end if;
 authorized:=public.np_erp_support(p_tenant_id,p_installation_id,p_secret_hash,p_nonce_hash,p_sent_at,
  jsonb_build_object('schema_version',1,'tenant_id',p_tenant_id,'installation_id',p_installation_id,'action','detail',
   'ticket_id',p_input->>'ticket_id','operation_id','','body','','status',''));
 if authorized->>'ok'<>'true' then return authorized; end if;
 select ticket.* into t from public.np_support_tickets ticket join public.np_installations i on i.id=ticket.installation_id
 join public.np_tenants tenant on tenant.id=ticket.tenant_id where i.installation_key=p_installation_id
 and tenant.tenant_key=p_tenant_id and ticket.source_ticket_id=p_input->>'ticket_id';
 return public.np_support_attachment_operation(t.tenant_id,t.installation_id,t.id,p_input->>'action',
 nullif(p_input->>'attachment_id','')::uuid,p_input->>'name',p_input->>'content_type',p_input->>'sha256',p_input->>'data_base64');
end; $$;

create function public.np_admin_support_attachment(p_actor_id uuid,p_ticket_id uuid,p_action text,p_attachment_id uuid,
 p_name text default '',p_content_type text default '',p_sha256 text default '',p_data_base64 text default '')
returns jsonb language plpgsql security definer set search_path=pg_catalog as $$
declare t public.np_support_tickets;
begin
 if not exists(select 1 from public.np_platform_users where id=p_actor_id and active and role in ('platform_admin','nexpoint_control_admin')) then
  return jsonb_build_object('ok',false,'code','unauthorized'); end if;
 select * into t from public.np_support_tickets where id=p_ticket_id;
 if not found then return jsonb_build_object('ok',false,'code','not_found'); end if;
 return public.np_support_attachment_operation(t.tenant_id,t.installation_id,t.id,p_action,p_attachment_id,p_name,p_content_type,p_sha256,p_data_base64);
end; $$;
revoke all on function public.np_support_attachment_operation(uuid,uuid,uuid,text,uuid,text,text,text,text),
 public.np_erp_support_attachment(text,text,text,text,timestamptz,jsonb),
 public.np_admin_support_attachment(uuid,uuid,text,uuid,text,text,text,text) from public,anon,authenticated;
grant execute on function public.np_support_attachment_operation(uuid,uuid,uuid,text,uuid,text,text,text,text),
 public.np_erp_support_attachment(text,text,text,text,timestamptz,jsonb),
 public.np_admin_support_attachment(uuid,uuid,text,uuid,text,text,text,text) to service_role;
