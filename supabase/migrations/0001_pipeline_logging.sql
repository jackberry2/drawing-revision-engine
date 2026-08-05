-- Proprietary pipeline logging tables for the drawing-revision-engine service.
--
-- These are additive only — nothing here alters drawings, analysis_requests,
-- or flagged_changes. pipeline_change_events links back to flagged_changes
-- so that a human correction recorded against the row a reviewer actually
-- sees (in flagged_changes / human_reviews) can be traced back to the full
-- internal reasoning (category taxonomy, bundling, confidence breakdown)
-- that produced it. That link is what turns this into a fine-tuning dataset
-- later.

create table if not exists public.pipeline_runs (
    id uuid primary key default gen_random_uuid(),
    analysis_request_id uuid references public.analysis_requests(id) on delete set null,
    old_drawing_id uuid references public.drawings(id) on delete set null,
    new_drawing_id uuid references public.drawings(id) on delete set null,
    status text not null default 'running' check (status in ('running', 'completed', 'failed')),
    created_at timestamptz not null default now()
);

create index if not exists pipeline_runs_analysis_request_id_idx
    on public.pipeline_runs (analysis_request_id);

-- Generic, stage-agnostic step log: every stage (today's prompted Claude
-- calls, or a custom-trained model later) writes the same shape here.
create table if not exists public.pipeline_steps (
    id uuid primary key default gen_random_uuid(),
    run_id uuid not null references public.pipeline_runs(id) on delete cascade,
    step_name text not null,
    step_order integer not null,
    model_used text,
    prompt_version text,
    input_json jsonb not null,
    output_json jsonb not null,
    latency_ms integer,
    created_at timestamptz not null default now()
);

create index if not exists pipeline_steps_run_id_idx
    on public.pipeline_steps (run_id);

-- The rich internal reasoning behind one flagged_changes row: category
-- taxonomy (finer-grained than flagged_changes.change_type), which raw
-- classified changes got bundled together, downstream implications, and
-- the confidence factor breakdown. flagged_changes itself only carries the
-- final mapped/simplified fields.
create table if not exists public.pipeline_change_events (
    id uuid primary key default gen_random_uuid(),
    run_id uuid not null references public.pipeline_runs(id) on delete cascade,
    flagged_change_id uuid references public.flagged_changes(id) on delete set null,
    category text not null,
    root_cause_summary text not null,
    bundled_change_ids jsonb not null default '[]'::jsonb,
    downstream_implications jsonb not null default '[]'::jsonb,
    schedule_corroboration text,
    confidence_score double precision not null,
    confidence_rationale jsonb not null,
    created_at timestamptz not null default now()
);

create index if not exists pipeline_change_events_run_id_idx
    on public.pipeline_change_events (run_id);
create index if not exists pipeline_change_events_flagged_change_id_idx
    on public.pipeline_change_events (flagged_change_id);

-- Human-in-the-loop correction capture, recorded against the exact
-- flagged_changes row a reviewer confirmed or corrected. This is the future
-- fine-tuning dataset.
create table if not exists public.human_reviews (
    id uuid primary key default gen_random_uuid(),
    flagged_change_id uuid not null references public.flagged_changes(id) on delete cascade,
    run_id uuid references public.pipeline_runs(id) on delete set null,
    reviewer text not null,
    verdict text not null check (verdict in ('confirmed', 'corrected', 'false_positive')),
    corrected_change_type text,
    corrected_description text,
    corrected_confidence_percentage integer,
    notes text,
    reviewed_at timestamptz not null default now()
);

create index if not exists human_reviews_flagged_change_id_idx
    on public.human_reviews (flagged_change_id);
