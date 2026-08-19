"""Covers dre.pipeline.tiled_detect — the first real production wiring of
docs/tiled_analysis_findings.md's tiling design: `decide_and_apply_tiling`
(the branch itself, called via Pipeline.run's on_after_detect hook) and
`merge_tiled_detections` (the pure assembly step it delegates to once
tiling actually runs).

Uses the same real E-101.2/E-101.3 detection text already validated in
tests/test_tile_merge.py and tests/test_tiling_trigger.py, not synthetic
data, for continuity with how every other piece of this design has been
tested."""

from pathlib import Path
from unittest.mock import patch

import pytest

from dre.models.schemas import ExtractedTable, SingleSheetDetectResult, SingleSheetDetection
from dre.pipeline.base import PipelineContext
from dre.pipeline.tile_merge import TiledDetection
from dre.pipeline.tiled_detect import (
    CLASSIFY_DETECTION_CAP,
    DEFAULT_SINGLE_PASS_ESTIMATE_SECONDS,
    DEFAULT_TILED_ESTIMATE_SECONDS,
    VolumeCapDiagnostics,
    _cap_detection_volume,
    decide_and_apply_tiling,
    estimate_analysis_duration,
    merge_tiled_detections,
    sheet_dimensions_in_inches,
)

# --- Real E-101.2 detections (see tests/test_tile_merge.py for the full
# provenance note) - the real negative case: two distinct "B5"-tagged
# clouds in different rooms must NOT merge. ---
_E101_2_CLOUD_1 = SingleSheetDetection(
    id="cloud1",
    flagged_by="revision_cloud",
    geometry_description=(
        "Vertical rectangular red dashed cloud outline enclosing a run of light "
        "fixture symbols (circles labeled R2, R2 EM, R2 EM) between two vertical "
        "bars, with an EX1 exit-sign symbol at the bottom of the enclosed area. "
        "Red triangular tag with number '5' visible nearby but a separate red "
        "triangle tag labeled 'B5' is at the bottom of this same cloud."
    ),
)
_E101_2_CLOUD_2 = SingleSheetDetection(
    id="cloud1",  # same id as _E101_2_CLOUD_1's, by design - see the id-collision test below
    flagged_by="revision_cloud",
    geometry_description=(
        "Irregular rounded red dashed cloud outline enclosing two light fixture "
        "circle symbols labeled 'R6' near a hexagon tag numbered '5', located in "
        "a room area to the right of E124."
    ),
)


def _real_e101_3_no_trigger_result() -> SingleSheetDetectResult:
    """The exact real trace confirmed NOT to fire §3a's trigger rule (see
    tests/test_tiling_trigger.py's test_e101_3_real_trace_does_not_trigger)
    - 4 detections, 4 distinct table titles, quoted_fraction=0.5."""
    return SingleSheetDetectResult(
        detections=[
            SingleSheetDetection(
                id="det-1",
                flagged_by="revision_cloud",
                geometry_description=(
                    "Large red dashed-line rectangular cloud/border enclosing the "
                    "entire 'DOOR W/PANIC HARDWARE ACCESS CONTROL DOOR ELEVATION' "
                    "detail (labeled B/E101.3) in the top-left portion of the sheet."
                ),
            ),
            SingleSheetDetection(
                id="det-2",
                flagged_by="revision_tag",
                geometry_description=(
                    "Small triangular revision tag (delta symbol) with no visible "
                    "number, positioned at the top-left corner of the red dashed "
                    "cloud border."
                ),
            ),
            SingleSheetDetection(
                id="det-3",
                flagged_by="revision_cloud",
                geometry_description=(
                    "Red dashed-line rectangular cloud enclosing a floor plan area "
                    "labeled 'NORTH COMMUNICATIONS CONDUIT SERVICE ENTRANCE', "
                    "'SOUTH COMMUNICATIONS CONDUIT SERVICE ENTRANCE', and '4\" "
                    "CONDUIT STUBBED INTO BOILER ROOM'."
                ),
            ),
            SingleSheetDetection(
                id="det-4",
                flagged_by="revision_tag",
                geometry_description=(
                    "Small triangular revision tag (delta symbol) with no visible "
                    "number, located above the clouded floor-plan area near the "
                    "communications service entrance labels."
                ),
            ),
        ],
        extracted_tables=[
            ExtractedTable(id="tbl-1", table_type="other", sheet_version="new", title="GENERAL NOTES"),
            ExtractedTable(id="tbl-2", table_type="other", sheet_version="new", title="CODED NOTES"),
            ExtractedTable(
                id="tbl-3", table_type="legend", sheet_version="new", title="LINE TYPE LEGEND"
            ),
            ExtractedTable(
                id="tbl-4", table_type="other", sheet_version="new", title="ISSUANCE/REVISION LIST"
            ),
        ],
    )


