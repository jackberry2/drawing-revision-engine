"""Runs the pipeline against every case in evals/cases/ and scores the result
(mapped onto the flagged_changes shape) against that case's
expected_output.json. Fully local — no Supabase credentials needed, since it
runs the pipeline directly against local old/new image files with a no-op
step logger. Meant to be run every iteration:

    dre eval
    # or directly:
    python evals/run_eval.py
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

from scorer import ExpectedCase, MappedAlert, score_case

from dre.mapping import to_change_type, to_confidence_percentage, to_confidence_tier
from dre.pipeline.base import NullStepLogger, PipelineContext
from dre.pipeline.runner import build_pipeline

CASES_DIR = Path(__file__).parent / "cases"


def find_case_dirs() -> list[Path]:
    return sorted(d for d in CASES_DIR.iterdir() if d.is_dir())


def find_image(case_dir: Path, stem: str) -> Path | None:
    matches = sorted(case_dir.glob(f"{stem}.*"))
    return matches[0] if matches else None


def to_mapped_alert(alert) -> MappedAlert:
    return MappedAlert(
        change_type=to_change_type(alert.category),
        description=alert.description,
        impact_note=alert.impact_note,
        confidence_tier=to_confidence_tier(alert.confidence.score),
        confidence_percentage=to_confidence_percentage(alert.confidence.score),
        entity_identifiers=[e.identifier for e in alert.affected_entities],
    )


def main() -> int:
    case_dirs = find_case_dirs()
    if not case_dirs:
        print(f"No eval cases found under {CASES_DIR}")
        return 1

    all_passed = True
    ran_any = False

    for case_dir in case_dirs:
        case_id = case_dir.name
        expected_path = case_dir / "expected_output.json"
        old_image = find_image(case_dir, "old")
        new_image = find_image(case_dir, "new")

        if not expected_path.exists() or old_image is None or new_image is None:
            print(f"[{case_id}] SKIP - missing old/new image or expected_output.json")
            continue

        ran_any = True
        expected = ExpectedCase.model_validate(json.loads(expected_path.read_text()))

        ctx = PipelineContext(
            run_id=f"eval_{uuid.uuid4().hex[:12]}",
            old_image_path=old_image,
            new_image_path=new_image,
            sheet_ref=case_id,
        )
        result = build_pipeline(logger=NullStepLogger()).run(ctx)
        mapped_alerts = [to_mapped_alert(a) for a in result.alerts]
        score = score_case(case_id, mapped_alerts, expected)

        status = "PASS" if score.passed else "FAIL"
        print(f"[{case_id}] {status}  (run_id={ctx.run_id})")
        for m in score.matches:
            mark = "ok    " if m.matched else "MISSED"
            detail = m.actual_description or m.reason
            print(f"    {mark}  expected={m.expected_change_type!r}  {detail}")
        for description in score.hallucinated:
            print(f"    EXTRA   unexpected alert: {description!r}")

        all_passed = all_passed and score.passed

    if not ran_any:
        print("No runnable cases (all skipped). Add old/new images + expected_output.json.")
        return 1

    print("")
    print("ALL PASS" if all_passed else "FAILURES PRESENT")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
