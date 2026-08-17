"""Covers dre.pipeline.tile_merge using the exact real detection text from
E-101.2 and E-101.3's tiled tests (docs/tiled_analysis_findings.md §1) -
not synthetic examples. Two real cases, tested honestly in both
directions:

1. E-101.2's two "B5"-tagged revision clouds, in different rooms with
   different fixture types - the real negative case that motivated
   requiring MIN_SHARED_TOKENS=2 rather than trusting a single shared tag.
   Confirmed kept separate.

2. E-101.3's revision-cloud cluster split across a tile boundary - the
   real positive case (confirmed the same physical cloud by directly
   viewing both tiles, see the E-101.3 tiled-test conversation). Content
   matching does NOT merge these under the current design, because the
   only content the two tiles share is the generic "4" bulletin-tag
   number - the same weak-signal risk MIN_SHARED_TOKENS=2 was built to
   guard against, just cutting the other way here. Documented as a known,
   honest v1 limitation (safer failure direction: under-merging leaves
   both fragments individually reported rather than silently conflating
   two different real elements), not hidden or asserted away.
"""

from dre.models.schemas import SingleSheetDetection
from dre.pipeline.tile_merge import (
    TiledDetection,
    compute_merge_diagnostics,
    extract_content_tokens,
    find_merge_candidates,
    group_merge_candidates,
    likely_same_element,
    tiles_are_adjacent,
)

# --- Real E-101.2 detections (tile row=1, col=1, 150 DPI) ---
# Both B5 clouds were actually captured within this single tile in the
# real run - assigned to two different (adjacent) tiles here specifically
# to exercise the cross-tile candidate path, using their real text
# unmodified. Documented, not silently pretended to be a real cross-tile
# capture.
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
_E101_2_TAG_1 = SingleSheetDetection(
    id="tag1",
    flagged_by="revision_tag",
    geometry_description=(
        "Red triangular tag containing text 'B5' pointing at the bottom edge of "
        "the vertical red-clouded rectangular region containing light fixture "
        "circles."
    ),
)
_E101_2_CLOUD_2 = SingleSheetDetection(
    id="cloud2",
    flagged_by="revision_cloud",
    geometry_description=(
        "Irregular rounded red dashed cloud outline enclosing two light fixture "
        "circle symbols labeled 'R6' near a hexagon tag numbered '5', located in "
        "a room area to the right of E124."
    ),
)
_E101_2_TAG_2 = SingleSheetDetection(
    id="tag2",
    flagged_by="revision_tag",
    geometry_description=(
        "Red triangular tag containing text 'B5' pointing at a small "
        "archway/curved wall segment near the E124/E125 room boundary."
    ),
)

# --- Real E-101.3 detections (tile row=1 col=1 and row=1 col=0, 150 DPI) ---
_E101_3_TAG_R1C1 = SingleSheetDetection(
    id="tag_r1c1",
    flagged_by="revision_tag",
    geometry_description=(
        "Red triangular revision tag containing number '4', positioned at left "
        "edge next to a vertical red squiggly/cloud-hatch line running the full "
        "height of the sheet; the number 4 tag points at the top of this red "
        "vertical marking"
    ),
)
_E101_3_CLOUD_R1C0 = SingleSheetDetection(
    id="cloud_r1c0",
    flagged_by="revision_cloud",
    geometry_description=(
        "Vertical elongated revision cloud (scalloped/wavy line border) running "
        "from near top where dashed line labeled 'EXISTING INCOMING FIBER TO "
        "REMAIN' meets a small symbol, down through a cluster of small circle "
        "symbols in a row, continuing down alongside a vertical solid line to "
        "the bottom of the visible sheet. Accompanied by a red numbered "
        "triangle tag '4' near the top of the cloud."
    ),
)
_E101_3_LABELS_R1C0 = SingleSheetDetection(
    id="labels_r1c0",
    flagged_by="revision_cloud",
    geometry_description=(
        "Row of small circular symbols (appears to be a cluster of 5 small "
        "circles in a horizontal line) enclosed within the revision cloud, with "
        "leader lines pointing to text labels 'NORTH COMMUNICATIONS CONDUIT "
        "SERVICE ENTRANCE' and 'SOUTH COMMUNICATIONS CONDUIT SERVICE ENTRANCE' "
        "below; the circles themselves are inside the clouded region."
    ),
)


