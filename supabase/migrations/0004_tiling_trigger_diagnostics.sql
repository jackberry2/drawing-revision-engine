-- Passive data collection for the tiling proposal in
-- docs/tiled_analysis_findings.md §3a. Every real (non-dry-run) analysis
-- computes whether that document's tiling trigger rule *would* fire
-- against its detect/detect_single output and logs the result here,
-- regardless of whether tiling itself is ever built. This is exactly the
-- real-sheet population needed to move the rule from "directionally
-- validated on 2 sheets" to something trustworthy — collecting it now
-- costs nothing beyond what detect already computes.

alter table public.pipeline_runs
    add column if not exists tiling_trigger_diagnostics jsonb;
