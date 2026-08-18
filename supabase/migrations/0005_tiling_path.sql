-- First real production wiring of docs/tiled_analysis_findings.md's tiling
-- design: §3a's trigger rule (already live as passive logging via
-- tiling_trigger_diagnostics, migration 0004) is now actually acted on for
-- single_sheet-mode requests. This column records which path a given run
-- actually took, so production traffic can be inspected for how often
-- tiling fires and whether it ever falls back after starting.
--
-- Values: 'single_pass' (rule didn't fire, or fired but the source wasn't
-- a tileable PDF), 'tiled' (rule fired and the tiled path completed),
-- 'tiled_failed_fallback' (rule fired, tiling was attempted, but it threw
-- and the original single-pass result was kept rather than failing the
-- whole request), 'not_applicable' (two_image mode — tiling is
-- single_sheet-only for now, see §3c/§5).

alter table public.pipeline_runs
    add column if not exists tiling_path text;