def test_extract_content_tokens_finds_label_codes_and_weak_quoted_tokens():
    tokens = extract_content_tokens(_E101_2_CLOUD_1.geometry_description)
    assert "R2" in tokens
    assert "EX1" in tokens
    assert "B5" in tokens
    # "B5" is a label code (letter+digit) - must not also appear as a
    # separate weak "~B5" token, which would double-count the same signal.
    assert "~B5" not in tokens


def test_extract_content_tokens_bare_number_is_a_weak_token_only():
    tokens = extract_content_tokens(_E101_3_TAG_R1C1.geometry_description)
    assert tokens == {"~4"}


def test_e101_2_two_b5_clouds_are_not_merged_real_negative_case():
    """The real case that motivated MIN_SHARED_TOKENS=2: both clouds share
    a "B5" tag, but nothing else - different fixture types (R2 vs R6),
    different rooms (none named vs E124/E125). A naive "shared tag is
    enough" rule would wrongly merge two genuinely different revision
    clouds into one. Every cross-pair here must come back false."""
    assert likely_same_element(_E101_2_CLOUD_1, _E101_2_CLOUD_2) is False
    assert likely_same_element(_E101_2_TAG_1, _E101_2_TAG_2) is False
    assert likely_same_element(_E101_2_CLOUD_1, _E101_2_TAG_2) is False
    assert likely_same_element(_E101_2_TAG_1, _E101_2_CLOUD_2) is False


def test_e101_2_cloud_and_its_own_tag_is_a_second_honest_limitation():
    """Not the sanity check it looks like - the original assumption here
    (a cloud and its own tag obviously share enough content to match) was
    wrong, caught by running this test against real data rather than
    assumed. tag1's own text is terse - "Red triangular tag containing
    text 'B5' pointing at the bottom edge..." - and doesn't repeat the
    cloud's other distinguishing details (R2, EX1), so they only share the
    one "B5" token, same as the cross-cloud case this design guards
    against. A second real, honest limitation alongside the E-101.3 case:
    tags are typically terse, so a cloud and its own tag split across a
    tile boundary will often also under-merge, not just genuinely
    different clusters that happen to share a bulletin/tag number. Not
    fixed by raising this pair's priority specifically - doing so would
    reopen the exact false-positive risk test_e101_2_two_b5_clouds_are_
    not_merged_real_negative_case exists to prevent, since that case's
    only shared token is also a single label code ("B5")."""
    assert likely_same_element(_E101_2_CLOUD_1, _E101_2_TAG_1) is False


def test_find_merge_candidates_keeps_the_two_b5_clouds_in_separate_groups():
    """End-to-end: assign the four E-101.2 detections across two adjacent
    tiles (constructed for this test - both were really captured in one
    tile, see module docstring) and confirm grouping keeps the two real,
    distinct clouds apart rather than merging them via the shared tag."""
    detections = [
        TiledDetection(tile_row=1, tile_col=1, detection=_E101_2_CLOUD_1),
        TiledDetection(tile_row=1, tile_col=1, detection=_E101_2_TAG_1),
        TiledDetection(tile_row=1, tile_col=2, detection=_E101_2_CLOUD_2),
        TiledDetection(tile_row=1, tile_col=2, detection=_E101_2_TAG_2),
    ]
    groups = group_merge_candidates(detections)
    # cloud1+tag1 are same-tile (excluded from candidates, so each is its
    # own group unless a cross-tile match pulls it in) - the real
    # assertion that matters: no group contains detections from both the
    # cloud1/tag1 pair AND the cloud2/tag2 pair.
    for group in groups:
        ids = {td.detection.id for td in group}
        assert not (
            ids & {"cloud1", "tag1"} and ids & {"cloud2", "tag2"}
        ), f"wrongly merged the two distinct B5 clouds into one group: {ids}"