def _triggering_result() -> SingleSheetDetectResult:
    """Below the low_n detection-count floor with zero tables extracted -
    would_trigger=True per §3a's low_n branch (see
    tests/test_tiling_trigger.py's equivalent case)."""
    return SingleSheetDetectResult(
        detections=[
            SingleSheetDetection(
                id="d1", flagged_by="revision_cloud", geometry_description="A symbol, no label."
            ),
        ],
        extracted_tables=[],
    )


# ---- sheet_dimensions_in_inches -------------------------------------------


def test_sheet_dimensions_in_inches_reads_the_real_pdf_page_size():
    import fitz

    # A real ARCH-D-ish 36"x24" sheet, at PDF's native 72pt/inch.
    doc = fitz.open()
    doc.new_page(width=36 * 72, height=24 * 72)
    pdf_bytes = doc.tobytes()
    doc.close()

    width_in, height_in = sheet_dimensions_in_inches(pdf_bytes)

    assert width_in == pytest.approx(36.0)
    assert height_in == pytest.approx(24.0)


# ---- merge_tiled_detections ----------------------------------------------


def test_merge_keeps_real_e101_2_clouds_separate_and_reassigns_ids():
    tiled = [
        TiledDetection(tile_row=1, tile_col=1, detection=_E101_2_CLOUD_1),
        TiledDetection(tile_row=1, tile_col=2, detection=_E101_2_CLOUD_2),
    ]
    result, cap_diagnostics = merge_tiled_detections(tiled, [])

    assert len(result.detections) == 2  # stayed separate, matching test_tile_merge.py's finding
    assert cap_diagnostics.applied is False  # well under the cap
    # The real bug this guards against: both source detections shared the
    # id "cloud1" (independent per-tile Claude calls can genuinely produce
    # colliding ids) - the merged output must not carry that collision
    # through, or classify's raw_detection_id references become ambiguous.
    ids = [d.id for d in result.detections]
    assert len(ids) == len(set(ids))


def test_merge_combines_a_multi_member_group_into_one_detection():
    tag = SingleSheetDetection(
        id="tag1",
        flagged_by="revision_tag",
        geometry_description="Red triangular tag containing text 'B5-2' and 'R2'.",
    )
    cloud = SingleSheetDetection(
        id="cloud1",
        flagged_by="revision_cloud",
        geometry_description="Cloud enclosing fixtures labeled 'B5-2' and 'R2'.",
    )
    tiled = [
        TiledDetection(tile_row=0, tile_col=0, detection=cloud),
        TiledDetection(tile_row=0, tile_col=1, detection=tag),
    ]
    result, _ = merge_tiled_detections(tiled, [])

    assert len(result.detections) == 1
    merged = result.detections[0]
    assert "Merged across 2 tiles" in merged.geometry_description
    assert merged.region is None  # per-tile regions aren't trusted merged - see §3c
    assert cloud.geometry_description in merged.geometry_description
    assert tag.geometry_description in merged.geometry_description


def test_merge_dedups_tables_by_title_across_tiles():
    tables = [
        ExtractedTable(id="a", table_type="other", sheet_version="new", title="GENERAL NOTES"),
        ExtractedTable(id="b", table_type="other", sheet_version="new", title="GENERAL NOTES"),
        ExtractedTable(id="c", table_type="other", sheet_version="new", title="CODED NOTES"),
    ]
    result, _ = merge_tiled_detections([], tables)

    assert sorted(t.title for t in result.extracted_tables) == ["CODED NOTES", "GENERAL NOTES"]


# ---- _cap_detection_volume: defensive backstop for the confirmed real
# classify breaking point (docs/tiled_analysis_findings.md §5/§3g: a real
# 54-detection set succeeded through n=50 and failed at n=54). ----------


def _fake_detection(i: int, flagged_by: str) -> SingleSheetDetection:
    return SingleSheetDetection(id=f"d{i}", flagged_by=flagged_by, geometry_description=f"item {i}")


def test_cap_is_a_no_op_under_the_limit():
    detections = [_fake_detection(i, "revision_tag") for i in range(10)]
    kept, diagnostics = _cap_detection_volume(detections, cap=40)

    assert kept == detections
    assert diagnostics == VolumeCapDiagnostics(
        pre_cap_count=10, post_cap_count=10, cap=40, applied=False
    )


