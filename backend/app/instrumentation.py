"""Structured fields for trace events (Day 4 - 2.2). Pure functions: nothing here changes a result.

Privacy policy applied: the project defines none for user text, so none is logged. A request is
recorded as a length and a short hash, the saved profile as flags and counts, the model reply as a
length, a hash and its validated fields. Constraints are logged because they are the filters
themselves. Search terms and topics are fragments of the request: a term that occurs in the
announcement titles is kept (it is what an evaluation needs); any other term is replaced by a
short hash ("?1a2b3c4d") so that unknown words typed by a user are not stored.
Secrets are never passed in; Tracer also drops secret-looking names.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlsplit

from .repository import normalize

MAX_RANKING = 20       # candidates listed in retrieval_completed
MAX_EXCLUDED_IDS = 10  # ids sampled per exclusion reason (the counts are complete)


def safe(builder, *args, **kwargs) -> dict[str, Any]:
    """Build attributes without ever raising: instrumentation must not fail a request."""
    try:
        return builder(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"instrumentation_error": f"{getattr(builder, '__name__', 'builder')}:{type(exc).__name__}"}


def fingerprint(text: str) -> dict[str, Any]:
    return {"chars": len(text), "sha256_16": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]}


def title_vocabulary(repository) -> str:
    """All normalized announcement titles joined: the vocabulary that makes a term safe to log."""
    try:
        return repository._vocabulary()[0]
    except Exception:  # noqa: BLE001 - with no vocabulary every term is redacted, never leaked
        return ""


def redact_term(term: str, vocabulary: str) -> str:
    """Keep a term only if it occurs in the titles; otherwise a short hash of it."""
    if normalize(term) in vocabulary:
        return term
    return "?" + hashlib.sha256(term.encode("utf-8")).hexdigest()[:8]


def redact_phrase(text: str, vocabulary: str) -> str:
    return " ".join(redact_term(token, vocabulary) for token in re.findall(r"\S+", text))


def _redacted_reading(reading: dict, vocabulary: str) -> dict:
    return {**reading, "keywords": [redact_term(k, vocabulary) for k in reading.get("keywords", [])]}


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def request_attributes(body, deps, evaluation_time) -> dict[str, Any]:
    c, p, s, cfg = body.constraints, body.profile, deps.settings, deps.model_config
    return {
        "evaluation_time": evaluation_time.isoformat(),
        "evaluation_time_source": "fixed" if s.evaluation_time else "actual",
        "query": fingerprint(body.query),
        "constraint_resolution": body.constraint_resolution,
        "constraints": {
            "opportunity_types": c.opportunity_types, "delivery_modes": c.delivery_modes,
            "location_preference": c.location_preference, "available_from": _iso(c.available_from),
            "available_to": _iso(c.available_to), "max_duration_days": c.max_duration_days,
            "max_budget_sar": c.max_budget_sar,
        },
        "profile": {
            "has_city": bool(p.city), "has_field_of_study": bool(p.field_of_study),
            "has_education_level": bool(p.education_level), "has_user_id": bool(p.user_id),
            "n_interests": len(p.interests), "n_skills": len(p.skills or []), "skills_provided": p.skills is not None,
        },
        "config": {
            "model_configured": cfg is not None,
            "provider": cfg.provider if cfg else None, "model": cfg.model_name if cfg else None,
            "model_timeout_s": s.model_timeout_seconds, "model_max_retries": s.model_max_retries,
            "workflow_timeout_s": s.workflow_timeout_seconds, "jsonl_sink": bool(s.trace_jsonl_path),
        },
    }


def model_attributes(config, rules_reading: dict, diag: dict, plan: dict | None, failure: Exception | None,
                     vocabulary: str) -> dict[str, Any]:
    """What the model step did. `diag` is filled by agent.py from facts the provider actually returned."""
    rejection = None
    if failure is not None:
        rejection = {"code": getattr(failure, "reason", None) or type(failure).__name__,
                     "kind": type(failure).__name__, "public_code": getattr(failure, "public_code", None),
                     "info": getattr(failure, "info", None)}
    upstream = {k: diag.get(k) for k in ("upstream_provider", "upstream_model") if diag.get(k)}
    filters = [{**f, "value": redact_phrase(str(f["value"]), vocabulary) if f.get("field") == "topic" else f.get("value")}
               for f in diag.get("filters") or []]
    shown_plan = None if plan is None else {**plan, "topics": [redact_phrase(t, vocabulary) for t in plan.get("topics", [])]}
    return {
        "configured": {
            "provider": config.provider, "model": config.model_name,
            "host": urlsplit(config.base_url or "").hostname,
            "reasoning_disabled": (config.provider or "").strip().lower() == "openrouter",
        },
        "rules_reading": _redacted_reading(rules_reading, vocabulary),
        "attempts": diag.get("attempts"), "http_status": diag.get("http_status"), "transport_error": diag.get("error_class"),
        "upstream": upstream or None,
        "finish_reason": diag.get("finish_reason"), "usage": diag.get("usage"), "cost_usd": diag.get("cost_usd"),
        "raw": diag.get("raw"),
        "filters_checked": filters,
        "validation": {"accepted": failure is None, "rejection": rejection},
        "plan": shown_plan,
    }


def criteria_attributes(criteria, reading_rules: dict, plan_applied: bool, vocabulary: str) -> dict[str, Any]:
    """The final structured interpretation of the request (what interpreted_filters says in Arabic)."""
    return {
        "plan_applied": plan_applied,
        "rules_reading": _redacted_reading(reading_rules, vocabulary),
        "criteria": {
            "types": criteria.types, "cities": criteria.cities, "delivery_modes": criteria.delivery_modes,
            "either_city_or_mode": criteria.either_city_or_mode, "max_budget_sar": criteria.max_budget,
            "available_from": _iso(criteria.available_from), "available_to": _iso(criteria.available_to),
            "max_duration_days": criteria.max_duration_days,
            "keywords": [redact_term(k, vocabulary) for k in criteria.keywords], "soft_terms": criteria.soft_terms,
            "profile_signals": {"city": bool(criteria.profile_city), "n_skills": len(criteria.profile_skills),
                                "n_interest_terms": len(criteria.profile_interests)}  # interests + field of study,
        },
    }


def retrieval_attributes(criteria, reading_rules: dict, plan_applied: bool, diag: dict, matches: list, top_k: int,
                         vocabulary: str) -> dict[str, Any]:
    excluded = diag.get("excluded_ids", {})
    return {
        **criteria_attributes(criteria, reading_rules, plan_applied, vocabulary),
        "n_opportunities": diag.get("n_opportunities"),
        "n_candidates": len(matches),
        "top_k": top_k,
        "ranking": [{"rank": i, "opportunity_id": m.opportunity.opportunity_id, "score": m.score,
                     "profile_score": m.profile_score, "matched_terms": m.matched_terms, "n_caveats": len(m.caveats),
                     "returned": i <= top_k}
                    for i, m in enumerate(matches[:MAX_RANKING], start=1)],
        "ranking_truncated": len(matches) > MAX_RANKING,
        "excluded_counts": diag.get("excluded_counts", {}),
        "excluded_ids_sample": {reason: ids[:MAX_EXCLUDED_IDS] for reason, ids in excluded.items()},
    }


def eligibility_facts(opp, now, profile_skills: set[str]) -> dict[str, Any]:
    """Facts behind one returned recommendation. `known` separates a stated fact from an unknown one."""
    claims = sorted(d.isoformat() for d in opp.deadline_claims)
    status = opp.application_status(now)
    deadline_state = opp.deadline_state(now)
    required = list(opp.required_skills or [])
    mandatory = list(opp.mandatory or [])
    why = []
    if any("عمر" in s for s in mandatory):
        why.append("age_requirement_not_collected_by_profile")
    if any("السنة الأخيرة" in s for s in mandatory):
        why.append("study_year_requirement_not_collected_by_profile")
    why += [f"required_skill_not_in_profile:{s}" for s in required if s.lower() not in profile_skills]
    if status in ("requires_verification", "unknown", "not_yet_open"):
        why.append(f"registration_status_{status}")
    if deadline_state in ("unknown", "uncertain"):
        why.append(f"deadline_{deadline_state}" + ("_conflicting" if len(claims) > 1 else ""))
    if opp.cost is None:
        why.append("fee_conflicting" if len(opp.cost_claims) > 1 else "fee_unknown")
    return {
        "opportunity_id": opp.opportunity_id,
        "source_ids": opp.source_ids,
        "eligibility_status": "uncertain",
        "uncertain_because": why,
        "application_status": status,
        "cancelled": opp.cancelled,
        "deadline": {"known": bool(claims), "claims": claims, "state": deadline_state, "conflicting": len(claims) > 1},
        "registration_statement": {
            "statement": opp.status_statement, "published_on": opp.status_published_at[:10] if opp.status_published_at else None,
            "opens_on": _iso(opp.opens_on),
        },
        "fee": {"known": opp.cost is not None, "value_sar": opp.cost, "claims_sar": opp.cost_claims,
                "conflicting": opp.cost is None and len(opp.cost_claims) > 1},
        "schedule": {"start": _iso(opp.start_date), "end": _iso(opp.end_date), "duration_days": opp.duration_days},
        "delivery": {"mode": opp.delivery_mode, "location": opp.location,
                     "physical_attendance_required": opp.physical_attendance_required},
        "requirements": {"mandatory_count": len(mandatory), "required_skills": required,
                         "profile_has_skill": {s: s.lower() in profile_skills for s in required}},
        "type": opp.opportunity_type,
        "data_warnings": len(opp.warnings),
    }


def response_attributes(result, deps) -> dict[str, Any]:
    recs = result.recommendations
    attrs: dict[str, Any] = {
        "status": result.status.value,
        "n_recommendations": len(recs),
        "opportunity_ids": [r.opportunity_id for r in recs],
        "evidence_version": (recs[0].evidence_version if recs else None) or getattr(deps.repository, "evidence_version", None),
        "evaluation_time": deps.evaluation_time.isoformat() if deps.evaluation_time else None,
    }
    if result.status.value == "needs_info":
        attrs["needs_info"] = {"missing_fields": result.missing_fields, "constraint_conflict": result.constraint_conflict}
    elif result.status.value == "no_match":
        attrs["no_match"] = {"cause": "no_candidates_after_filters", "see": "retrieval_completed.excluded_counts"}
    elif result.status.value == "error":
        attrs["error"] = {"error_code": result.error_code}
    return attrs
