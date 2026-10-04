-- Separate migration: PostgreSQL enum value must commit before ingestion uses it.
alter type public.np_sync_kind add value if not exists 'company_profile';
