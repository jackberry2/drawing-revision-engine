"""Cross-tile merge/dedup for docs/tiled_analysis_findings.md §3c — option
C from the sketched alternatives: tile-adjacency-scoped candidate
generation (cheap, uses only `compute_tile_grid`'s own known-correct grid
structure) plus content-based confirmation (matches by what a detection
describes, never by where it claims to be).

Built this way specifically because detect's self-reported regions have
been shown unreliable even when independent runs agree with each other
(see docs/tiled_analysis_findings.md §3c and §1's E-101.2 finding) — a
purely geometric IOU merge inherits that unreliability directly. Content
matching sidesteps it: only the deterministic tile grid (never a
detection's own coordinate claim) decides which pairs are even worth
comparing, and only shared description content decides whether to merge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel

from dre.models.schemas import SingleSheetDetection

# Distinctive alphanumeric codes (tag numbers, entity/room labels) - "B5",
# "R2", "E124", "EX1" - a strong signal regardless of whether the source
# text happens to quote them. Requires a letter prefix precisely because
# bare short numbers ("4", "12") are too generic/collision-prone to trust
# on their own - see _QUOTED_SHORT_TOKEN_RE below.
_LABEL_CODE_RE = re.compile(r"\b[A-Z]{1,4}-?\d{1,4}[A-Z]?\b")

# A bare quoted short token (e.g. "number '4'") is a *weaker* signal than a
# distinctive label code: short digit-only tags collide easily (a sheet can
# reference "4" as a conduit size, a coded-note number, and a revision
# bulletin, all in the same area - confirmed a real risk, not hypothetical,
# against E-101.3's real data during validation below). Only ever used as a
# secondary signal alongside label codes, via MIN_SHARED_TOKENS - never
# sufficient alone to trigger a merge.
_QUOTED_SHORT_TOKEN_RE = re.compile(r"['\"]([A-Za-z0-9]{1,4})['\"]")

MIN_SHARED_TOKENS = 2


@dataclass(frozen=True)
class TiledDetection:
    """One detection, tagged with which tile it came from. The tile
    indices are the only spatial information this module trusts - they
    come from `compute_tile_grid`'s own deterministic output, not from the
    detection's own self-reported (and shown-unreliable) `region`."""

    tile_row: int
    tile_col: int
    detection: SingleSheetDetection


def extract_content_tokens(text: str) -> set[str]:
    """Distinctive content signal from a detection's own description text.
    A documented v1 heuristic (see module docstring), not a claim of real
    semantic understanding - validated against real data below, including
    a case where it's honestly known to under-match (see
    tests/test_tile_merge.py's E-101.3 case)."""
    upper = text.upper()
    label_codes = set(_LABEL_CODE_RE.findall(upper))
    quoted = set(_QUOTED_SHORT_TOKEN_RE.findall(upper))
    # A quoted token already captured as a label code isn't a second,
    # independent signal - avoids double-counting e.g. "B5" once as a
    # label code and again as a "weak" quoted token.
    weak_tokens = {f"~{t}" for t in quoted if t not in label_codes}
    return label_codes | weak_tokens


def tiles_are_adjacent(row_a: int, col_a: int, row_b: int, col_b: int) -> bool:
    """Same tile or any of the 8 neighbors in the grid - the only spatial
    relationship this module trusts, since it comes from the tile grid
    itself, never from a detection's self-reported region."""
    return abs(row_a - row_b) <= 1 and abs(col_a - col_b) <= 1


def likely_same_element(a: SingleSheetDetection, b: SingleSheetDetection) -> bool:
    """Content-based confirmation - matches by what's described, never by
    where either detection claims to be. Requires at least
    MIN_SHARED_TOKENS distinctive tokens in common, not just one: a single
    shared tag (e.g. "B5") is exactly the real E-101.2 case that would
    wrongly merge two genuinely different revision clouds in different
    rooms if one shared token were treated as sufficient."""
    shared = extract_content_tokens(a.geometry_description) & extract_content_tokens(
        b.geometry_description
    )
    return len(shared) >= MIN_SHARED_TOKENS


