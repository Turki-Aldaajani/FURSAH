"""Opportunity-type rules for the raw announcements / قواعد نوع الفرصة.

Separate from preparation_helpers.py on purpose; nothing here edits sources or
the supplied helpers. Standard library only, so the backend can import it too.

The corpus is template-generated: the activity kind is stated as a short noun
phrase in a fixed slot next to the quoted title («...»), e.g. "لدى X ورشة عمل
بعنوان «...»". The rule reads ONLY those slots and accepts ONLY phrases in
TYPE_PHRASES. Bare words are never matched: «معرض» and «لقاء» occur in titles
and in "حضور اللقاء الأول", where they do not state the kind of activity.

Phrase -> API type is an engineering mapping onto the six values of
SearchConstraints.opportunity_types; the data guide does not define it.
`basis` records why each phrase maps where it does (see AUDIT notes):
  "wording"  the phrase itself names the type (ورشة، دورة، تدريب، منافسة/مسابقة،
             تطوع، فعالية).
  "inferred" the wording alone is not enough; the family is fixed by the
             announcements' structure and by linked sources swapping these phrases
             only within the family. Confirm these four with the team.
"""
import re

TYPE_PHRASES = {
    "ورشة عمل": ("workshop", "wording"),
    "ورشة تطبيقية": ("workshop", "wording"),
    "جلسة تدريبية عملية": ("workshop", "inferred"),
    "دورة تدريبية": ("course", "wording"),
    "دورة من عدة لقاءات": ("course", "wording"),
    "برنامج تعليمي متسلسل": ("course", "inferred"),
    "تدريب عملي داخل مقر العمل": ("internship", "wording"),
    "تدريب مهني داخل المؤسسة": ("internship", "wording"),
    "فرصة تدريب ميداني بإشراف الفريق": ("internship", "wording"),
    "منافسة لتطوير فكرة": ("competition", "wording"),
    "مسابقة فرق": ("competition", "wording"),
    "تحدي تنافسي": ("competition", "wording"),
    "نشاط تطوعي": ("volunteering", "wording"),
    "مهمة تطوعية مجتمعية": ("volunteering", "wording"),
    "فرصة للتطوع": ("volunteering", "wording"),
    "فعالية حوارية": ("event", "wording"),
    "لقاء مفتوح": ("event", "inferred"),
    "أمسية معرفية": ("event", "inferred"),
}

_T, _P = "‹T›", "‹P›"
_KIND = r"([^\n.،:؛«»؟]+?)"
# (name, pattern) over text where every «...» became «‹T›» and the publisher ‹P›.
_SLOTS = (
    ("title_by", rf"(?:لدى {_P}|الإعلان عن|يقدم {_P}|ينظم {_P}) {_KIND} بعنوان «{_T}»"),
    ("season_program", rf"لهذا الموسم «{_T}»، وهو {_KIND}[.،]"),
    ("invites_via", rf"المهتمين إلى «{_T}» عبر {_KIND}\."),
    ("open_space", rf"لمناقشة «{_T}» في {_KIND} يقدمها {_P}"),
    ("story_in", rf"تبدأ حكاية «{_T}» مع {_P} في {_KIND}\."),
    ("offers_about", rf"يقدم {_P} {_KIND} حول «{_T}»"),
    ("new_date_with", rf"موعد جديد مع «{_T}»، {_KIND} ينظمه {_P}"),
    ("this_is", rf"بموضوع «{_T}»، (?:هذه|هذا) {_KIND} لدى {_P}"),
    ("question_organizes", rf"«{_T}»؟\s*ينظم {_P} {_KIND}\."),
    ("title_colon", rf"«{_T}»: {_KIND} من تنظيم {_P}"),
    ("we_received", rf"وصلنا إعلان {_P} عن {_KIND}: «{_T}»"),
)


def _mask(content, source_name):
    text = content.replace("\r\n", "\n")
    text = re.sub(r"«[^»\n]*»", f"«{_T}»", text)
    return text.replace(source_name, _P)


def _evidence(content, phrase):
    for part in re.split(r"(?<=[.؟!؛])\s+|\r?\n", content):
        if phrase in part:
            return part.strip()
    return None


def classify_type(content, source_name):
    """Classify one announcement; never guesses.

    Returns a dict. type_status is "classified" (one type), "unknown" (no slot
    matched, or only phrases outside TYPE_PHRASES) or "ambiguous" (slots name
    phrases of different types). opportunity_type is None unless "classified".
    type_evidence is the original clause that contains the phrase.
    """
    text = _mask(content, source_name)
    found, unrecognized = {}, []
    for slot, pattern in _SLOTS:
        for match in re.finditer(pattern, text):
            phrase = match.group(1).strip()
            if phrase in TYPE_PHRASES:
                found.setdefault(phrase, slot)
            elif phrase not in unrecognized:
                unrecognized.append(phrase)
    types = {TYPE_PHRASES[p][0] for p in found}
    result = {"opportunity_type": None, "type_status": "unknown", "type_phrase": None,
              "type_basis": None, "type_slot": None, "type_evidence": None,
              "type_phrases_found": sorted(found), "type_unrecognized": unrecognized}
    if len(types) > 1:
        result["type_status"] = "ambiguous"
    elif found:
        phrase = next(iter(found))
        result.update(opportunity_type=TYPE_PHRASES[phrase][0], type_status="classified",
                      type_phrase=phrase, type_basis=TYPE_PHRASES[phrase][1],
                      type_slot=found[phrase], type_evidence=_evidence(content, phrase))
    return result
