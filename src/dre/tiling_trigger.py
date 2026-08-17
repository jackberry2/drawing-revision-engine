"""Passive data collection for the tiling proposal in
docs/tiled_analysis_findings.md §3a — computes and logs whether that
document's trigger rule *would* fire against every real `detect`/
`detect_single` output, regardless of whether tiling itself is ever built.
This costs nothing beyond what already exists (detect's output is already
computed) and is exactly what's needed to move the rule from
"directionally validated on 2 sheets" to validated against a real
population before its thresholds are trusted for anything.

No commitment to build tiling is implied by this module existing.
"""

from __future__ import annotations

import re
from typing import Optional, Union

from pydantic import BaseModel

from dre.models.schemas import DetectResult, SingleSheetDetectResult

# Detections carrying an actual printed label quote something specific;
# plain shape/marker descriptions with nothing to quote are expected not
# to. Requires a quoted run of at least 3 characters starting alnum, to
# avoid matching stray apostrophes (e.g. possessives) as a "quote".
_QUOTE_RE = re.compile(r"['\"][A-Za-z0-9][^'\"]{2,}['\"]")

# Below this many detections, quoted_fraction is too noisy on a single
# data point to trust (a sparse-but-legitimate sheet could swing from 0/1
# to 1/1) — see docs/tiled_analysis_findings.md §3a.
_MIN_DETECTIONS_FOR_QUOTED_FRACTION = 3


class TilingTriggerDiagnostics(BaseModel):
    detection_count: int
    distinct_extracted_table_titles: int
    quoted_fraction: Optional[float]
    rule_branch: str  # "low_n" or "main" — which branch of the §3a rule was used
    would_trigger: bool


def compute_tiling_trigger_diagnostics(
    result: Union[DetectResult, SingleSheetDetectResult],
) -> TilingTriggerDiagnostics:
    """Implements exactly the rule validated in docs/tiled_analysis_findings.md
    §3a against real E-101.3 (correctly off) and E-101.2 (correctly fires)
    traces. Deliberately does not depend on `flagged_by` — that field only
    exists on `SingleSheetDetection`, not `RawDetection`, a real bug caught
    while validating this rule against E-101.2's two-image-mode trace."""
    geometry_descriptions = [d.geometry_description for d in _detections(result)]
    detection_count = len(geometry_descriptions)

    distinct_table_titles = {t.title for t in result.extracted_tables if t.title}
    n_tables = len(distinct_table_titles)

    if detection_count < _MIN_DETECTIONS_FOR_QUOTED_FRACTION:
        quoted_fraction = None
        would_trigger = n_tables == 0
        branch = "low_n"
    else:
        quoted_count = sum(1 for desc in geometry_descriptions if _QUOTE_RE.search(desc))
        quoted_fraction = round(quoted_count / detection_count, 4)
        would_trigger = n_tables <= 1 and quoted_fraction < 0.5
        branch = "main"

    return TilingTriggerDiagnostics(
        detection_count=detection_count,
        distinct_extracted_table_titles=n_tables,
        quoted_fraction=quoted_fraction,
        rule_branch=branch,
        would_trigger=would_trigger,
    )


def _detections(result: Union[DetectResult, SingleSheetDetectResult]) -> list:
    if isinstance(result, SingleSheetDetectResult):
        return result.detections
    return result.raw_detections