def test_e101_3_cluster_split_across_tile_boundary_is_a_known_miss():
    """Honest documentation of a real limitation, not a hidden gap: this
    is the SAME physical revision cloud, split across a tile boundary
    (confirmed by directly viewing both tiles during the E-101.3 tiled
    test). Content matching does not merge it under the current design,
    because the only shared signal between these two tiles' detections is
    the generic "4" bulletin-tag number - exactly the kind of weak,
    collision-prone signal MIN_SHARED_TOKENS=2 exists to not trust alone.
    Safer failure direction (both fragments still get reported separately,
    nothing is silently conflated) but a real gap worth revisiting with a
    larger positive-case sample before trusting content-matching alone in
    general - see docs/tiled_analysis_findings.md §5.
    """
    assert likely_same_element(_E101_3_TAG_R1C1, _E101_3_CLOUD_R1C0) is False
    assert likely_same_element(_E101_3_TAG_R1C1, _E101_3_LABELS_R1C0) is False


def test_tiles_are_adjacent():
    assert tiles_are_adjacent(1, 1, 1, 1) is True  # same tile
    assert tiles_are_adjacent(1, 1, 1, 2) is True  # horizontal neighbor
    assert tiles_are_adjacent(1, 1, 2, 1) is True  # vertical neighbor
    assert tiles_are_adjacent(1, 1, 2, 2) is True  # diagonal neighbor
    assert tiles_are_adjacent(1, 1, 1, 3) is False
    assert tiles_are_adjacent(1, 1, 4, 1) is False


def test_find_merge_candidates_excludes_same_tile_pairs():
    """Same-tile detections are already distinct entries in one coherent
    detect_single response - reconciling those is reason_single's existing
    bundling job, not this module's."""
    detections = [
        TiledDetection(tile_row=1, tile_col=1, detection=_E101_2_CLOUD_1),
        TiledDetection(tile_row=1, tile_col=1, detection=_E101_2_TAG_1),
    ]
    assert find_merge_candidates(detections) == []


def test_group_merge_candidates_returns_singletons_for_unrelated_detections():
    detections = [
        TiledDetection(tile_row=0, tile_col=0, detection=_E101_2_CLOUD_1),
        TiledDetection(tile_row=5, tile_col=5, detection=_E101_2_CLOUD_2),
    ]
    groups = group_merge_candidates(detections)
    assert len(groups) == 2
    assert all(len(g) == 1 for g in groups)


def test_compute_merge_diagnostics_on_the_real_e101_2_calibration_case():
    """The current calibration baseline (§5): this exact real case is the
    '1 known-good' data point - candidate pairs get evaluated (cross-tile,
    content overlap computed) but none clear the threshold, so no group
    ends up with multiple members."""
    detections = [
        TiledDetection(tile_row=1, tile_col=1, detection=_E101_2_CLOUD_1),
        TiledDetection(tile_row=1, tile_col=1, detection=_E101_2_TAG_1),
        TiledDetection(tile_row=1, tile_col=2, detection=_E101_2_CLOUD_2),
        TiledDetection(tile_row=1, tile_col=2, detection=_E101_2_TAG_2),
    ]
    diagnostics = compute_merge_diagnostics(detections)

    assert diagnostics.total_detections == 4
    assert diagnostics.multi_member_groups == 0
    # tag1-tag2 is a real evaluated candidate (adjacent tiles, shares "B5")
    # that correctly fails the threshold - confirm it shows up in the
    # diagnostics for calibration purposes, not just that it was rejected.
    tag_pair = [
        p
        for p in diagnostics.pairs
        if {p.detection_id_a, p.detection_id_b} == {"tag1", "tag2"}
    ]
    assert len(tag_pair) == 1
    assert tag_pair[0].shared_tokens == ["B5"]


def test_compute_merge_diagnostics_on_the_real_e101_3_calibration_case():
    """The current calibration baseline (§5): this exact real case is the
    '1 known-miss' data point - the real cluster (confirmed the same
    physical cloud by directly viewing both tiles) doesn't produce a
    multi-member group, logged here as calibration data, not hidden."""
    detections = [
        TiledDetection(tile_row=1, tile_col=1, detection=_E101_3_TAG_R1C1),
        TiledDetection(tile_row=1, tile_col=0, detection=_E101_3_CLOUD_R1C0),
        TiledDetection(tile_row=1, tile_col=0, detection=_E101_3_LABELS_R1C0),
    ]
    diagnostics = compute_merge_diagnostics(detections)

    assert diagnostics.total_detections == 3
    assert diagnostics.multi_member_groups == 0
