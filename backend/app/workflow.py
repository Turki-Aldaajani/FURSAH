# Learner guidance: Return interpreted_filters and relevant missing_explanations. For
# incompatible query/profile constraints, return needs_info with constraint_conflict=True
# and the affected constraints.* missing field. Honor constraint_resolution only for that
# request; never overwrite the saved profile. Enforce
# deps.settings.workflow_timeout_seconds and return 1–3 grounded recommendations only for
# ok.
#
# Team implementation (first working path): deterministic, no model yet. The query is
# read for type/city/delivery/budget words and topic keywords; these are merged with
# the request constraints; the repository filters stated facts; the top three matches
# are returned with every source ID. Eligibility is never confirmed. A model client
# can later replace interpret_query() or re-rank/explain the candidates.
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import asdict, dataclass, field, replace

from . import instrumentation as ins
from .agent import model_filters
from .infrastructure.dependencies import AppDependencies
from .infrastructure.errors import FursahError, WorkflowBudgetExceededError
from .infrastructure.observability import start_span
from .repository import Match, SearchCriteria, normalize, strip_prefix
from .schemas import DiscoveryRequest, DiscoveryResponse, DiscoveryStatus, Recommendation

TYPE_LABELS = {"workshop": "ورشة عمل", "course": "دورة", "internship": "تدريب عملي",
               "competition": "مسابقة", "volunteering": "تطوع", "event": "فعالية"}
MODE_LABELS = {"in_person": "حضوري", "online": "عن بُعد", "hybrid": "مدمج"}
# Matched on normalized query text. Bare "تدريب" is ambiguous (course or internship) and
# is not mapped; "تحدي" needs a word boundary so that "تحديد" does not match.
QUERY_TYPES = {
    "workshop": r"ورش",
    "course": r"\bدور(?:ه|ات)\b",
    "internship": r"تدريب (?:عملي|ميداني|مهني|تعاوني)",
    "competition": r"مسابق|منافس|\bتحدي\b|هاكاثون",
    "volunteering": r"تطوع",
    "event": r"\bفعالي(?:ه|ات)\b|امسيه|لقاء مفتوح",
}
QUERY_MODES = {"online": r"عن بعد|عن بُعد|اونلاين|online", "in_person": r"حضوري", "hybrid": r"مدمج|هجين"}
# Technology names are written in Latin letters in the data ("Python", "Power BI") and often in
# Arabic letters in queries. Patterns run on normalized text; the value is the Latin name that
# occurs in titles. Only names that occur in this corpus are listed.
TECH_TERMS = (
    (r"باور بي (?:اي|اى)|power ?bi", "power bi"),
    (r"اس كيو (?:ال|ايل)|\bsql\b", "sql"),
    (r"بايثون|بايثن|\bpython\b", "python"),
    (r"اكسل|\bexcel\b", "excel"),
)
# Level/audience words describe the attendee, not the subject: they may rank, never match a topic.
AUDIENCE_TERMS = {"مبتدئين", "مبتدئ"}
STOPWORDS = set(normalize(w) for w in (
    "أبحث ابحث أريد اريد أبغى ابغى ودي عن في من على إلى الى مع لي هل يوجد أو او و ما التي الذي "
    "هذا هذه بعد قبل أي اي كل حول بدون غير فرصة فرص فرصه مجال موضوع ريال مجاني مجانية مجانا "
    "بأقل باقل أقل اقل حتى ميزانية ميزانيتي عمل تدريب تدريبية تدريبي ورشة ورش دورة دورات "
    "مسابقة مسابقات منافسة تحدي تطوع تطوعي تطوعية فعالية فعاليات لقاء مفتوح أمسية حضوري "
    "اونلاين أونلاين مدمج مدينة مدينتي قريب قريبة "
    # Conversational words ("بدوّر على شي ينفعني"): not topics, and absent from every title and body.
    "ادور بدور دور ابي ابى تنفع ينفع تنفعني ينفعني").split())
