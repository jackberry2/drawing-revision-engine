"""Validates dre.tiling_trigger against the exact real traces documented in
docs/tiled_analysis_findings.md §3a — E-101.3 (good, post-2576px-fix,
single_sheet) must stay off, E-101.2 (degraded, two_image) must fire. These
fixtures are the real detect/detect_single output, not synthetic data."""

from dre.models.schemas import (
    DetectResult,
    ExtractedTable,
    RawDetection,
    SingleSheetDetectResult,
    SingleSheetDetection,
)
from dre.tiling_trigger import compute_tiling_trigger_diagnostics

_E101_3_DETECT_SINGLE_RESULT = SingleSheetDetectResult(
    detections=[
        SingleSheetDetection(
            id="det-1",
            flagged_by="revision_cloud",
            geometry_description=(
                "Large red dashed-line rectangular cloud/border enclosing the entire "
                "'DOOR W/PANIC HARDWARE ACCESS CONTROL DOOR ELEVATION' detail "
                "(labeled B/E101.3) in the top-left portion of the sheet."
            ),
        ),
        SingleSheetDetection(
            id="det-2",
            flagged_by="revision_tag",
            geometry_description=(
                "Small triangular revision tag (delta symbol) with no visible number, "
                "positioned at the top-left corner of the red dashed cloud border."
            ),
        ),
        SingleSheetDetection(
            id="det-3",
            flagged_by="revision_cloud",
            geometry_description=(
                "Red dashed-line rectangular cloud enclosing a floor plan area labeled "
                "'NORTH COMMUNICATIONS CONDUIT SERVICE ENTRANCE', 'SOUTH COMMUNICATIONS "
                "CONDUIT SERVICE ENTRANCE', and '4\" CONDUIT STUBBED INTO BOILER ROOM'."
            ),
        ),
        SingleSheetDetection(
            id="det-4",
            flagged_by="revision_tag",
            geometry_description=(
                "Small triangular revision tag (delta symbol) with no visible number, "
                "located above the clouded floor-plan area near the communications "
                "service entrance labels."
            ),
        ),
    ],
    extracted_tables=[
        ExtractedTable(id="tbl-1", table_type="other", sheet_version="new", title="GENERAL NOTES"),
        ExtractedTable(id="tbl-2", table_type="other", sheet_version="new", title="CODED NOTES"),
        ExtractedTable(id="tbl-3", table_type="legend", sheet_version="new", title="LINE TYPE LEGEND"),
        ExtractedTable(
            id="tbl-4", table_type="other", sheet_version="new", title="ISSUANCE/REVISION LIST"
        ),
    ],
)

_E101_2_DETECT_RESULT = DetectResult(
    raw_detections=[
        RawDetection(
            id="d1",
            present_in="new_only",
            geometry_description=(
                "New dashed red/orange rectangular outline (cloud/revision marker) "
                "added around cluster of symbols near room E100/E124 area, not "
                "present in old version"
            ),
        ),
        RawDetection(
            id="d2",
            present_in="new_only",
            geometry_description=(
                "New small red/orange triangular revision marker (flag) added near "
                "symbol below the dashed rectangle"
            ),
        ),
        RawDetection(
            id="d3",
            present_in="new_only",
            geometry_description=(
                "New small red/orange triangular revision marker (flag) added near a "
                "circular symbol cluster to the right of d2"
            ),
        ),
        RawDetection(
            id="d4",
            present_in="both_modified",
            geometry_description=(
                "Revision list table entry changed: row 'RFI #36' date text appears "
                "same, but new row 'RFI #47 04/13/2026' added below existing "
                "'RFI #36 04/02/2026' row in issuance/revision list"
            ),
        ),
        RawDetection(
            id="d5",
            present_in="both_modified",
            geometry_description=(
                "Issuance/revision list block expanded slightly in height due to "
                "added row, shifting bottom border of table down slightly compared "
                "to old"
            ),
        ),
    ],
    extracted_tables=[
        ExtractedTable(
            id="t1", table_type="other", sheet_version="old", title="ISSUANCE/REVISION LIST"
        ),
        ExtractedTable(
            id="t2", table_type="other", sheet_version="new", title="ISSUANCE/REVISION LIST"
        ),
    ],
)


def test_e101_3_real_trace_does_not_trigger():
    diagnostics = compute_tiling_trigger_diagnostics(_E101_3_DETECT_SINGLE_RESULT)

    assert diagnostics.detection_count == 4
    assert diagnostics.distinct_extracted_table_titles == 4
    assert diagnostics.quoted_fraction == 0.5
    assert diagnostics.rule_branch == "main"
    assert diagnostics.would_trigger is False


def test_e101_2_real_trace_triggers():
    diagnostics = compute_tiling_trigger_diagnostics(_E101_2_DETECT_RESULT)

    assert diagnostics.detection_count == 5
    assert diagnostics.distinct_extracted_table_titles == 1
    assert diagnostics.quoted_fraction == 0.2
    assert diagnostics.rule_branch == "main"
    assert diagnostics.would_trigger is True


def test_low_detection_count_falls_back_to_zero_tables_branch():
    result = SingleSheetDetectResult(
        detections=[
            SingleSheetDetection(
                id="d1", flagged_by="revision_cloud", geometry_description="A symbol, no label."
            ),
        ],
        extracted_tables=[],
    )
    diagnostics = compute_tiling_trigger_diagnostics(result)

    assert diagnostics.detection_count == 1
    assert diagnostics.quoted_fraction is None
    assert diagnostics.rule_branch == "low_n"
    assert diagnostics.would_trigger is True  # zero tables, below the N floor


def test_low_detection_count_with_a_table_found_does_not_trigger():
    result = SingleSheetDetectResult(
        detections=[
            SingleSheetDetection(
                id="d1", flagged_by="revision_cloud", geometry_description="A symbol, no label."
            ),
        ],
        extracted_tables=[
            ExtractedTable(id="t1", table_type="other", sheet_version="new", title="NOTES"),
        ],
    )
    diagnostics = compute_tiling_trigger_diagnostics(result)

    assert diagnostics.rule_branch == "low_n"
    assert diagnostics.would_trigger is False


def test_zero_detections():
    result = SingleSheetDetectResult(detections=[], extracted_tables=[])
    diagnostics = compute_tiling_trigger_diagnostics(result)

    assert diagnostics.detection_count == 0
    assert diagnostics.rule_branch == "low_n"
    assert diagnostics.would_trigger is True  # zero tables too — but see note below


def test_many_detections_with_at_least_two_tables_never_triggers():
    result = DetectResult(
        raw_detections=[
            RawDetection(id=f"d{i}", present_in="new_only", geometry_description="generic shape")
            for i in range(5)
        ],
        extracted_tables=[
            ExtractedTable(id="t1", table_type="other", sheet_version="new", title="NOTES"),
            ExtractedTable(id="t2", table_type="legend", sheet_version="new", title="LEGEND"),
        ],
    )
    diagnostics = compute_tiling_trigger_diagnostics(result)

    assert diagnostics.would_trigger is False
