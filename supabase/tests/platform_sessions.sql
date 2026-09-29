-- Run only against a disposable QA database after all official migrations.
-- Every fixture and temporary privilege change is rolled back.
\set ON_ERROR_STOP on

begin;

create extension if not exists pgtap with schema extensions;
select extensions.plan(1);

do $$
declare
    actor uuid := extensions.gen_random_uuid();
    other_actor uuid := extensions.gen_random_uuid();
    role_name text;
    privilege_name text;
    affected integer;
begin
    if not exists (
        select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace
         where n.nspname = 'public' and c.relname = 'np_platform_sessions'
           and c.relrowsecurity and c.relforcerowsecurity
    ) then
        raise exception 'session table must enable and force RLS';
    end if;
    if exists (
        select 1 from pg_class c
        cross join lateral aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
         where c.oid = 'public.np_platform_sessions'::regclass and a.grantee = 0
    ) then
        raise exception 'PUBLIC must have no session table privileges';
    end if;
    foreach role_name in array array['anon', 'authenticated'] loop
        foreach privilege_name in array array[
            'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'
        ] loop
            if has_table_privilege(role_name, 'public.np_platform_sessions', privilege_name) then
                raise exception 'client role received session table privilege';
            end if;
        end loop;
    end loop;
    foreach privilege_name in array array['SELECT', 'INSERT', 'DELETE'] loop
        if not has_table_privilege('service_role', 'public.np_platform_sessions', privilege_name) then
            raise exception 'service role lacks required session privilege';
        end if;
    end loop;
    foreach privilege_name in array array['UPDATE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'] loop
        if has_table_privilege('service_role', 'public.np_platform_sessions', privilege_name) then
            raise exception 'service role has unnecessary session privilege';
        end if;
    end loop;

    insert into public.np_platform_users(id, username, display_name, role, password_hash)
    values
        (actor, 'session-qa-' || replace(actor::text, '-', ''), 'Session QA',
         'platform_admin', 'scrypt$' || repeat('x', 64)),
        (other_actor, 'session-qa-' || replace(other_actor::text, '-', ''), 'Session QA B',
         'nexpoint_control_admin', 'scrypt$' || repeat('y', 64));

    set local role service_role;
    insert into public.np_platform_sessions(token_hash, user_id, created_at, expires_at)
    values (repeat('a', 64), actor, now(), now() + interval '8 hours'),
           (repeat('b', 64), actor, now(), now() + interval '8 hours'),
           (repeat('c', 64), actor, now() - interval '9 hours', now() - interval '1 hour');
    if (select count(*) from public.np_platform_sessions
         where user_id = actor and expires_at > now()) <> 2 then
        raise exception 'session expiry filter did not exclude expired fixture';
    end if;
    delete from public.np_platform_sessions
     where user_id = other_actor and token_hash = repeat('a', 64);
    get diagnostics affected = row_count;
    if affected <> 0 then
        raise exception 'wrong-user revocation removed a session';
    end if;
    delete from public.np_platform_sessions
     where user_id = actor and token_hash = repeat('a', 64);
    get diagnostics affected = row_count;
    if affected <> 1 or not exists (
        select 1 from public.np_platform_sessions
         where user_id = actor and token_hash = repeat('b', 64)
    ) then
        raise exception 'revocation must preserve other sessions';
    end if;
    reset role;

    foreach role_name in array array['anon', 'authenticated'] loop
        execute format('set local role %I', role_name);
        begin
            perform token_hash from public.np_platform_sessions;
            raise exception 'client SELECT unexpectedly allowed';
        exception when insufficient_privilege then null;
        end;
        begin
            insert into public.np_platform_sessions(token_hash, user_id, created_at, expires_at)
            values (repeat('d', 64), actor, now(), now() + interval '8 hours');
            raise exception 'client INSERT unexpectedly allowed';
        exception when insufficient_privilege then null;
        end;
        begin
            delete from public.np_platform_sessions where user_id = actor;
            raise exception 'client DELETE unexpectedly allowed';
        exception when insufficient_privilege then null;
        end;
        reset role;
    end loop;

    -- Defense in depth: a later accidental SELECT grant still reveals no rows.
    grant select on public.np_platform_sessions to authenticated;
    set local role authenticated;
    if exists (select 1 from public.np_platform_sessions) then
        raise exception 'RLS exposed a session after temporary SELECT grant';
    end if;
    reset role;
    revoke select on public.np_platform_sessions from authenticated;

    begin
        insert into public.np_platform_sessions(token_hash, user_id, created_at, expires_at)
        values (repeat('g', 64), actor, now(), now() + interval '8 hours');
        raise exception 'malformed hash unexpectedly accepted';
    exception when check_violation then null;
    end;
    begin
        insert into public.np_platform_sessions(token_hash, user_id, created_at, expires_at)
        values (repeat('d', 64), actor, now(), now());
        raise exception 'nonpositive lifetime unexpectedly accepted';
    exception when check_violation then null;
    end;
    begin
        insert into public.np_platform_sessions(token_hash, user_id, created_at, expires_at)
        values (repeat('e', 64), extensions.gen_random_uuid(), now(), now() + interval '8 hours');
        raise exception 'missing session user unexpectedly accepted';
    exception when foreign_key_violation then null;
    end;
    delete from public.np_platform_users where id = actor;
    if exists (select 1 from public.np_platform_sessions where user_id = actor) then
        raise exception 'deleted user left orphaned sessions';
    end if;
end;
$$;

select extensions.pass('Persistent platform session ownership, expiry, constraints and RLS verified');
select * from extensions.finish();
rollback;