def test_cap_drops_excess_and_reports_it():
    detections = [_fake_detection(i, "revision_tag") for i in range(50)]
    kept, diagnostics = _cap_detection_volume(detections, cap=40)

    assert len(kept) == 40
    assert diagnostics == VolumeCapDiagnostics(
        pre_cap_count=50, post_cap_count=40, cap=40, applied=True
    )


def test_cap_keeps_highest_trust_categories_first():
    """revision_cloud (hardest to fake) must survive over revision_tag
    (the real noise category, §3g) when the cap has to choose."""
    detections = (
        [_fake_detection(i, "revision_tag") for i in range(38)]
        + [_fake_detection(100 + i, "revision_cloud") for i in range(5)]
    )
    kept, diagnostics = _cap_detection_volume(detections, cap=40)

    assert diagnostics.applied is True
    kept_ids = {d.id for d in kept}
    # All 5 clouds must survive - only the lowest-trust tags get dropped.
    assert {"d100", "d101", "d102", "d103", "d104"} <= kept_ids
    assert len(kept) == 40


def test_cap_preserves_original_relative_order_of_survivors():
    detections = [_fake_detection(i, "revision_tag") for i in range(45)]
    kept, _ = _cap_detection_volume(detections, cap=40)

    assert [d.id for d in kept] == [f"d{i}" for i in range(40)]


def test_default_cap_constant_matches_the_documented_safe_margin():
    assert CLASSIFY_DETECTION_CAP == 40


# ---- decide_and_apply_tiling ---------------------------------------------


def test_no_op_when_rule_does_not_fire_real_e101_3_case():
    """The explicit regression test requested: a real known-non-triggering
    trace (E-101.3) must leave ctx.detect_single_result completely
    untouched and never attempt a tiled call."""
    original = _real_e101_3_no_trigger_result()
    ctx = PipelineContext(run_id="r1", old_image_path=Path(__file__), mode="single_sheet")
    ctx.detect_single_result = original

    with patch("dre.pipeline.tiled_detect.run_tiled_detect_and_merge") as mock_run:
        outcome = decide_and_apply_tiling(ctx, raw_pdf_bytes=b"%PDF-1.4 fake pdf bytes")

    mock_run.assert_not_called()
    assert outcome.path == "single_pass"
    assert ctx.detect_single_result is original  # strict no-op, not just equal


def test_falls_back_to_single_pass_when_source_is_not_a_pdf():
    original = _triggering_result()
    ctx = PipelineContext(run_id="r2", old_image_path=Path(__file__), mode="single_sheet")
    ctx.detect_single_result = original

    with patch("dre.pipeline.tiled_detect.run_tiled_detect_and_merge") as mock_run:
        outcome = decide_and_apply_tiling(ctx, raw_pdf_bytes=b"\x89PNG\r\n\x1a\nnot a pdf")

    mock_run.assert_not_called()
    assert outcome.path == "single_pass_no_pdf_source"
    assert ctx.detect_single_result is original


def test_falls_back_to_single_pass_when_raw_bytes_are_none():
    """A non-PDF Storage source (a plain image upload) means raw_pdf_bytes
    is never fetched by the caller — must degrade the same as an explicit
    non-PDF, not crash on a missing value."""
    original = _triggering_result()
    ctx = PipelineContext(run_id="r2b", old_image_path=Path(__file__), mode="single_sheet")
    ctx.detect_single_result = original

    outcome = decide_and_apply_tiling(ctx, raw_pdf_bytes=None)

    assert outcome.path == "single_pass_no_pdf_source"
    assert ctx.detect_single_result is original


def test_replaces_detect_single_result_when_rule_fires_against_a_pdf():
    original = _triggering_result()
    ctx = PipelineContext(run_id="r3", old_image_path=Path(__file__), mode="single_sheet")
    ctx.detect_single_result = original
    tiled_result = SingleSheetDetectResult(
        detections=[
            SingleSheetDetection(
                id="tiled-1", flagged_by="revision_cloud", geometry_description="from tiling"
            )
        ],
        extracted_tables=[],
    )
    cap_diagnostics = VolumeCapDiagnostics(pre_cap_count=1, post_cap_count=1, cap=40, applied=False)

    with patch(
        "dre.pipeline.tiled_detect.run_tiled_detect_and_merge",
        return_value=(tiled_result, cap_diagnostics),
    ) as mock_run:
        outcome = decide_and_apply_tiling(ctx, raw_pdf_bytes=b"%PDF-1.4 fake pdf bytes", dpi=150.0)

    mock_run.assert_called_once_with(
        b"%PDF-1.4 fake pdf bytes", dpi=150.0, usage_sink=ctx.token_usage
    )
    assert outcome.path == "tiled"
    assert ctx.detect_single_result is tiled_result
    assert outcome.volume_cap_diagnostics == cap_diagnostics.model_dump(mode="json")


