"""Compares actual pipeline output against a known-correct expected_output.json
for one eval case.

Matching is deliberately loose on identity (generated ids never match run to
run) and strict on substance: category, the trade entities involved, and any
description keywords the expected case cares about. A case fails if any
expected alert goes unmatched (a miss) or any actual alert matches nothing
expected (a hallucination) — that second condition is what stops the engine
from over-reporting borderline/noise changes as it's tuned.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from dre.models.schemas import FinalChangeAlert


class ExpectedAlert(BaseModel):
    category: str
    required_entities: list[str] = []
    description_keywords: list[str] = []
    min_confidence: Optional[float] = None


class ExpectedCase(BaseModel):
    alerts: list[ExpectedAlert]


class MatchDetail(BaseModel):
    matched: bool
    expected_category: str
    actual_headline: Optional[str] = None
    reason: str = ""


class CaseScore(BaseModel):
    case_id: str
    matches: list[MatchDetail]
    missed: list[ExpectedAlert]
    hallucinated: list[str]

    @property
    def passed(self) -> bool:
        return not self.missed and not self.hallucinated


def _alert_matches(alert: FinalChangeAlert, expected: ExpectedAlert) -> tuple[bool, str]:
    if alert.category.value != expected.category:
        return False, f"category {alert.category.value!r} != expected {expected.category!r}"

    haystack = alert.description.lower() + " " + " ".join(
        e.identifier.lower() for e in alert.affected_entities
    )
    for required in expected.required_entities:
        if required.lower() not in haystack:
            return False, f"missing required entity {required!r}"
    for keyword in expected.description_keywords:
        if keyword.lower() not in alert.description.lower():
            return False, f"missing description keyword {keyword!r}"
    if expected.min_confidence is not None and alert.confidence.score < expected.min_confidence:
        return (
            False,
            f"confidence {alert.confidence.score:.2f} below required {expected.min_confidence:.2f}",
        )
    return True, "ok"


def score_case(case_id: str, actual_alerts: list[FinalChangeAlert], expected: ExpectedCase) -> CaseScore:
    remaining = list(actual_alerts)
    matches: list[MatchDetail] = []
    missed: list[ExpectedAlert] = []

    for exp in expected.alerts:
        found = None
        reason = "no candidate alert matched"
        for alert in remaining:
            ok, why = _alert_matches(alert, exp)
            if ok:
                found = alert
                break
            reason = why
        if found is None:
            missed.append(exp)
            matches.append(MatchDetail(matched=False, expected_category=exp.category, reason=reason))
        else:
            remaining.remove(found)
            matches.append(
                MatchDetail(matched=True, expected_category=exp.category, actual_headline=found.headline)
            )

    hallucinated = [a.headline for a in remaining]
    return CaseScore(case_id=case_id, matches=matches, missed=missed, hallucinated=hallucinated)
