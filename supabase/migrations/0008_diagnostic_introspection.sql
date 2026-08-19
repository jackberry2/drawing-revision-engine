-- Temporary, service_role-only introspection helpers. Not application
-- logic — these exist only so the backend (which has no direct Postgres
-- connection, only the PostgREST data API) can read real RLS policy text
-- and real function definitions instead of guessing. Safe to drop once
-- used: locked to service_role via REVOKE/GRANT below, so no exposure
-- risk to anon/authenticated in the meantime either way.

create or replace function public.dre_introspect_policies(_table_names text[])
returns table (
    schemaname text,
    tablename text,
    policyname text,
    permissive text,
    roles text[],
    cmd text,
    qual text,
    with_check text
)
language sql
security definer
set search_path = public, pg_catalog
as $$
    select schemaname, tablename, policyname, permissive, roles, cmd,
           qual::text, with_check::text
    from pg_policies
    where tablename = any(_table_names)
$$;

create or replace function public.dre_introspect_rls_enabled(_table_names text[])
returns table (tablename text, rowsecurity boolean)
language sql
security definer
set search_path = public, pg_catalog
as $$
    select c.relname, c.relrowsecurity
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relname = any(_table_names)
$$;

create or replace function public.dre_introspect_function_source(_function_names text[])
returns table (function_name text, source text)
language sql
security definer
set search_path = public, pg_catalog
as $$
    select p.proname, pg_get_functiondef(p.oid)
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.proname = any(_function_names)
$$;

revoke all on function public.dre_introspect_policies(text[]) from public, anon, authenticated;
revoke all on function public.dre_introspect_rls_enabled(text[]) from public, anon, authenticated;
revoke all on function public.dre_introspect_function_source(text[]) from public, anon, authenticated;
grant execute on function public.dre_introspect_policies(text[]) to service_role;
grant execute on function public.dre_introspect_rls_enabled(text[]) to service_role;
grant execute on function public.dre_introspect_function_source(text[]) to service_role;
