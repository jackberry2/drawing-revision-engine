"""First real production wiring of docs/tiled_analysis_findings.md's tiling
design: given the raw PDF bytes for a single-sheet analysis, runs the full
trigger→grid→per-tile-detect→merge pipeline built and validated as a
harness (`dre.tiling_trigger`, `dre.tiling`, `dre.pipeline.tile_tuning`,
`dre.pipeline.tile_merge`) and produces one consolidated
`SingleSheetDetectResult` — the same shape `DetectSingleStep` itself
produces, so it can drop straight into `ctx.detect_single_result` and let
classify/reason_single/confidence/describe run unchanged (§3d).

Single-sheet mode only. Two-image tiling (`DetectStep`/`RawDetection`) has
no design or validation behind it — see docs/tiled_analysis_findings.md §5.

150 DPI is the validated default (§2/§5: resolved every known failure on
both real sheets tested) — kept as a parameter, not hardcoded elsewhere,
for the same reason `compute_tile_grid` never defaults `target_dpi`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel

from dre.imaging import is_pdf
from dre.models.schemas import ExtractedTable, SingleSheetDetectResult, SingleSheetDetection
from dre.pipeline.base import PipelineContext
from dre.pipeline.tile_merge import (
    TiledDetection,
    filter_detections_by_cloud_proximity,
    group_merge_candidates,
)
from dre.pipeline.tile_tuning import run_detect_single_on_grid
from dre.tiling_trigger import compute_tiling_trigger_diagnostics

TILED_DETECT_DPI = 150.0

# Defensive backstop for docs/tiled_analysis_findings.md §5/§3g's confirmed
# real classify breaking point: a real (truncated) 54-detection set from
# E-101.2 succeeded at classify through n=50 and failed at n=54 — a narrow,
# specific ceiling, not an estimate. detect_single.md's hexagon-vs-triangle
# fix (§3g) is the primary defense and dramatically reduces volume in
# practice (a fresh real run went 79 -> 15 total detections after both the
# prompt fix and the cloud-proximity filter), but that's one run against
# one sheet - real LLM output varies run to run, and this exact sheet
# organically produced 54 once already before the fix existed. This cap
# exists so a future unlucky run (or a genuinely busier real sheet) can't
# silently break classify again the same way, independent of how well the
# upstream fixes perform. 40 leaves real margin below the confirmed 50/54
# boundary, not a value tuned precisely to it.
CLASSIFY_DETECTION_CAP = 40

# Trust ordering reused from filter_detections_by_cloud_proximity's own
# hierarchy (revision_cloud = hardest to fake, unmarked = already
# self-hedged by the model) - not a new heuristic invented for this cap,
# the same one already governing which detections this pipeline trusts
# most. Lower rank = kept first when the cap has to drop something.
_FLAGGED_BY_SIGNAL_RANK = {
    "revision_cloud": 0,
    "revision_tag": 1,
    "annotation_note": 2,
    "unmarked": 3,
}


class VolumeCapDiagnostics(BaseModel):
    pre_cap_count: int
    post_cap_count: int
    cap: int
    applied: bool


def _cap_detection_volume(
    detections: list[SingleSheetDetection], *, cap: int = CLASSIFY_DETECTION_CAP
) -> tuple[list[SingleSheetDetection], VolumeCapDiagnostics]:
    """Drops lowest-signal excess rather than chunking classify into
    multiple calls — simpler, bounded real API cost regardless of how
    large the raw volume gets, and consistent with the safer-direction,
    under-inclusion-over-false-confidence bias used everywhere else in
    this design (§3c's merge threshold, the cloud-proximity filter).
    Chunking was the sketched alternative — rejected here specifically
    because it doesn't actually cap anything (cost still scales with raw
    volume, just spread across more calls) and adds cross-batch merge
    complexity for a backstop that's meant to be simple.

    Ranks by `_FLAGGED_BY_SIGNAL_RANK`, keeping original relative order
    within each rank (stable), then keeps the first `cap` — so surviving
    detections still appear in the same relative order they were merged
    in, not resorted."""
    pre_cap_count = len(detections)
    if pre_cap_count <= cap:
        return detections, VolumeCapDiagnostics(
            pre_cap_count=pre_cap_count, post_cap_count=pre_cap_count, cap=cap, applied=False
        )

    ranked = sorted(
        enumerate(detections), key=lambda pair: (_FLAGGED_BY_SIGNAL_RANK.get(pair[1].flagged_by, 99), pair[0])
    )
    kept_indices = {i for i, _ in ranked[:cap]}
    kept = [d for i, d in enumerate(detections) if i in kept_indices]
    return kept, VolumeCapDiagnostics(
        pre_cap_count=pre_cap_count, post_cap_count=len(kept), cap=cap, applied=True
    )


def sheet_dimensions_in_inches(pdf_bytes: bytes) -> tuple[float, float]:
    """Real sheet size from the PDF's own first page — never assumed or
    passed in by a caller, so the grid always matches the actual source,
    not a guessed sheet format (ARCH D/E/letter)."""
    import fitz  # PyMuPDF — imported lazily, only needed when tiling actually runs

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc[0]
        return page.rect.width / 72, page.rect.height / 72


def _merge_group(group: list[TiledDetection], *, next_id: int) -> SingleSheetDetection:
    """One `TiledDetection` group (from `tile_merge.group_merge_candidates`)
    -> one `SingleSheetDetection`. A singleton keeps its own content as-is
    but still gets a fresh id (see `merge_tiled_detections`'s docstring on
    why ids can't survive from per-tile detect_single calls unchanged). A
    multi-member group's description concatenates each member's own text
    rather than trying to invent one unified description — no attempt was
    ever made to have a model re-describe a merged element, so the merged
    detection's text stays traceable back to what each tile actually saw.
    `region` is deliberately dropped on a merge: per-tile self-reported
    regions are known-unreliable even independently agreeing across runs
    (docs/tiled_analysis_findings.md §3c) — carrying one forward on a
    multi-tile merge would imply a confidence in tile-local coordinates
    this design explicitly refuses to trust. A singleton keeps its own
    region, since it isn't being reinterpreted across tiles."""
    primary = group[0].detection
    detection_id = f"tile-merged-{next_id}"
    if len(group) == 1:
        return primary.model_copy(update={"id": detection_id})

    seen_descriptions: set[str] = set()
    descriptions = []
    for td in group:
        d = td.detection.geometry_description
        if d not in seen_descriptions:
            descriptions.append(d)
            seen_descriptions.add(d)
    merged_description = (
        f"[Merged across {len(group)} tiles, content-matched — see "
        "docs/tiled_analysis_findings.md §3c] " + " | ".join(descriptions)
    )
    return SingleSheetDetection(
        id=detection_id,
        sheet_ref=primary.sheet_ref,
        flagged_by=primary.flagged_by,
        region=None,
        geometry_description=merged_description,
    )


def _dedup_tables(tiled_tables: list[ExtractedTable], *, next_id_start: int) -> list[ExtractedTable]:
    """Naive title-based dedup across tiles — a table whose title repeats
    across tiles (the same schedule spanning several tile columns) keeps
    only its first occurrence. NOT a real cross-tile row-merge: if the
    table's rows actually differ per tile (e.g. each tile only saw the
    columns physically within it), this silently keeps an incomplete one.
    Genuinely undesigned — see docs/tiled_analysis_findings.md §3c/§5 —
    kept this simple deliberately rather than inventing an unvalidated
    row-merge here."""
    seen_titles: set[str] = set()
    result = []
    next_id = next_id_start
    for t in tiled_tables:
        key = t.title or f"__untitled_{next_id}"
        if key in seen_titles:
            continue
        seen_titles.add(key)
        result.append(t.model_copy(update={"id": f"tile-table-{next_id}"}))
        next_id += 1
    return result


def merge_tiled_detections(
    tiled_detections: list[TiledDetection], extracted_tables: list[ExtractedTable]
) -> tuple[SingleSheetDetectResult, VolumeCapDiagnostics]:
    """Pure merge step, split out from `run_tiled_detect_and_merge` so it's
    directly testable without a real PDF/API call — same reasoning as
    keeping `tiling.py`/`tile_merge.py` pure.

    Reassigns every detection's and table's `id` rather than keeping the
    ids `detect_single` returned per-tile: those ids are only unique
    *within one tile's own response* (each tile is an independent Claude
    call, e.g. tile (0,0) and tile (2,1) can both legitimately return an
    element with id "det1"). Carrying colliding ids into one merged result
    would make `classify`'s `raw_detection_id` references genuinely
    ambiguous — a real traceability bug, not just a cosmetic one — so
    every detection in the merged output gets a fresh, guaranteed-unique
    id instead.

    First applies `filter_detections_by_cloud_proximity` (docs/tiled_
    analysis_findings.md §5) — a real full-grid production run against
    E-101.2 produced 79 raw detections, 92% of them `revision_tag` items
    describing hexagonal keyed-note tags misapplied with a category
    detect_single.md itself defines as triangular, which broke classify
    3 times in a row on the resulting volume. Filtering implausible
    per-tile detections before merge/grouping, not after, keeps the
    volume classify actually receives bounded from the start.

    Then applies `_cap_detection_volume` as a defensive backstop, on top
    of that filter and detect_single.md's own hexagon-vs-triangle fix
    (§3g) — not instead of them. Both real fixes dramatically reduce
    volume in practice, but that's evidence from individual real runs, not
    a guarantee for every future one; the cap protects against the exact,
    now-precisely-known failure mode (classify breaks between n=50 and
    n=54) regardless of how well the upstream fixes perform on any given
    run. Returns the diagnostics alongside the result so the caller can
    log whether the cap actually did anything on this run — see
    `dre.service`'s `tile_merge` step log."""
    tiled_detections = filter_detections_by_cloud_proximity(tiled_detections)
    groups = group_merge_candidates(tiled_detections)
    merged_detections = [_merge_group(g, next_id=i) for i, g in enumerate(groups, start=1)]
    merged_detections, cap_diagnostics = _cap_detection_volume(merged_detections)
    merged_tables = _dedup_tables(extracted_tables, next_id_start=1)
    result = SingleSheetDetectResult(detections=merged_detections, extracted_tables=merged_tables)
    return result, cap_diagnostics


def run_tiled_detect_and_merge(
    pdf_bytes: bytes, *, dpi: float = TILED_DETECT_DPI
) -> tuple[SingleSheetDetectResult, VolumeCapDiagnostics]:
    """The real production entry point: full sheet PDF bytes in, one merged
    `SingleSheetDetectResult` out — ready to replace
    `ctx.detect_single_result`. Sequential per-tile calls (§3f's parallel
    execution is a separate, not-yet-built piece — see
    docs/tiled_analysis_findings.md §5's end-to-end status note)."""
    sheet_width_in, sheet_height_in = sheet_dimensions_in_inches(pdf_bytes)
    grid_result = run_detect_single_on_grid(
        pdf_bytes, sheet_width_in=sheet_width_in, sheet_height_in=sheet_height_in, dpi=dpi
    )
    return merge_tiled_detections(grid_result.tiled_detections, grid_result.extracted_tables)


@dataclass(frozen=True)
class TilingOutcome:
    # "single_pass" (rule didn't fire), "single_pass_no_pdf_source" (rule
    # fired but the drawing isn't a tileable PDF — tiling only supports PDF
    # sources today, see module docstring), "tiled" (rule fired, tiling
    # completed, ctx.detect_single_result was replaced), or
    # "tiled_failed_fallback" (rule fired, tiling was attempted and threw,
    # original single-pass ctx.detect_single_result was left in place).
    path: str
    trigger_diagnostics: dict
    # Only populated when path == "tiled" — None otherwise, since capping
    # is meaningless when tiling never ran.
    volume_cap_diagnostics: Optional[dict] = None


def decide_and_apply_tiling(
    ctx: PipelineContext, *, raw_pdf_bytes: Optional[bytes], dpi: float = TILED_DETECT_DPI
) -> TilingOutcome:
    """The real branch: checks docs/tiled_analysis_findings.md §3a's
    trigger rule (already live in production as passive logging, see
    dre.tiling_trigger) against the single-pass `ctx.detect_single_result`
    that was just computed, and — the first point this rule is actually
    acted on rather than only logged — replaces `ctx.detect_single_result`
    with the merged tiled result when it fires against a tileable source.

    Called via `Pipeline.run`'s `on_after_detect` hook, so `ctx.mode` is
    guaranteed 'single_sheet' by the caller (`dre.service`) before this
    runs — asserted here rather than silently no-oping, since a two_image
    call into this function would be a real wiring bug, not a case to
    handle quietly (two_image tiling doesn't exist — see module docstring).

    Never raises: a tiling failure mid-attempt (a real API error on some
    tile, a malformed PDF, anything) falls back to the already-computed
    single-pass result rather than failing the whole analysis request —
    tiling is a quality enhancement over an already-working single-pass
    path, not a new hard dependency."""
    assert ctx.mode == "single_sheet"
    assert ctx.detect_single_result is not None
    diagnostics = compute_tiling_trigger_diagnostics(ctx.detect_single_result)
    diagnostics_dict = diagnostics.model_dump(mode="json")

    if not diagnostics.would_trigger:
        return TilingOutcome(path="single_pass", trigger_diagnostics=diagnostics_dict)

    if raw_pdf_bytes is None or not is_pdf(raw_pdf_bytes):
        return TilingOutcome(
            path="single_pass_no_pdf_source", trigger_diagnostics=diagnostics_dict
        )

    try:
        tiled_result, cap_diagnostics = run_tiled_detect_and_merge(raw_pdf_bytes, dpi=dpi)
    except Exception:
        return TilingOutcome(path="tiled_failed_fallback", trigger_diagnostics=diagnostics_dict)

    ctx.detect_single_result = tiled_result
    return TilingOutcome(
        path="tiled",
        trigger_diagnostics=diagnostics_dict,
        volume_cap_diagnostics=cap_diagnostics.model_dump(mode="json"),
    )