@dataclass
class QueryReading:
    types: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    delivery_modes: list[str] = field(default_factory=list)
    either_city_or_mode: bool = False  # "في أبها أو عن بعد": alternatives, not both
    max_budget: float | None = None
    keywords: list[str] = field(default_factory=list)
    soft_terms: list[str] = field(default_factory=list)


def _drop(text: str, pattern: str) -> str:
    """Remove every whole word that contains a match, so no fragment is left behind."""
    return re.sub(r"\S*(?:" + pattern + r")\S*", " ", text)


def interpret_query(query: str, known_cities: list[str]) -> QueryReading:
    text = normalize(query)
    reading = QueryReading()
    reading.types = [t for t, p in QUERY_TYPES.items() if re.search(p, text)]
    reading.delivery_modes = [m for m, p in QUERY_MODES.items() if re.search(p, text)]
    city_forms = {normalize(c): c for c in known_cities}
    city_forms[normalize("مكة")] = "مكة المكرمة"
    reading.cities = list(dict.fromkeys(c for form, c in city_forms.items() if form in text))
    if re.search(r"مجان", text):
        reading.max_budget = 0.0
    elif (m := re.search(r"([0-9]+(?:\.[0-9]+)?)\s*ريال", text)):
        reading.max_budget = float(m.group(1))
    if reading.cities and reading.delivery_modes:
        cities = "|".join(re.escape(f) for f, c in city_forms.items() if c in reading.cities)
        modes = "|".join(QUERY_MODES[m] for m in reading.delivery_modes)
        reading.either_city_or_mode = bool(re.search(
            rf"(?:{cities})\S*\s+او\s+(?:{modes})|(?:{modes})\s+او\s+(?:في\s+)?\S*(?:{cities})", text))
    # What remains after removing technology names, activity types, delivery modes and
    # cities is the subject of the request.
    remaining = text
    for pattern, canonical in TECH_TERMS:
        if re.search(pattern, remaining):
            reading.keywords.append(canonical)
            remaining = _drop(remaining, pattern)
    for pattern in [*QUERY_TYPES.values(), *QUERY_MODES.values(), *(re.escape(f) for f in city_forms)]:
        remaining = _drop(remaining, pattern)
    for word in re.findall(r"[؀-ۿA-Za-z]+", remaining):
        if word in STOPWORDS:
            continue
        word = strip_prefix(word)
        if len(word) < 3 or word in STOPWORDS:
            continue
        (reading.soft_terms if word in AUDIENCE_TERMS else reading.keywords).append(word)
    reading.keywords = list(dict.fromkeys(reading.keywords))
    reading.soft_terms = list(dict.fromkeys(reading.soft_terms))
    return reading


def _merge(name, from_query, from_request, resolution, conflicts):
    """Query and saved constraints; disjoint values are a conflict unless resolved."""
    if not from_query:
        return from_request or []
    if not from_request:
        return from_query
    common = [v for v in from_query if v in from_request]
    if common:
        return common
    if resolution == "query":
        return from_query
    if resolution == "profile":
        return from_request
    conflicts.append(name)
    return []


def application_is_ready(deps: AppDependencies) -> bool:
    """Ready when the opportunity store is loaded. No model client is used yet."""
    return deps.repository.is_ready() is True


def _recommendation(match: Match, deps: AppDependencies, profile_skills: set[str]) -> Recommendation:
    opp = match.opportunity
    caveats = list(match.caveats)
    for statement in opp.mandatory or []:
        caveats.append(f"شرط من الإعلان: {statement}")
    for skill in opp.required_skills or []:
        if skill.lower() not in profile_skills:
            caveats.append(f"لم تذكر {skill} ضمن مهاراتك، والإعلان يطلبها.")
    caveats.extend(w for w in opp.warnings if w not in caveats)
    parts = [TYPE_LABELS.get(opp.opportunity_type, "نشاط")]
    parts.append(f"في {opp.location}" if opp.location else MODE_LABELS.get(opp.delivery_mode, ""))
    if match.matched_terms:
        parts.append("ويطابق طلبك في: " + "، ".join(match.matched_terms))
    reason = " ".join(p for p in parts if p) + "."
    if match.profile_matches:
        reason += " يتوافق مع ملفك: " + "، ".join(match.profile_matches) + "."
    if opp.type_evidence:
        reason += f" من نص الإعلان: «{opp.type_evidence}»"
    return Recommendation(
        evidence_version=deps.repository.evidence_version,
        opportunity_id=opp.opportunity_id,
        title=opp.title,
        opportunity_type=opp.opportunity_type,
        location=opp.location,
        delivery_mode=opp.delivery_mode,
        deadline=opp.deadline.isoformat() if opp.deadline else None,
        application_status=opp.application_status(deps.evaluation_time),
        eligibility_status="uncertain",
        reason=reason,
        caveats=caveats,
        source_ids=opp.source_ids,
        next_action=f"راجع الإعلان الأصلي قبل التقديم: {opp.url}",
    )


