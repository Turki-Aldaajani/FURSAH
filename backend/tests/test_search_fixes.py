"""Search behavior fixes: alternatives, activity-type words, technology names, profile ranking.

Run from backend/: .venv\\Scripts\\python.exe -m unittest discover -s tests -v
The classroom evaluation time is pinned so results do not drift with the date. Expected
opportunities are facts of the prepared corpus at that time (characterization, not ground truth).
"""
import os
import unittest

os.environ["FURSAH_EVALUATION_TIME"] = "2026-10-18T12:00:00+03:00"
os.environ["FURSAH_MODEL_API_KEY"] = ""  # automated tests never call a real model, whatever backend/.env holds
os.environ["FURSAH_TRACE_JSONL_PATH"] = ""  # ...and never write into the real trace file named in backend/.env

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.workflow import interpret_query  # noqa: E402

CITIES = ["أبها", "الدمام", "الرياض", "جدة", "تبوك"]
EMPTY_PROFILE = {"interests": [], "skills": None}


class SearchFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def ask(self, query, profile=None, constraints=None):
        response = self.client.post("/api/v1/discover", json={
            "query": query, "profile": profile or EMPTY_PROFILE, "constraints": constraints or {}})
        self.assertEqual(response.status_code, 200)
        return response.json()

    @staticmethod
    def sources(body):
        return {sid for rec in body["recommendations"] for sid in rec["source_ids"]}

    # 1. "or" between a city and a delivery mode means alternatives
    def test_or_between_city_and_remote_is_an_alternative(self):
        self.assertTrue(interpret_query("فرص صحية في أبها أو عن بُعد", CITIES).either_city_or_mode)
        self.assertTrue(interpret_query("عن بعد أو في أبها", CITIES).either_city_or_mode)
        self.assertFalse(interpret_query("ورشة في أبها عن بعد", CITIES).either_city_or_mode)
        self.assertFalse(interpret_query("أو عن بعد في جدة", CITIES).either_city_or_mode)

    def test_health_query_accepts_either_location(self):
        body = self.ask("فرص صحية في أبها أو عن بُعد")
        self.assertEqual(body["status"], "ok")
        self.assertTrue(any("بدائل" in f for f in body["interpreted_filters"]))
        for rec in body["recommendations"]:
            self.assertTrue(rec["location"] == "أبها" or rec["delivery_mode"] == "online")

    def test_alternative_keeps_an_in_city_hybrid_that_the_both_reading_dropped(self):
        # SRC-0954: Excel data-analysis workshop in Abha, hybrid (not online).
        either = self.ask("ورشة في تحليل البيانات في أبها أو عن بعد")
        self.assertIn("SRC-0954", self.sources(either))
        both = self.ask("ورشة في تحليل البيانات في أبها عن بعد")
        self.assertNotIn("SRC-0954", self.sources(both))

    # 2. activity-type words are not topic keywords
    def test_internship_phrase_is_not_a_topic(self):
        reading = interpret_query("أبغى تدريب عملي في الدمام", CITIES)
        self.assertEqual((reading.types, reading.cities, reading.keywords), (["internship"], ["الدمام"], []))
        body = self.ask("أبغى تدريب عملي في الدمام")
        self.assertEqual(body["status"], "ok")
        self.assertFalse(any("كلمات الموضوع" in f for f in body["interpreted_filters"]))
        for rec in body["recommendations"]:
            self.assertEqual(rec["opportunity_type"], "internship")
            self.assertTrue(rec["location"] == "الدمام" or rec["delivery_mode"] == "online")

    def test_other_type_words_leave_no_fragments(self):
        for query, kind in [("ورشة عمل في تبوك", "workshop"), ("مسابقة في جدة", "competition"),
                            ("فعاليات في الرياض", "event"), ("تطوعية في أبها", "volunteering")]:
            self.assertEqual(interpret_query(query, CITIES).keywords, [], query)
            self.assertEqual(interpret_query(query, CITIES).types, [kind], query)

    # 3. technology names across scripts; level words are not topics
    def test_technology_names_are_canonical(self):
        for query, name in [("بايثون", "python"), ("Python", "python"), ("إكسل", "excel"), ("excel", "excel"),
                            ("اس كيو ال", "sql"), ("SQL", "sql"), ("باور بي آي", "power bi"), ("Power BI", "power bi")]:
            self.assertEqual(interpret_query(f"دورة {query}", CITIES).keywords, [name], query)

    def test_python_course_returns_only_python_courses(self):
        reading = interpret_query("دورة بايثون للمبتدئين", CITIES)
        self.assertEqual((reading.keywords, reading.soft_terms), (["python"], ["مبتدئين"]))
        body = self.ask("دورة بايثون للمبتدئين")
        self.assertEqual(body["status"], "ok")
        self.assertEqual(self.sources(body), {"SRC-0428", "SRC-0807"})  # SRC-0971: deadline 2026-10-17 is before the evaluation day
        for rec in body["recommendations"]:
            self.assertEqual(rec["opportunity_type"], "course")
            self.assertIn("python", rec["title"].lower())

    def test_level_word_alone_is_not_a_topic_filter(self):
        body = self.ask("دورة للمبتدئين")
        self.assertEqual(body["status"], "ok")
        self.assertFalse(any("كلمات الموضوع" in f for f in body["interpreted_filters"]))

    def test_template_filler_words_never_count_as_topic(self):
        plain = self.ask("أبحث عن ورشة")
        filler = self.ask("ورشة للمجتمع خطوة بخطوة من الفكرة إلى التطبيق")
        self.assertEqual([r["opportunity_id"] for r in plain["recommendations"]],
                         [r["opportunity_id"] for r in filler["recommendations"]])

    def test_unmatched_topics_still_return_no_match(self):
        for query in ("ورشة في الحوسبة الكمومية للمريخ", "فرص في ريادة الأعمال عن بُعد"):
            self.assertEqual(self.ask(query)["status"], "no_match", query)

    # 4. profile signals rank but never filter
    def test_profile_city_ranks_first_without_removing_other_cities(self):
        body = self.ask("ورشة", {"city": "الرياض", "interests": [], "skills": None})
        recs = body["recommendations"]
        self.assertEqual(len(recs), 3)
        self.assertEqual([r["location"] for r in recs[:2]], ["الرياض", "الرياض"])
        self.assertNotEqual(recs[2]["location"], "الرياض")
        self.assertIn("مدينتك الرياض", recs[0]["reason"])

    def test_profile_skill_ranks_a_matching_title_first(self):
        body = self.ask("ورشة", {"interests": [], "skills": [{"name": "Python", "level": None}]})
        first = body["recommendations"][0]
        self.assertIn("python", first["title"].lower())
        self.assertIn("مهارتك Python", first["reason"])
        self.assertEqual(len(body["recommendations"]), 3)

    def test_profile_interest_ranks_a_matching_title_first(self):
        body = self.ask("ورشة", {"interests": ["التصميم"], "skills": None})
        self.assertIn("تصميم", body["recommendations"][0]["title"])
        self.assertIn("اهتمامك التصميم", body["recommendations"][0]["reason"])

    def test_profile_with_no_matching_signal_changes_nothing(self):
        plain = self.ask("ورشة")
        other = self.ask("ورشة", {"city": "الباحة", "interests": ["الفضاء"], "skills": [{"name": "Arduino", "level": None}]})
        self.assertEqual([r["opportunity_id"] for r in plain["recommendations"]],
                         [r["opportunity_id"] for r in other["recommendations"]])
        self.assertEqual(other["status"], "ok")


if __name__ == "__main__":
    unittest.main()
