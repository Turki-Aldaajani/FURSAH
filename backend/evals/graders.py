"""Day 4 · 2.3: code graders, one per component from your 2.1 audit.

Every grader has the same signature:

    grader(case, response, spans) -> Grade | None

case      one line of dev_cases.jsonl. Its "expected" block holds:
            status                 "ok" or "no_match"
            acceptable_source_ids  recommending ANY source of these opportunities is correct
            excluded_source_ids    {source_id: reason}: recommending these is wrong, even
                                   next to an acceptable source in the same recommendation
            uncertain_source_ids   {source_id: reason}: availability unknown; a recommendation
                                   made only of these is not graded (always empty in no_match cases)
response  the DiscoveryResponse your workflow.discover() returned
spans     {span name: attributes} for this case's trace; the last span wins when a name
          repeats. spans.all(name) returns every span with that name, in order.

Return None when the case has nothing to check for this metric (e.g. no acceptable IDs).
Put the expected and the actual value in `reason`: it is printed under every failing case.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path


@dataclass
class Grade:
    metric: str
    score: float  # 0..1
    reason: str = ""  # "expected X, got Y"


def tool_inputs(spans, tool_name: str) -> list[dict]:
    """Arguments of every call to `tool_name`, for any framework that records tool spans.

    LangChain, LangGraph and PydanticAI all record `tool.name` and the arguments as JSON in
    `input.value`. Without a framework, record them yourself on a custom span with the same keys.
    Exception: LangChain records a tool with ONE argument as the bare value, not JSON; that
    call comes back as {"raw": value}. Print tool_inputs(...) once to see what yours returns.
    """
    calls = []
    for attributes in spans.all_spans():
        if attributes.get("tool.name") == tool_name:
            value = attributes.get("input.value") or "{}"
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = None
            calls.append(parsed if isinstance(parsed, dict) else {"raw": value})
    return calls


# --- Example (given) ---------------------------------------------------------------

def grade_status(case, response, spans):
    expected = case["expected"]["status"]
    got = response.status.value
    return Grade("e2e.status", float(got == expected), f"expected {expected!r}, got {got!r}")


# --- Our graders: one per component the response and the cases can honestly judge ----------
#
# Everything below uses only the response and the case. The app records no OpenTelemetry spans
# (its 2.2 trace is a JSONL file the harness does not read), so `spans` is never consulted:
# retrieval before the top-3 cut, per-source exclusion reasons, model-step metrics, tool calls
# and the model's reading of the query cannot be graded and are NOT graded here.
#
# Labels drive the verdicts. A grader looks at what the response itself says (type, delivery
# mode, location, deadline, status) only where the labels are silent about a recommendation,
# and never to contradict a label.

# Why a source is excluded, by what it says about the user's request (labels, not guesses).
CONSTRAINT_REASONS = {
    "location_not_preferred": "location",
    "delivery_not_requested": "delivery",
    "type_not_requested": "type",
    "over_budget": "budget",
    "fee_unknown": "budget (fee unknown)",
    "outside_dates": "dates",
}
AVAILABILITY_REASONS = {"deadline_passed", "activity_ended", "status_cancelled", "status_closed", "not_yet_open"}
# Statuses a response may give a recommendation that must not have been recommended.
UNAVAILABLE_STATUSES = {"closed", "cancelled", "not_yet_open"}


def _labels(case):
    expected = case["expected"]
    return (set(expected.get("acceptable_source_ids") or []),
            dict(expected.get("excluded_source_ids") or {}),
            dict(expected.get("uncertain_source_ids") or {}))


def _judge(sources, acceptable, excluded, uncertain):
    """The worksheet's table for one recommendation: ("right" | "wrong" | "ungraded", excluded hits, unlisted).

    wrong     any source is excluded, or no source is in any list (off topic)
    right     at least one acceptable source and none excluded
    ungraded  only uncertain sources (an uncertain source next to unlisted ones is not covered by
              the worksheet; it is left ungraded, and this does not occur in the 24 shared cases)
    """
    hits = [(s, excluded[s]) for s in sources if s in excluded]
    unlisted = [s for s in sources if s not in acceptable and s not in excluded and s not in uncertain]
    if hits:
        return "wrong", hits, unlisted
    if any(s in acceptable for s in sources):
        return "right", hits, unlisted
    if len(unlisted) == len(sources):
        return "wrong", hits, unlisted
    return "ungraded", hits, unlisted


def _sources(response):
    return [list(rec.source_ids) for rec in response.recommendations]


def _tags(hits):
    return [f"{source}:{reason}" for source, reason in hits]


def _evaluation_day(case):
    try:
        return datetime.fromisoformat(case["evaluation_time"]).date()
    except (KeyError, ValueError, TypeError):
        return None


def _as_date(value):
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


@lru_cache(maxsize=1)
def _known_source_ids():
    """Every announcement id in the raw CSV, or None when the file cannot be read."""
    path = Path(__file__).resolve().parents[2] / "data" / "raw" / "raw_opportunities.csv"
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            return frozenset(row["source_id"] for row in csv.DictReader(stream))
    except (OSError, KeyError):
        return None


def grade_recommendations(case, response, spans):
    """e2e.recommendations: share of graded recommendations that are right, by their source_ids.

    Right: an acceptable source and no excluded one. Wrong: any excluded source, or none of its
    sources in any list. A recommendation made only of uncertain sources is neither counted
    for nor against. None when no recommendation was graded.
    """
    acceptable, excluded, uncertain = _labels(case)
    right = wrong = 0
    got, excluded_hits, off_topic = _sources(response), [], []
    for sources in got:
        verdict, hits, unlisted = _judge(sources, acceptable, excluded, uncertain)
        right += verdict == "right"
        if verdict == "wrong":
            wrong += 1
            excluded_hits += _tags(hits)
            off_topic += unlisted if not hits else []
    if right + wrong == 0:
        return None
    expected = f"any of {sorted(acceptable)}" if acceptable else "no recommendation (nothing acceptable)"
    reason = f"expected {expected}, got {got}; {right} right, {wrong} wrong"
    if excluded_hits:
        reason += f"; excluded: {excluded_hits}"
    if off_topic:
        reason += f"; off topic (in no list): {off_topic}"
    return Grade("e2e.recommendations", right / (right + wrong), reason)


def grade_constraints(case, response, spans):
    """e2e.constraints: share of provable recommendations that respect the request's constraints.

    A violation is proven by a label (an excluded source whose reason is a constraint: location,
    delivery, type, budget, dates) or, for a recommendation the labels do not mention at all, by
    its own fields (type or delivery mode outside the requested ones; an in-person or hybrid
    location outside the preferred cities; online is exempt, as in the labels). Respected is proven
    only by a right recommendation. Budget, dates and duration are not in the response, so they
    are judged by labels alone. Anything else is unproven and not counted; None if nothing is.
    """
    acceptable, excluded, uncertain = _labels(case)
    asked = case.get("constraints") or {}
    types, modes, places = (asked.get("opportunity_types") or [], asked.get("delivery_modes") or [],
                            asked.get("location_preference") or [])
    respected = violated = 0
    findings = []
    for rec in response.recommendations:
        sources = list(rec.source_ids)
        verdict, hits, unlisted = _judge(sources, acceptable, excluded, uncertain)
        found = {CONSTRAINT_REASONS[r]: f"{s}:{r}" for s, r in hits if r in CONSTRAINT_REASONS}
        if not found and len(unlisted) == len(sources):  # labels say nothing: the response may still prove it
            if types and rec.opportunity_type is not None and rec.opportunity_type not in types:
                found["type"] = f"response says {rec.opportunity_type!r}, asked {types}"
            if modes and rec.delivery_mode in ("online", "in_person") and rec.delivery_mode not in modes:
                found["delivery"] = f"response says {rec.delivery_mode!r}, asked {modes}"
            if places and rec.delivery_mode in ("in_person", "hybrid") and rec.location is not None \
                    and rec.location not in places:
                found["location"] = f"response says {rec.location!r}, asked {places}"
        if found:
            violated += 1
            findings.append(f"{sources} breaks " + "; ".join(f"{kind} ({why})" for kind, why in found.items()))
        elif verdict == "right":
            respected += 1
    if respected + violated == 0:
        return None
    wanted = {k: v for k, v in asked.items() if v not in (None, [], "")}
    reason = f"expected every provable recommendation to respect {wanted}; {respected} respect it, {violated} break it"
    if findings:
        reason += ": " + " | ".join(findings)
    return Grade("e2e.constraints", respected / (respected + violated), reason)


def grade_recall(case, response, spans):
    """e2e.recall: share of the acceptable sources found in right recommendations.

    A recommendation carries every source of its opportunity, so a repost counts with its original.
    Only right recommendations earn credit (a source next to an excluded one earns none). The
    case must have acceptable sources (None otherwise, as in no_match). A reply with no
    recommendation scores 0 here: nothing found is not a pass.
    """
    acceptable, excluded, uncertain = _labels(case)
    if not acceptable:
        return None
    found = set()
    for sources in _sources(response):
        if _judge(sources, acceptable, excluded, uncertain)[0] == "right":
            found |= acceptable & set(sources)
    missing = sorted(acceptable - found)
    reason = (f"expected all of {sorted(acceptable)}, found {sorted(found)}, missing {missing}; "
              f"status {response.status.value!r}, {len(response.recommendations)} recommendation(s)")
    return Grade("e2e.recall", len(found) / len(acceptable), reason)


def grade_rank(case, response, spans):
    """e2e.rank: 1 / position of the first right recommendation (1.0 means the top answer is right).

    The cases do not rank their acceptable sources, so this only asks that wrong answers do not
    sit above a right one. None when the case has no acceptable source or nothing was returned
    (recall reports that); 0 when answers were returned but none is right.
    """
    acceptable, excluded, uncertain = _labels(case)
    got = _sources(response)
    if not acceptable or not got:
        return None
    for position, sources in enumerate(got, start=1):
        if _judge(sources, acceptable, excluded, uncertain)[0] == "right":
            above = [s for before in got[: position - 1] for s in before]
            return Grade("e2e.rank", 1 / position,
                         f"expected a right recommendation at position 1, first right at {position} of {len(got)}"
                         + (f"; ranked above it: {above}" if above else ""))
    return Grade("e2e.rank", 0.0, f"expected a right recommendation at position 1, none of the {len(got)} returned is right: {got}")


def grade_availability(case, response, spans):
    """e2e.availability: share of recommendations that were still available at the evaluation time.

    Unavailable is proven by a label (excluded for deadline_passed, activity_ended, status_cancelled,
    status_closed or not_yet_open) or by the response itself (its application_status is closed,
    cancelled or not_yet_open, or its deadline date is before the evaluation day: in every shared
    case a deadline before that day is labelled passed and none of the acceptable sources has one).
    Available is proven only by a right recommendation. Uncertain-only ones are not counted.
    """
    acceptable, excluded, uncertain = _labels(case)
    today = _evaluation_day(case)
    available = unavailable = 0
    findings = []
    for rec in response.recommendations:
        sources = list(rec.source_ids)
        verdict, hits, _ = _judge(sources, acceptable, excluded, uncertain)
        if verdict == "ungraded":
            continue
        issues = _tags([(s, r) for s, r in hits if r in AVAILABILITY_REASONS])
        if rec.application_status in UNAVAILABLE_STATUSES:
            issues.append(f"response status {rec.application_status!r}")
        deadline = _as_date(rec.deadline)
        if deadline is not None and today is not None and deadline < today:
            issues.append(f"response deadline {deadline} is before the evaluation day {today}")
        if issues:
            unavailable += 1
            findings.append(f"{sources}: {', '.join(issues)}")
        elif verdict == "right":
            available += 1
    if available + unavailable == 0:
        return None
    reason = (f"expected every recommendation open at {case.get('evaluation_time')}; "
              f"{available} open, {unavailable} not")
    if findings:
        reason += ": " + " | ".join(findings)
    return Grade("e2e.availability", available / (available + unavailable), reason)


def grade_contract(case, response, spans):
    """e2e.contract: the recommendations' ids are real, unique and consistent.

    Every source id exists in the raw announcements, no source appears in two recommendations or
    twice in one, no opportunity is returned twice, and every recommendation has sources. None when
    nothing was recommended. (The checks against the raw CSV are skipped, and said so, if it is missing.)
    """
    recommendations = response.recommendations
    if not recommendations:
        return None
    known = _known_source_ids()
    problems, owner, seen_opportunities = [], {}, set()
    for position, rec in enumerate(recommendations, start=1):
        ids = list(rec.source_ids)
        if not ids:
            problems.append(f"#{position} has no source_ids")
        if len(set(ids)) != len(ids):
            problems.append(f"#{position} repeats a source id: {ids}")
        for source in ids:
            if known is not None and source not in known:
                problems.append(f"#{position} cites {source}, which is not an announcement")
            if owner.setdefault(source, position) != position:
                problems.append(f"{source} is in recommendations #{owner[source]} and #{position}")
        if rec.opportunity_id in seen_opportunities:
            problems.append(f"{rec.opportunity_id} is recommended twice")
        seen_opportunities.add(rec.opportunity_id)
    checked = "ids unique and real" if known is not None else "ids unique (raw CSV not found: existence not checked)"
    return Grade("e2e.contract", float(not problems),
                 f"expected {checked}; got " + ("; ".join(problems) if problems else "no problem"))


GRADERS = [
    grade_status,
    grade_recommendations,
    grade_constraints,
    grade_recall,
    grade_rank,
    grade_availability,
    grade_contract,
]
