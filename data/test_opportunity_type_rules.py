"""Checks for opportunity_type_rules.py. Run from data/: python -m unittest test_opportunity_type_rules -v
(not with -I, which drops the current directory from the import path). Reads only raw/raw_opportunities.csv; writes nothing.
"""
import csv
import re
import unittest
from collections import Counter, defaultdict
from pathlib import Path

from opportunity_type_rules import TYPE_PHRASES, classify_type

RAW = Path(__file__).resolve().parent / "raw" / "raw_opportunities.csv"
API_TYPES = {"workshop", "course", "internship", "competition", "volunteering", "event"}


def load():
    with RAW.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


class TypeRulesOnCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = load()
        cls.res = {r["source_id"]: classify_type(r["content"], r["source_name"]) for r in cls.rows}

    def test_every_source_is_classified_once(self):
        self.assertEqual(len(self.rows), 1000)
        self.assertEqual(Counter(r["type_status"] for r in self.res.values()), {"classified": 1000})
        self.assertTrue(all(len(r["type_phrases_found"]) == 1 for r in self.res.values()))
        self.assertTrue(all(not r["type_unrecognized"] for r in self.res.values()))

    def test_types_are_api_values_and_vocabulary_is_fully_used(self):
        self.assertEqual({r["opportunity_type"] for r in self.res.values()}, API_TYPES)
        self.assertEqual({r["type_phrase"] for r in self.res.values()}, set(TYPE_PHRASES))

    def test_distribution_regression(self):
        # Characterization of the current corpus, not a ground truth.
        counts = Counter(r["opportunity_type"] for r in self.res.values())
        self.assertEqual(counts, {"competition": 169, "event": 167, "volunteering": 167,
                                  "course": 166, "internship": 166, "workshop": 165})

    def test_evidence_is_original_text_containing_the_phrase(self):
        for row in self.rows:
            r = self.res[row["source_id"]]
            self.assertIn(r["type_evidence"], row["content"])
            self.assertIn(r["type_phrase"], r["type_evidence"])

    def test_linked_sources_never_disagree_on_type(self):
        by_url = defaultdict(list)
        for row in self.rows:
            by_url[re.search(r"https://fursah-\d+\.example/info", row["content"]).group(0)].append(row["source_id"])
        pairs = [v for v in by_url.values() if len(v) > 1]
        self.assertEqual(len(pairs), 100)
        self.assertEqual([p for p in pairs if len({self.res[i]["opportunity_type"] for i in p}) > 1], [])

    def test_hand_read_first_ten(self):
        # Types read directly from the text of SRC-0001..0010 (not produced by the rule).
        expected = {"SRC-0001": "competition", "SRC-0002": "course", "SRC-0003": "event",
                    "SRC-0004": "course", "SRC-0005": "course", "SRC-0006": "volunteering",
                    "SRC-0007": "volunteering", "SRC-0008": "volunteering",
                    "SRC-0009": "workshop", "SRC-0010": "internship"}
        self.assertEqual({i: self.res[i]["opportunity_type"] for i in expected}, expected)

    def test_internship_family_has_the_structural_markers(self):
        for row in self.rows:
            if self.res[row["source_id"]]["opportunity_type"] == "internship":
                self.assertIn("العمل حضوري داخل مقر", row["content"])
                self.assertIn("طالباً في السنة الأخيرة", row["content"])


class TypeRulesNeverGuess(unittest.TestCase):
    def test_no_slot_is_unknown(self):
        r = classify_type("تم تحديد الموعد. حضور اللقاء الأول في الموقع. معرض للمجتمع.", "ناشر")
        self.assertEqual((r["type_status"], r["opportunity_type"]), ("unknown", None))

    def test_phrase_outside_vocabulary_is_reported_not_mapped(self):
        r = classify_type("لدى ناشر هاكاثون بعنوان «فكرة».", "ناشر")
        self.assertEqual((r["type_status"], r["opportunity_type"], r["type_unrecognized"]),
                         ("unknown", None, ["هاكاثون"]))

    def test_conflicting_types_are_ambiguous(self):
        r = classify_type("لدى ناشر ورشة عمل بعنوان «فكرة». يدعو ناشر المهتمين إلى «فكرة» عبر نشاط تطوعي.", "ناشر")
        self.assertEqual((r["type_status"], r["opportunity_type"]), ("ambiguous", None))

    def test_bare_word_in_title_or_body_does_not_classify(self):
        r = classify_type("«ورشة عمل» عنوان فقط. النص يذكر دورة وتحدي ومسابقة.", "ناشر")
        self.assertEqual(r["type_status"], "unknown")


if __name__ == "__main__":
    unittest.main()
