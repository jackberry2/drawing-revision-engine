"""Compares actual pipeline output — mapped onto the flagged_changes shape,
via the same `dre.mapping` code path the live service uses — against a
known-correct expected_output.json for one eval case.

Per the user's own grading standard: judge whether the same changes were
captured, with a roughly matching confidence tier and correct bundling
reasoning — not word-for-word text. Matching is loose on identity (ids are
regenerated every run) and on wording (keyword/substring checks), strict on
`change_type` and (when specified) `confidence_tier`.
"""

from __future__ import annotations

from typing import Optional, Union

from pydantic import BaseModel


class MappedAlert(BaseModel):
    """The flagged_changes-shaped view of one alert, produced by mapping.py."""

    change_type: str
    description: str
    impact_note: Optional[str] = None
    confidence_tier: str
    confidence_percentage: int
    entity_identifiers: list[str] = []


class ExpectedAlert(BaseModel):
    # "added" | "removed" | "moved" | "modified", or a list of acceptable
    # values for a genuinely ambiguous case where more than one bucket is a
    # defensible read (e.g. a shift that could reasonably land as "moved"
    # or "modified" depending on exactly what else the model noticed).
    change_type: Union[str, list[str]]
    required_entities: list[str] = []
    description_keywords: list[str] = []
    confidence_tier: Optional[str] = None  # "high" | "medium" | "low", exact match if set

    @property
    def change_types(self) -> list[str]:
        return [self.change_type] if isinstance(self.change_type, str) else self.change_type


class ExpectedCase(BaseModel):
    alerts: list[ExpectedAlert]


class MatchDetail(BaseModel):
    matched: bool
    expected_change_type: str
    actual_description: Optional[str] = None
    reason: str = ""


class CaseScore(BaseModel):
    case_id: str
    matches: list[MatchDetail]
    missed: list[ExpectedAlert]
    hallucinated: list[str]

    @property
    def passed(self) -> bool:
        return not self.missed and not self.hallucinated


def _alert_matches(alert: MappedAlert, expected: ExpectedAlert) -> tuple[bool, str]:
    if alert.change_type not in expected.change_types:
        return (
            False,
            f"change_type {alert.change_type!r} != expected {expected.change_types!r}",
        )

    haystack = " ".join(
        [alert.description, alert.impact_note or "", *alert.entity_identifiers]
    ).lower()
    for required in expected.required_entities:
        if required.lower() not in haystack:
            return False, f"missing required entity {required!r}"
    for keyword in expected.description_keywords:
        if keyword.lower() not in haystack:
            return False, f"missing description keyword {keyword!r}"
    if expected.confidence_tier is not None and alert.confidence_tier != expected.confidence_tier:
        return (
            False,
            f"confidence_tier {alert.confidence_tier!r} != expected {expected.confidence_tier!r}",
        )
    return True, "ok"


def score_case(case_id: str, actual_alerts: list[MappedAlert], expected: ExpectedCase) -> CaseScore:
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
        expected_change_type = "/".join(exp.change_types)
        if found is None:
            missed.append(exp)
            matches.append(
                MatchDetail(matched=False, expected_change_type=expected_change_type, reason=reason)
            )
        else:
            remaining.remove(found)
            matches.append(
                MatchDetail(
                    matched=True,
                    expected_change_type=expected_change_type,
                    actual_description=found.description,
                )
            )

    hallucinated = [a.description for a in remaining]
    return CaseScore(case_id=case_id, matches=matches, missed=missed, hallucinated=hallucinated)