def _apply_model_plan(reading: QueryReading, plan: dict, known_cities: list[str]) -> QueryReading:
    """Add what the validated model plan found; the deterministic reading is never reduced."""
    merged = replace(
        reading,
        types=list(dict.fromkeys([*reading.types, *plan["types"]])),
        cities=list(dict.fromkeys([*reading.cities, *plan["cities"]])),
        delivery_modes=list(dict.fromkeys([*reading.delivery_modes, *plan["delivery_modes"]])),
        max_budget=reading.max_budget if reading.max_budget is not None else plan["max_budget"],
        either_city_or_mode=reading.either_city_or_mode or plan["either"],
        keywords=list(reading.keywords), soft_terms=list(reading.soft_terms),
    )
    for topic in plan["topics"]:
        # The topic goes through the same reader, so Arabic spellings of technologies
        # map to the names used in titles and filler/level words are handled the same way.
        read = interpret_query(topic, known_cities)
        merged.keywords = list(dict.fromkeys([*merged.keywords, *read.keywords]))
        merged.soft_terms = list(dict.fromkeys([*merged.soft_terms, *read.soft_terms]))
    return merged


def _set_criteria(span, criteria: SearchCriteria, repo) -> None:
    """Day 4 span attributes: the structured reading, with unknown words hashed (instrumentation.py)."""
    attributes = ins.safe(ins.criteria_attributes, criteria, {}, False, ins.title_vocabulary(repo)).get("criteria", {})
    span.set_attribute("output.value", json.dumps(attributes, ensure_ascii=False))


