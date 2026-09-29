-- SD-001: durable per-session revocation. Apply in QA first; no automatic PROD rollout.
begin;

create table if not exists public.np_platform_sessions (
    token_hash text primary key check (token_hash ~ '^[0-9a-f]{64}$'),
    user_id uuid not null references public.np_platform_users(id) on delete cascade,
    created_at timestamptz not null default now(),
    expires_at timestamptz not null,
    constraint np_platform_session_lifetime check (
        expires_at > created_at and expires_at <= created_at + interval '8 hours'
    )
);
create index if not exists np_platform_sessions_expiry_idx
    on public.np_platform_sessions(expires_at);

alter table public.np_platform_sessions enable row level security;
alter table public.np_platform_sessions force row level security;
revoke all on public.np_platform_sessions from public, anon, authenticated, service_role;
grant select, insert, delete on public.np_platform_sessions to service_role;
-- No browser policies. Token plaintext is never persisted. Expired rows are
-- removed during server-side login; absence always means access denied.

commit;
