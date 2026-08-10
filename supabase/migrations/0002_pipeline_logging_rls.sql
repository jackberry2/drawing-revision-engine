-- Admin-only read access to the pipeline logging tables added in
-- 0001_pipeline_logging.sql. This app's service role key (used by the
-- FastAPI service) bypasses RLS entirely, so these policies only govern
-- access through the Supabase dashboard / anon+authenticated API clients:
-- an admin (per is_scope_admin()) can read; no regular authenticated user
-- can query these tables at all, since no policy exists for them.

alter table public.pipeline_runs enable row level security;
alter table public.pipeline_steps enable row level security;
alter table public.pipeline_change_events enable row level security;
alter table public.human_reviews enable row level security;

create policy "admin_read_pipeline_runs"
    on public.pipeline_runs
    for select
    using (is_scope_admin());

create policy "admin_read_pipeline_steps"
    on public.pipeline_steps
    for select
    using (is_scope_admin());

create policy "admin_read_pipeline_change_events"
    on public.pipeline_change_events
    for select
    using (is_scope_admin());

create policy "admin_read_human_reviews"
    on public.human_reviews
    for select
    using (is_scope_admin());