def test_falls_back_to_single_pass_when_tiling_raises():
    """Tiling is a quality enhancement over an already-working single-pass
    result, not a new hard dependency - a real failure mid-attempt (a
    Claude API error on some tile, a malformed PDF) must not take down the
    whole analysis request."""
    original = _triggering_result()
    ctx = PipelineContext(run_id="r4", old_image_path=Path(__file__), mode="single_sheet")
    ctx.detect_single_result = original

    with patch(
        "dre.pipeline.tiled_detect.run_tiled_detect_and_merge",
        side_effect=RuntimeError("simulated API failure on tile 2"),
    ):
        outcome = decide_and_apply_tiling(ctx, raw_pdf_bytes=b"%PDF-1.4 fake pdf bytes")

    assert outcome.path == "tiled_failed_fallback"
    assert ctx.detect_single_result is original


def test_asserts_on_two_image_mode_rather_than_silently_no_oping():
    """Two-image tiling has no design or validation behind it (docs/tiled_
    analysis_findings.md §5) - decide_and_apply_tiling should only ever be
    called for single_sheet mode (dre.service gates the hook on ctx.mode
    before calling this); a two_image call reaching it is a real wiring
    bug worth surfacing loudly, not a case to handle quietly."""
    ctx = PipelineContext(run_id="r5", old_image_path=Path(__file__), mode="two_image")

    with pytest.raises(AssertionError):
        decide_and_apply_tiling(ctx, raw_pdf_bytes=None)


# ---- estimate_analysis_duration (docs/tiled_analysis_findings.md §3e) ----


def _real_pdf_bytes(width_in: float, height_in: float) -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page(width=width_in * 72, height=height_in * 72)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def test_estimate_flags_tiling_likely_for_a_real_large_sheet():
    """ARCH-E-sized (36x48in) - real precedent: E-101.2 (50x36in) needed 20
    tiles at 150 DPI, so a sheet in this range is exactly the shape §3a's
    cheap pre-filter is meant to catch early."""
    pdf_bytes = _real_pdf_bytes(36, 48)
    estimate = estimate_analysis_duration(pdf_bytes)

    assert estimate.tiling_likely is True
    assert estimate.estimated_duration_seconds == DEFAULT_TILED_ESTIMATE_SECONDS
    assert "tile" in estimate.reason.lower()


def test_estimate_flags_tiling_unlikely_for_a_real_small_sheet():
    """Letter-sized (8.5x11in) - real precedent: this is the same size-only
    reasoning §3a's cheap pre-filter uses to rule out small sheets before
    spending anything on the real content-based trigger check."""
    pdf_bytes = _real_pdf_bytes(8.5, 11)
    estimate = estimate_analysis_duration(pdf_bytes)

    assert estimate.tiling_likely is False
    assert estimate.estimated_duration_seconds == DEFAULT_SINGLE_PASS_ESTIMATE_SECONDS


def test_estimate_handles_a_non_pdf_source_as_single_pass():
    """A plain image upload can't be tiled at all today regardless of
    size (module docstring) - must degrade the same as a small sheet, not
    crash trying to read a PDF page size from non-PDF bytes."""
    estimate = estimate_analysis_duration(b"\x89PNG\r\n\x1a\nnot a real pdf")

    assert estimate.tiling_likely is False
    assert estimate.estimated_duration_seconds == DEFAULT_SINGLE_PASS_ESTIMATE_SECONDS
    assert "not a pdf" in estimate.reason.lower()


def test_estimate_handles_none_pdf_bytes():
    estimate = estimate_analysis_duration(None)
    assert estimate.tiling_likely is False
    assert estimate.estimated_duration_seconds == DEFAULT_SINGLE_PASS_ESTIMATE_SECONDS


def test_estimate_makes_no_api_call():
    """The whole point of §3e: cheap enough to call speculatively. Real
    proof, not just an absence of imports - patch out the one thing that
    would make this expensive (the tile-grid detect call) and confirm it's
    simply never reachable from this function at all."""
    import dre.pipeline.tiled_detect as tiled_detect_module

    with patch.object(
        tiled_detect_module, "run_detect_single_on_grid", side_effect=AssertionError("should never be called")
    ):
        estimate_analysis_duration(_real_pdf_bytes(36, 48))  # would raise if it touched the API path
