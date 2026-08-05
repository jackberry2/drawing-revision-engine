"""Runs the pipeline against every case in evals/cases/ and scores the result
against that case's expected_output.json. Meant to be run every iteration:

    dre eval
    # or directly:
    python evals/run_eval.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scorer import ExpectedCase, score_case

from dre.pipeline.runner import run_pipeline

CASES_DIR = Path(__file__).parent / "cases"


def find_case_dirs() -> list[Path]:
    return sorted(d for d in CASES_DIR.iterdir() if d.is_dir())


def find_image(case_dir: Path, stem: str) -> Path | None:
    matches = sorted(case_dir.glob(f"{stem}.*"))
    return matches[0] if matches else None


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
        prev_image = find_image(case_dir, "prev")
        revised_image = find_image(case_dir, "revised")

        if not expected_path.exists() or prev_image is None or revised_image is None:
            print(f"[{case_id}] SKIP - missing prev/revised image or expected_output.json")
            continue

        ran_any = True
        expected = ExpectedCase.model_validate(json.loads(expected_path.read_text()))
        result = run_pipeline(prev_image, revised_image, sheet_id=case_id)
        score = score_case(case_id, result.alerts, expected)

        status = "PASS" if score.passed else "FAIL"
        print(f"[{case_id}] {status}  (run_id={result.run_id})")
        for m in score.matches:
            mark = "ok    " if m.matched else "MISSED"
            detail = m.actual_headline or m.reason
            print(f"    {mark}  expected={m.expected_category!r}  {detail}")
        for headline in score.hallucinated:
            print(f"    EXTRA   unexpected alert: {headline!r}")

        all_passed = all_passed and score.passed

    if not ran_any:
        print("No runnable cases (all skipped). Add prev/revised images + expected_output.json.")
        return 1

    print("")
    print("ALL PASS" if all_passed else "FAILURES PRESENT")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