def _discover(request: DiscoveryRequest, deps: AppDependencies, plan: dict | None = None,
              interpretation: str | None = None) -> DiscoveryResponse:
    repo = deps.repository
    known_cities = sorted({o.location for o in repo.all() if o.location})
    with start_span("query.interpret", request_id=request.request_id, model_plan_applied=plan is not None) as span:
        reading = interpret_query(request.query, known_cities)
        rules_reading = asdict(reading)
        if plan is not None:
            reading = _apply_model_plan(reading, plan, known_cities)
        c = request.constraints
        conflicts: list[str] = []
        types = _merge("constraints.opportunity_types", reading.types, c.opportunity_types,
                       request.constraint_resolution, conflicts)
        cities = _merge("constraints.location_preference", reading.cities, c.location_preference,
                        request.constraint_resolution, conflicts)
        modes = _merge("constraints.delivery_modes", reading.delivery_modes, c.delivery_modes,
                       request.constraint_resolution, conflicts)
        if conflicts:
            span.set_attribute("conflicts", conflicts)
            return DiscoveryResponse(
                request_id=request.request_id, status=DiscoveryStatus.NEEDS_INFO,
                message="طلبك يختلف عن تفضيلاتك المحفوظة. أيهما نعتمد لهذا البحث؟",
                missing_fields=conflicts, constraint_conflict=True,
                missing_explanations=[f"قيمة في نص الطلب تتعارض مع {name}" for name in conflicts],
            )
        budgets = [b for b in (reading.max_budget, c.max_budget_sar) if b is not None]
        # The alternatives reading applies only to what the query itself said, not to merged saved values.
        either = reading.either_city_or_mode and cities == reading.cities and modes == reading.delivery_modes
        profile = request.profile
        criteria = SearchCriteria(
            evaluation_time=deps.evaluation_time, types=types, cities=cities, delivery_modes=modes,
            either_city_or_mode=either,
            max_budget=min(budgets) if budgets else None, available_from=c.available_from,
            available_to=c.available_to, max_duration_days=c.max_duration_days, keywords=reading.keywords,
            soft_terms=reading.soft_terms, profile_city=profile.city,
            profile_skills=[s.name for s in profile.skills or []],
            profile_interests=[*profile.interests, *([profile.field_of_study] if profile.field_of_study else [])],
        )
        _set_criteria(span, criteria, deps.repository)
    filters = [f"وقت التقييم: {deps.evaluation_time.isoformat()}",
               "استبعاد الفرص الملغاة والمغلقة والمنتهية الموعد"]
    if types:
        filters.append("النوع: " + "، ".join(TYPE_LABELS[t] for t in types))
    if either:
        filters.append("المكان (بدائل): " + " أو ".join([*cities, *(MODE_LABELS[m] for m in modes)]))
    else:
        if cities:
            filters.append("المدينة: " + "، ".join(cities) + " (الأنشطة عن بُعد مشمولة)")
        if modes:
            filters.append("طريقة التقديم: " + "، ".join(MODE_LABELS[m] for m in modes))
    if criteria.max_budget is not None:
        filters.append(f"أقصى ميزانية: {criteria.max_budget:g} ريال (الرسوم المجهولة لا تُعد مجانية)")
    if c.available_from or c.available_to:
        filters.append(f"الفترة: {c.available_from or '…'} إلى {c.available_to or '…'}")
    if c.max_duration_days:
        filters.append(f"أقصى مدة: {c.max_duration_days} يوم")
    if reading.keywords:
        filters.append("كلمات الموضوع: " + "، ".join(reading.keywords))
    if interpretation:
        filters.append(interpretation)
    search_diag: dict = {}
    started = time.perf_counter()
    with start_span("tool.search_opportunities", **{
            "openinference.span.kind": "TOOL", "tool.name": "search_opportunities", "request_id": request.request_id,
            "input.value": json.dumps(ins.safe(ins.criteria_attributes, criteria, rules_reading, plan is not None,
                                               ins.title_vocabulary(repo))["criteria"], ensure_ascii=False)}) as span:
        candidates = repo.search(criteria, diagnostics=search_diag)
        span.set_attribute("n_candidates", len(candidates))
        span.set_attribute("candidate_ids", [m.opportunity.opportunity_id for m in candidates[:ins.MAX_RANKING]])
        span.set_attribute("excluded_counts", json.dumps(search_diag.get("excluded_counts", {})))
    deps.tracer.emit(request_id=request.request_id, step="retrieval_completed", component="repository",
                     outcome="candidates" if candidates else "no_candidates",
                     elapsed_ms=(time.perf_counter() - started) * 1000,
                     attributes=ins.safe(ins.retrieval_attributes, criteria, rules_reading, plan is not None,
                                         search_diag, candidates, 3, ins.title_vocabulary(repo)))
    matches = candidates[:3]
    if not matches:
        return DiscoveryResponse(
            request_id=request.request_id, status=DiscoveryStatus.NO_MATCH,
            message="لم نجد فرصة تدعم الأدلة مطابقتها لطلبك.", interpreted_filters=filters,
        )
    skills = {s.name.lower() for s in request.profile.skills or []}
    recommendations = [_recommendation(m, deps, skills) for m in matches]
    started = time.perf_counter()
    deps.tracer.emit(request_id=request.request_id, step="eligibility_checked", component="workflow",
                     outcome="uncertain" if all(r.eligibility_status == "uncertain" for r in recommendations) else "mixed",
                     elapsed_ms=(time.perf_counter() - started) * 1000,
                     evidence_ids=[sid for m in matches for sid in m.opportunity.source_ids],
                     attributes={"recommendations": [ins.safe(ins.eligibility_facts, m.opportunity, deps.evaluation_time, skills)
                                                       for m in matches]})
    explanations = []
    if any(m.opportunity.mandatory for m in matches):
        explanations.append("بعض الإعلانات تشترط العمر أو السنة الدراسية أو مهارة؛ الملف الشخصي لا يجمع العمر "
                            "ولا السنة الدراسية، لذلك تبقى الأهلية غير مؤكدة.")
    return DiscoveryResponse(
        request_id=request.request_id, status=DiscoveryStatus.OK,
        message=f"وجدنا {len(recommendations)} من الفرص المطابقة للمعايير المتاحة. الأهلية غير مؤكدة؛ راجع الملاحظات.",
        recommendations=recommendations, interpreted_filters=filters, missing_explanations=explanations,
    )


