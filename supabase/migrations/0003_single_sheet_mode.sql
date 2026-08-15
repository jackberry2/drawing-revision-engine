-- Adds single-sheet mode support: analyses that have only one drawing (a
-- sheet already carrying the drafter's own revision clouds/tags) instead of
-- a paired old/new revision.
--
-- analysis_requests.old_drawing_id stays NOT NULL and holds the single
-- sheet in single-sheet mode (kept as the required field to avoid touching
-- its existing constraint); new_drawing_id becomes nullable and is NULL
-- whenever mode = 'single_sheet'. Existing rows default to mode =
-- 'two_image', matching their current (only) behavior.

alter table public.analysis_requests
    alter column new_drawing_id drop not null;

alter table public.analysis_requests
    add column if not exists mode text not null default 'two_image'
        check (mode in ('two_image', 'single_sheet'));

-- Proprietary logging tables (ours, from 0001) get the same mode tag plus
-- room for the single-sheet-specific confidence/reasoning fields described
-- in docs/single_sheet_mode_findings.md.

alter table public.pipeline_runs
    add column if not exists mode text not null default 'two_image'
        check (mode in ('two_image', 'single_sheet'));

alter table public.pipeline_change_events
    add column if not exists schedule_consistency text;

alter table public.pipeline_change_events
    add column if not exists identity_unresolved boolean not null default false;