def _adjacent_cross_tile_pairs(
    detections: list[TiledDetection],
) -> list[tuple[TiledDetection, TiledDetection]]:
    """Every cross-tile pair worth evaluating at all: adjacent tiles per
    the known-correct grid, excluding same-tile pairs (`detect_single`
    already treats same-image detections as distinct entries in one
    coherent response; reconciling those is `reason_single`'s existing
    bundling job, not this module's). Does NOT filter by content overlap -
    that's `find_merge_candidates`'s job when the goal is an actual merge
    decision, versus `compute_merge_diagnostics`'s job when the goal is
    logging what *every* evaluated pair scored, matched or not."""
    pairs = []
    for i, a in enumerate(detections):
        for b in detections[i + 1 :]:
            if a.tile_row == b.tile_row and a.tile_col == b.tile_col:
                continue
            if not tiles_are_adjacent(a.tile_row, a.tile_col, b.tile_row, b.tile_col):
                continue
            pairs.append((a, b))
    return pairs


def find_merge_candidates(
    detections: list[TiledDetection],
) -> list[tuple[TiledDetection, TiledDetection]]:
    """Cross-tile candidate pairs likely representing the same real
    element: adjacent (or same) tiles per the known-correct grid, AND
    enough shared content to pass `likely_same_element`."""
    return [
        (a, b)
        for a, b in _adjacent_cross_tile_pairs(detections)
        if likely_same_element(a.detection, b.detection)
    ]


def group_merge_candidates(detections: list[TiledDetection]) -> list[list[TiledDetection]]:
    """Groups detections into clusters via the pairwise candidates above,
    using union-find so a chain of pairwise matches (A-B, B-C) merges into
    one group (A, B, C) instead of staying as separate overlapping pairs.
    A detection with no merge candidates comes back as its own singleton
    group - nothing is dropped, only combined where content actually
    supports it."""
    n = len(detections)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    index_of = {id(d): i for i, d in enumerate(detections)}
    for a, b in find_merge_candidates(detections):
        union(index_of[id(a)], index_of[id(b)])

    groups: dict[int, list[TiledDetection]] = {}
    for i, d in enumerate(detections):
        groups.setdefault(find(i), []).append(d)
    return list(groups.values())


class MergeCandidatePairDiagnostics(BaseModel):
    tile_a: str
    tile_b: str
    detection_id_a: str
    detection_id_b: str
    shared_tokens: list[str]
    matched: bool


class MergeDiagnostics(BaseModel):
    """Passive calibration data for MIN_SHARED_TOKENS (docs/tiled_analysis_
    findings.md §5) — logged the same way §3a's tiling_trigger diagnostics
    are, so the threshold can eventually be tuned against accumulated real
    evidence instead of the current 1-known-good/1-known-miss baseline.
    `pairs` covers EVERY adjacent cross-tile pair evaluated, not just the
    ones that cleared MIN_SHARED_TOKENS — a pair that scored just under
    threshold is exactly the data point a future retuning decision needs,
    so it's logged with `matched=False` rather than silently dropped.
    Unlike §3a's trigger (which runs on every real single-image detect
    call already happening in production), there is no real multi-tile
    production flow yet for this to attach to passively — see §5. This is
    the pure computation, ready to log the moment one exists; today it's
    exercised through the tuning harness (`dre tile-detect-grid`), not
    production traffic.
    """

    total_detections: int
    evaluated_pairs: int
    matched_pairs: int
    groups: int
    multi_member_groups: int
    pairs: list[MergeCandidatePairDiagnostics]


def compute_merge_diagnostics(detections: list[TiledDetection]) -> MergeDiagnostics:
    evaluated_pairs = _adjacent_cross_tile_pairs(detections)
    groups = group_merge_candidates(detections)
    pairs = []
    matched_count = 0
    for a, b in evaluated_pairs:
        shared = extract_content_tokens(a.detection.geometry_description) & extract_content_tokens(
            b.detection.geometry_description
        )
        matched = len(shared) >= MIN_SHARED_TOKENS
        matched_count += matched
        pairs.append(
            MergeCandidatePairDiagnostics(
                tile_a=f"({a.tile_row},{a.tile_col})",
                tile_b=f"({b.tile_row},{b.tile_col})",
                detection_id_a=a.detection.id,
                detection_id_b=b.detection.id,
                shared_tokens=sorted(shared),
                matched=matched,
            )
        )
    return MergeDiagnostics(
        total_detections=len(detections),
        evaluated_pairs=len(pairs),
        matched_pairs=matched_count,
        groups=len(groups),
        multi_member_groups=sum(1 for g in groups if len(g) > 1),
        pairs=pairs,
    )