async def discover(
    request: DiscoveryRequest,
    deps: AppDependencies,
) -> DiscoveryResponse:
    """API integration entry point: request + dependencies -> DiscoveryResponse.

    Uses deps.evaluation_time, captured once for this request by the route.
    """
    if deps.evaluation_time is None:
        deps = replace(deps, evaluation_time=deps.settings.resolve_evaluation_time())
    # Day 4 root span: every step below nests under it (no-op unless tracing is set up).
    with start_span("workflow.discover", request_id=request.request_id,
                    evaluation_time=deps.evaluation_time.isoformat(),
                    **{"input.chars": len(request.query)}) as span:
        try:
            result = await asyncio.wait_for(_interpret_then_search(request, deps),
                                            timeout=deps.settings.workflow_timeout_seconds)
        except TimeoutError as exc:
            raise WorkflowBudgetExceededError() from exc
        span.set_attribute("output.status", result.status.value)
        span.set_attribute("output.opportunity_ids", [r.opportunity_id for r in result.recommendations])
        return result


async def _interpret_then_search(request: DiscoveryRequest, deps: AppDependencies) -> DiscoveryResponse:
    """Optional model interpretation first; any failure leaves the deterministic reading."""
    plan, interpretation = None, None
    if deps.model_config is not None:
        started = time.perf_counter()
        known_cities = sorted({o.location for o in await asyncio.to_thread(deps.repository.all) if o.location})
        explicit = interpret_query(request.query, known_cities)
        diag: dict = {}
        failure = None
        # One LLM span per model step. The request text is not recorded: only its length (privacy policy).
        with start_span("llm.generate", **{"openinference.span.kind": "LLM", "request_id": request.request_id,
                                           "llm.model_name": deps.model_config.model_name,
                                           "input.chars": len(request.query)}) as span:
            try:
                plan = await model_filters(
                    request.query,
                    {"types": list(TYPE_LABELS), "delivery_modes": list(MODE_LABELS), "cities": known_cities},
                    {"types": explicit.types, "cities": explicit.cities, "delivery_modes": explicit.delivery_modes,
                     "max_budget": explicit.max_budget},
                    deps, diag=diag)
                outcome, interpretation = "accepted", "تفسير الطلب: بمساعدة النموذج"
            except FursahError as exc:
                # Provider failure or a plan that failed validation: keep searching with the base rules.
                failure = exc
                outcome, interpretation = exc.public_code, "تفسير الطلب: القواعد الأساسية (تعذّر اعتماد تفسير النموذج)"
            usage = diag.get("usage", {})
            for key, name in (("prompt_tokens", "llm.token_count.prompt"), ("completion_tokens", "llm.token_count.completion")):
                if usage.get(key) is not None:
                    span.set_attribute(name, usage[key])
            span.set_attribute("model.outcome", outcome)
            span.set_attribute("model.attempts", diag.get("attempts", 0))
            if plan is not None:
                span.set_attribute("output.value", json.dumps(
                    {k: plan.get(k) for k in ("types", "cities", "delivery_modes") if k in plan}, ensure_ascii=False))
        deps.tracer.emit(request_id=request.request_id, step="model_interpretation", component="model",
                         outcome=outcome, elapsed_ms=(time.perf_counter() - started) * 1000,
                         attributes=ins.safe(ins.model_attributes, deps.model_config, asdict(explicit), diag, plan, failure,
                                             ins.title_vocabulary(deps.repository)))
    return await asyncio.to_thread(_discover, request, deps, plan, interpretation)
