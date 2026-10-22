# Learner guidance: prepare data/raw/raw_opportunities.csv using data/README.md.
# Choose your own storage and internal schema. The bridge is an optional CSV/JSON
# helper. Map your storage into the public API contract; rename/split this module freely.
#
# Team implementation (first working path): the store is the notebook export
# data/prepared/source_preparation.json (one row per announcement). Sources are
# grouped into opportunities by the key (activity URL, title, publisher), verified
# on the corpus to give 900 opportunities. Every fact keeps its source IDs; facts
# that sources disagree on stay unresolved and are reported as data warnings.
from __future__ import annotations

import collections
import hashlib
import importlib.util
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .infrastructure.config import get_settings
from .infrastructure.data_package import DatasetLoader
from .infrastructure.errors import DataAccessError
from .schemas import EligibilityRequirements, OpportunityDetailsResponse

PREPARED_FILE = Path("prepared") / "source_preparation.json"
TYPE_RULES_FILE = "opportunity_type_rules.py"  # single copy lives in data/
RIYADH = timezone(timedelta(hours=3))  # Asia/Riyadh has no DST; avoids a tzdata dependency.
MONTHS = {"يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "مايو": 5, "يونيو": 6,
          "يوليو": 7, "أغسطس": 8, "سبتمبر": 9, "أكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12}
# Sources write "مكة"; the frontend city list writes "مكة المكرمة".
CITY_ALIASES = {"مكة": "مكة المكرمة"}

_DATE = r"([0-9]{1,2}) (\S+) ([0-9]{4})"
_CANCELLED = re.compile(r"أُلغيت هذه الفرصة|تم إلغاء النشاط|ألغوه")
_CLOSED = re.compile(r"باب التقديم مغلق|أُغلق التسجيل لاكتمال المقاعد|انتهى استقبال الطلبات لهذه النسخة")
_OPENS = re.compile(rf"لم يبدأ التسجيل بعد؛ يفتح يوم {_DATE}")
_OPEN = re.compile(r"التسجيل مفتوح حالياً|تُستقبل الطلبات الآن|باب المشاركة مفتوح")


def _date(day: str, month: str, year: str) -> date | None:
    number = MONTHS.get(month.strip())
    try:
        return date(int(year), number, int(day)) if number else None
    except ValueError:
        return None


def _sentences(text: str, pattern: str) -> list[str]:
    parts = re.split(r"(?<=[.؟!])\s+|\r?\n", text)
    return list(dict.fromkeys(p.strip() for p in parts if re.search(pattern, p)))


def _load_type_rules(data_dir: Path):
    path = data_dir / TYPE_RULES_FILE
    spec = importlib.util.spec_from_file_location("fursah_opportunity_type_rules", path)
    if spec is None or spec.loader is None or not path.is_file():
        raise DataAccessError(f"Type rules not found at {path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.classify_type


def deadline_state(deadline: date | None, evaluation_time: datetime) -> str:
    """Date-only deadlines (data/README: a same-day deadline without a cutoff time stays uncertain).

    Before the evaluation day the deadline has passed; on that day it is uncertain; after it,
    it has not passed. The cutoff time and timezone are not stated, so the source date is never shifted.
    """
    if deadline is None:
        return "unknown"
    today = evaluation_time.astimezone(RIYADH).date()
    if deadline < today:
        return "passed"
    if deadline > today:
        return "not_passed"
    return "uncertain"


@dataclass
class Opportunity:
    opportunity_id: str
    title: str
    publisher: str
    url: str
    sources: list[dict[str, Any]]  # prepared rows, oldest publication first
    opportunity_type: str | None = None
    type_evidence: str | None = None
    location: str | None = None
    delivery_mode: str | None = None
    physical_attendance_required: bool | None = None
    remote_participation_allowed: bool | None = None
    start_date: date | None = None
    end_date: date | None = None
    session_count: int | None = None
    hours_per_session: float | None = None
    deadline: date | None = None
    deadline_claims: list[date] = field(default_factory=list)
    cost: float | None = None
    cost_claims: list[float] = field(default_factory=list)
    cancelled: bool = False
    status_statement: str | None = None  # latest source: closed / not_yet_open / open
    status_published_at: str | None = None
    opens_on: date | None = None
    mandatory: list[str] | None = None
    preferred: list[str] | None = None
    required_skills: list[str] | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def source_ids(self) -> list[str]:
        return [s["source_id"] for s in self.sources]

    @property
    def duration_days(self) -> int | None:
        if self.start_date and self.end_date and self.end_date >= self.start_date:
            return (self.end_date - self.start_date).days + 1
        return None

    def deadline_state(self, evaluation_time: datetime) -> str:
        """Combine the states of all deadline claims; disagreeing claims stay uncertain."""
        states = {deadline_state(d, evaluation_time) for d in self.deadline_claims}
        if not states:
            return "unknown"
        return states.pop() if len(states) == 1 else "uncertain"

    def application_status(self, evaluation_time: datetime) -> str:
        if self.cancelled:
            return "cancelled"
        if self.status_statement == "closed" or self.deadline_state(evaluation_time) == "passed":
            return "closed"
        today = evaluation_time.astimezone(RIYADH).date()
        if self.status_statement == "not_yet_open" and self.opens_on and self.opens_on > today:
            return "not_yet_open"
        # An "open" statement describes publication time, not the current state.
        return "requires_verification" if self.status_statement else "unknown"


# A title word used by at least this share of distinct titles is template filler
# ("خطوة بخطوة", "للمجتمع", "من الفكرة إلى التطبيق"...), not a topic. On this corpus the
# filler words sit at 16.7% and the most common real topic word ("تصميم") at 9.7%.
GENERIC_TITLE_SHARE = 0.15
PREFIXES = ("وال", "بال", "فال", "كال", "لل", "ال", "و", "ب", "ل", "ف")


@dataclass
class SearchCriteria:
    evaluation_time: datetime
    types: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    delivery_modes: list[str] = field(default_factory=list)
    # "in Abha or remote": the city and the delivery mode are alternatives, not both required.
    either_city_or_mode: bool = False
    max_budget: float | None = None
    available_from: date | None = None
    available_to: date | None = None
    max_duration_days: int | None = None
    keywords: list[str] = field(default_factory=list)
    soft_terms: list[str] = field(default_factory=list)  # ranking only, never a filter
    # Profile signals: ranking only, never a filter.
    profile_city: str | None = None
    profile_skills: list[str] = field(default_factory=list)
    profile_interests: list[str] = field(default_factory=list)


@dataclass
class Match:
    opportunity: Opportunity
    score: int
    matched_terms: list[str]
    caveats: list[str]
    profile_score: int = 0
    profile_matches: list[str] = field(default_factory=list)


def normalize(text: str) -> str:
    """Arabic letter-form normalization for matching only; stored text is untouched."""
    text = re.sub(r"[ً-ْـ]", "", text)
    text = re.sub(r"[أإآ]", "ا", text)
    return text.replace("ة", "ه").replace("ى", "ي").lower()


def strip_prefix(word: str) -> str:
    """Drop one attached article/preposition (بالرياض -> رياض) when 3+ letters remain."""
    for prefix in PREFIXES:
        if word.startswith(prefix) and len(word) - len(prefix) >= 3:
            return word[len(prefix):]
    return word


def _source_facts(row: dict[str, Any]) -> dict[str, Any]:
    text = row["content"]
    facts: dict[str, Any] = {"is_update": "تحديث من" in text}
    extended = re.search(rf"مُدد آخر يوم للتقديم من {_DATE} إلى {_DATE}", text)
    stated = re.search(rf"آخر يوم للتقديم {_DATE}", text)
    facts["deadline"] = _date(*extended.groups()[3:]) if extended else (_date(*stated.groups()) if stated else None)
    facts["cost"] = row.get("cost_sar")
    span = re.search(rf"يبدأ البرنامج يوم {_DATE} وينتهي يوم {_DATE}", text)
    one_day = re.search(rf"ليوم واحد بتاريخ {_DATE}", text)
    if span:
        facts["start_date"], facts["end_date"] = _date(*span.groups()[:3]), _date(*span.groups()[3:])
    elif one_day:
        facts["start_date"] = facts["end_date"] = _date(*one_day.groups())
    sessions = re.search(r"يشمل ([0-9]+) لقاءات، مدة كل لقاء (ساعتان|ساعة واحدة|([0-9]+) ساعات)", text)
    if sessions:
        facts["session_count"] = int(sessions.group(1))
        facts["hours_per_session"] = {"ساعتان": 2.0, "ساعة واحدة": 1.0}.get(sessions.group(2)) or float(sessions.group(3))
    if "عن بعد بالكامل" in text:
        facts.update(delivery_mode="online", location=None,
                     physical_attendance_required=False, remote_participation_allowed=True)
    elif (m := re.search(r"البداية حضورية في (.+?)، ثم", text)):
        # The first meeting is in person and required; follow-up is remote.
        facts.update(delivery_mode="hybrid", location=m.group(1), physical_attendance_required=True)
    elif (m := re.search(r"الحضور في (.+?) داخل مقر|العمل حضوري داخل مقر .+? في (.+?)\.", text)):
        facts.update(delivery_mode="in_person", location=m.group(1) or m.group(2), physical_attendance_required=True)
    if facts.get("location"):
        facts["location"] = CITY_ALIASES.get(facts["location"], facts["location"])
    facts["cancelled"] = bool(_CANCELLED.search(text))
    if _CLOSED.search(text):
        facts["status"] = "closed"
    elif (m := _OPENS.search(text)):
        facts["status"], facts["opens_on"] = "not_yet_open", _date(*m.groups())
    elif _OPEN.search(text):
        facts["status"] = "open"
    facts["mandatory"] = _sentences(text, r"يشترط|معرفة [A-Za-z ]+ مطلوبة")
    facts["preferred"] = _sentences(text, r"ميزة إضافية وليست شرطاً")
    facts["skills"] = re.findall(r"معرفة ([A-Za-z][A-Za-z ]*?) مطلوبة", text)
    return facts


LABELS = {"deadline": "موعد التقديم", "cost": "الرسوم", "start_date": "تاريخ البداية",
          "end_date": "تاريخ النهاية", "session_count": "عدد اللقاءات",
          "hours_per_session": "مدة اللقاء", "delivery_mode": "طريقة التقديم", "location": "الموقع"}


def _resolve(name: str, per_source: list[tuple[str, dict[str, Any]]], warnings: list[str]):
    """One value when sources agree; an explicit update wins for the fields it states;
    otherwise the field stays None and the disagreement is reported."""
    claims = [(sid, f[name], f["is_update"]) for sid, f in per_source if f.get(name) is not None]
    if not claims:
        return None
    updates = [c for c in claims if c[2]]
    if updates:
        return updates[-1][1]
    if len({c[1] for c in claims}) == 1:
        return claims[0][1]
    warnings.append(f"تعارض في {LABELS[name]} بين المصادر: "
                    + "، ".join(f"{sid}: {value}" for sid, value, _ in claims))
    return None


def _title(row: dict[str, Any]) -> str:
    # Verified on all 152 multi-candidate rows: dropping repeats and the publisher leaves one.
    candidates = list(dict.fromkeys(t for t in row["title_candidates"] if t != row["source_name"]))
    if len(candidates) != 1:
        raise DataAccessError(f"No single title for {row['source_id']}.")
    return candidates[0]


def build_opportunity(key: tuple[str, str, str], rows: list[dict[str, Any]], classify) -> Opportunity:
    url, title, publisher = key
    rows = sorted(rows, key=lambda r: r["published_at"])
    digest = hashlib.sha1("\n".join(key).encode("utf-8")).hexdigest()[:12]
    opp = Opportunity(opportunity_id=f"opp-{digest}", title=title, publisher=publisher, url=url, sources=rows)
    per_source = [(r["source_id"], _source_facts(r)) for r in rows]
    for name in ("start_date", "end_date", "session_count", "hours_per_session", "delivery_mode", "location"):
        setattr(opp, name, _resolve(name, per_source, opp.warnings))
    if opp.delivery_mode:
        first = next(f for _, f in per_source if f.get("delivery_mode") == opp.delivery_mode)
        opp.physical_attendance_required = first.get("physical_attendance_required")
        opp.remote_participation_allowed = first.get("remote_participation_allowed")
    opp.deadline = _resolve("deadline", per_source, opp.warnings)
    opp.deadline_claims = [opp.deadline] if opp.deadline else sorted({f["deadline"] for _, f in per_source if f["deadline"]})
    opp.cost = _resolve("cost", per_source, opp.warnings)
    opp.cost_claims = [opp.cost] if opp.cost is not None else sorted({f["cost"] for _, f in per_source if f["cost"] is not None})
    opp.cancelled = any(f["cancelled"] for _, f in per_source)
    stated = [(r, f) for r, (_, f) in zip(rows, per_source) if f.get("status")]
    if stated:
        row, facts = stated[-1]
        opp.status_statement, opp.status_published_at, opp.opens_on = facts["status"], row["published_at"], facts.get("opens_on")
    mandatory = list(dict.fromkeys(s for _, f in per_source for s in f["mandatory"]))
    preferred = list(dict.fromkeys(s for _, f in per_source for s in f["preferred"]))
    skills = list(dict.fromkeys(s for _, f in per_source for s in f["skills"]))
    opp.mandatory, opp.preferred, opp.required_skills = mandatory or None, preferred or None, skills or None
    types = {}
    for row in rows:
        result = classify(row["content"], row["source_name"])
        if result["type_status"] == "classified":
            types.setdefault(result["opportunity_type"], result["type_evidence"])
    if len(types) == 1:
        opp.opportunity_type, opp.type_evidence = next(iter(types.items()))
    elif len(types) > 1:
        opp.warnings.append("تختلف المصادر في نوع النشاط: " + "، ".join(sorted(types)))
    if opp.cancelled:
        opp.warnings.append("أحد المصادر يعلن إلغاء هذه الفرصة.")
    return opp


class OpportunityRepository:
    """Prepared source rows -> opportunities with source evidence."""

    def __init__(self, bridge: DatasetLoader) -> None:
        self.bridge = bridge
        self._opportunities: dict[str, Opportunity] | None = None
        self._by_source: dict[str, str] = {}
        self._vocabulary_cache: tuple[str, set[str]] | None = None
        self.evidence_version: str | None = None

    def _load(self) -> dict[str, Opportunity]:
        if self._opportunities is not None:
            return self._opportunities
        path = self.bridge.data_dir / PREPARED_FILE
        try:
            raw = path.read_bytes()
            rows = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise DataAccessError(f"Prepared sources are unreadable at {path}.") from exc
        if not isinstance(rows, list) or not rows:
            raise DataAccessError("Prepared sources must be a nonempty JSON array.")
        if len({r["source_id"] for r in rows}) != len(rows):
            raise DataAccessError("Prepared sources contain duplicate source IDs.")
        classify = _load_type_rules(self.bridge.data_dir)
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault((row["activity_url"], _title(row), row["source_name"]), []).append(row)
        opportunities = {}
        for key, members in groups.items():
            opp = build_opportunity(key, members, classify)
            opportunities[opp.opportunity_id] = opp
            for sid in opp.source_ids:
                self._by_source[sid] = opp.opportunity_id
        self.evidence_version = f"{PREPARED_FILE.name}:{hashlib.md5(raw).hexdigest()[:12]}"
        self._opportunities = opportunities
        return opportunities

    def is_ready(self) -> bool:
        try:
            return len(self._load()) > 0
        except DataAccessError:
            return False

    def all(self) -> list[Opportunity]:
        return list(self._load().values())

    def find(self, identifier: str) -> Opportunity | None:
        """Opportunity ID, or the ID of any source that belongs to it."""
        store = self._load()
        return store.get(identifier) or store.get(self._by_source.get(identifier, ""))

    def _vocabulary(self) -> tuple[str, set[str]]:
        """(all normalized titles joined, title words that are template filler)."""
        if self._vocabulary_cache is None:
            titles = sorted({normalize(o.title) for o in self._load().values()})
            frequency = collections.Counter(
                word for title in titles for word in set(re.findall(r"[؀-ۿA-Za-z]+", title)) if len(word) >= 3)
            generic = {w for w, n in frequency.items() if n / len(titles) >= GENERIC_TITLE_SHARE}
            self._vocabulary_cache = ("\n".join(titles), generic)
        return self._vocabulary_cache

    def search(self, criteria: SearchCriteria, diagnostics: dict | None = None) -> list[Match]:
        """Hard filters on stated facts; unknown facts are kept with a caveat.

        Topic keywords: template-filler words never count as topic evidence. If some
        keywords occur in titles, an opportunity must match one of them in its title.
        Keywords found in no title (e.g. "الحوسبة الكمومية") must match title or body.
        Profile signals and soft terms only reorder results.

        If `diagnostics` is given it receives the opportunity count and, for every excluded
        opportunity, the first filter that excluded it. This never changes the result.
        """
        matches = []
        excluded: dict[str, list[str]] = {}

        def drop(opp, reason):
            excluded.setdefault(reason, []).append(opp.opportunity_id)

        title_blob, generic = self._vocabulary()
        topic = [k for k in criteria.keywords if not any(k in g or g in k for g in generic)]
        known = [k for k in topic if k in title_blob]
        profile_skills = [(s, normalize(s)) for s in criteria.profile_skills]
        profile_interests = []
        for interest in criteria.profile_interests:
            words = [w for w in (strip_prefix(w) for w in re.findall(r"[؀-ۿA-Za-z]+", normalize(interest)))
                     if len(w) >= 4 and not any(w in g or g in w for g in generic)]
            profile_interests.append((interest, normalize(interest), words))
        for opp in self._load().values():
            caveats: list[str] = []
            status = opp.application_status(criteria.evaluation_time)
            if status in ("cancelled", "closed"):
                drop(opp, status if status == "cancelled" else ("closed_statement" if opp.status_statement == "closed" else "deadline_passed"))
                continue
            if opp.end_date and opp.end_date < criteria.evaluation_time.astimezone(RIYADH).date():
                drop(opp, "activity_ended"); continue  # the activity is over; a same-day one stays
            if criteria.types and opp.opportunity_type not in criteria.types:
                drop(opp, "type"); continue
            if criteria.either_city_or_mode:
                if not (opp.location in criteria.cities or opp.delivery_mode in criteria.delivery_modes):
                    drop(opp, "city_or_mode"); continue
            else:
                if criteria.delivery_modes and opp.delivery_mode not in criteria.delivery_modes:
                    drop(opp, "delivery_mode"); continue
                if criteria.cities and opp.delivery_mode != "online" and opp.location not in criteria.cities:
                    drop(opp, "city"); continue
            if criteria.max_budget is not None:
                if not opp.cost_claims:
                    drop(opp, "fee_unknown"); continue  # a budget limit was asked for and the fee is not stated
                elif all(c > criteria.max_budget for c in opp.cost_claims):
                    drop(opp, "budget"); continue
                elif any(c > criteria.max_budget for c in opp.cost_claims):
                    caveats.append("المصادر تختلف في الرسوم؛ إحدى القيم تتجاوز ميزانيتك.")
            elif opp.cost is None:
                caveats.append("الرسوم غير مذكورة أو متعارضة؛ لا تُعد مجانية.")
            if criteria.available_from or criteria.available_to:
                if not (opp.start_date and opp.end_date):
                    caveats.append("تواريخ النشاط غير مذكورة؛ لا يمكن تأكيد توافقها مع فترتك.")
                elif (criteria.available_from and opp.start_date < criteria.available_from) or \
                        (criteria.available_to and opp.end_date > criteria.available_to):
                    drop(opp, "availability_window"); continue
            if criteria.max_duration_days:
                if opp.duration_days is None:
                    caveats.append("مدة النشاط غير مذكورة.")
                elif opp.duration_days > criteria.max_duration_days:
                    drop(opp, "duration"); continue
            state = opp.deadline_state(criteria.evaluation_time)
            if state == "unknown":
                caveats.append("لم يُذكر موعد التقديم.")
            elif state == "uncertain":
                caveats.append("موعد التقديم قريب جدًا أو متعارض؛ تحقق منه قبل الاعتماد.")
            if status == "not_yet_open":
                caveats.append(f"التسجيل لم يبدأ بعد؛ يفتح يوم {opp.opens_on.isoformat()}.")
            elif status == "requires_verification":
                caveats.append("حالة التسجيل وردت وقت نشر الإعلان وتحتاج تحققًا.")
            elif status == "unknown":
                caveats.append("لم تُذكر حالة التسجيل في الإعلان.")
            score, matched = 0, []
            title, body = normalize(opp.title), normalize(" ".join(s["content"] for s in opp.sources))
            if known:
                hits = [k for k in known if k in title]
                if not hits:
                    drop(opp, "topic_not_in_title"); continue
                score += 2 * len(hits)
                matched.extend(hits)
                for term in topic:
                    if term not in known and term in body:
                        score += 1; matched.append(term)
            elif topic:
                for term in topic:
                    if term in title:
                        score += 2; matched.append(term)
                    elif term in body:
                        score += 1; matched.append(term)
                if score == 0:
                    drop(opp, "topic_not_found"); continue
            score += sum(1 for term in criteria.soft_terms if term in body)
            if criteria.cities and opp.location in criteria.cities:
                score += 1
            profile_score, profile_matches = 0, []
            if criteria.profile_city and opp.location == criteria.profile_city:
                profile_score += 2; profile_matches.append(f"مدينتك {criteria.profile_city}")
            for skill, skill_norm in profile_skills:
                if skill_norm and skill_norm in title:
                    profile_score += 2; profile_matches.append(f"مهارتك {skill}")
                elif skill_norm and skill_norm in {normalize(r) for r in opp.required_skills or []}:
                    profile_score += 2; profile_matches.append(f"مهارتك {skill} (مطلوبة في الإعلان)")
            for interest, phrase, words in profile_interests:
                if phrase in title or any(w in title for w in words):
                    profile_score += 1; profile_matches.append(f"اهتمامك {interest}")
            matches.append(Match(opp, score, matched, caveats, profile_score, profile_matches))
        far = date.max
        matches.sort(key=lambda m: (-m.score, -m.profile_score, len(m.caveats),
                                    m.opportunity.deadline or far, m.opportunity.opportunity_id))
        if diagnostics is not None:
            diagnostics.update(n_opportunities=len(self._load()), excluded_ids=excluded,
                               excluded_counts={reason: len(ids) for reason, ids in excluded.items()})
        return matches

    def get_details(self, opportunity_id: str):
        """ID (opportunity or source) -> OpportunityDetailsResponse, or None if absent."""
        opp = self.find(opportunity_id)
        if opp is None:
            return None
        now = get_settings().resolve_evaluation_time()
        warnings = list(opp.warnings)
        if opp.cost is None and not any("الرسوم" in w for w in warnings):
            warnings.append("الرسوم غير مذكورة في المصادر؛ لا تُعد مجانية.")
        if not opp.deadline_claims:
            warnings.append("لم يُذكر موعد التقديم في المصادر.")
        status = opp.application_status(now)
        if status in ("requires_verification", "not_yet_open"):
            warnings.append(f"حالة التسجيل كما وردت عند النشر ({opp.status_published_at[:10]}) وتحتاج تحققًا.")
        hours = opp.hours_per_session
        return OpportunityDetailsResponse(
            evidence_version=self.evidence_version,
            opportunity_id=opp.opportunity_id,
            title=opp.title,
            opportunity_type=opp.opportunity_type,
            description=opp.sources[0]["content"].strip(),
            location=opp.location,
            delivery_mode=opp.delivery_mode,
            start_date=opp.start_date.isoformat() if opp.start_date else None,
            end_date=opp.end_date.isoformat() if opp.end_date else None,
            remote_participation_allowed=opp.remote_participation_allowed,
            physical_attendance_required=opp.physical_attendance_required,
            duration=opp.duration_days,
            session_count=opp.session_count,
            hours_per_session=hours,
            total_hours=opp.session_count * hours if opp.session_count and hours else None,
            deadline=opp.deadline.isoformat() if opp.deadline else None,
            application_status=status,
            eligibility_requirements=EligibilityRequirements(mandatory=opp.mandatory, preferred=opp.preferred),
            required_skills=opp.required_skills,
            cost=opp.cost,
            source_ids=opp.source_ids,
            data_warnings=warnings,
        )
