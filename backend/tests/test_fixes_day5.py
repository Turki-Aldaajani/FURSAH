"""Day 5: conversational words are not topics (empty Riyadh in-person search seen on Render).

"بدوّر" became the topic "دور" and "تنفع" stayed one; neither occurs in any title or body, so
the only opportunity left after the other filters was dropped as topic_not_found.
"""
import asyncio
import contextlib
import io
import os
import unittest
from dataclasses import replace
from datetime import datetime

os.environ["FURSAH_MODEL_API_KEY"] = ""
os.environ["FURSAH_TRACE_JSONL_PATH"] = ""

from app.infrastructure.dependencies import get_app_dependencies  # noqa: E402
from app.infrastructure.tracing import Tracer  # noqa: E402
from app.schemas import DiscoveryRequest  # noqa: E402
from app.workflow import discover, interpret_query  # noqa: E402

EVAL = datetime.fromisoformat("2026-10-22T18:14:27+00:00")  # the reported search, 21:14 in Riyadh
REPORTED = {"location_preference": ["الرياض"], "delivery_modes": ["in_person"], "max_budget_sar": 5000.0,
            "available_from": "2026-10-22", "available_to": "2026-10-31"}


class ConversationalWords(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deps = replace(get_app_dependencies(), model_config=None, tracer=Tracer(), evaluation_time=EVAL)
        cls.cities = sorted({o.location for o in cls.deps.repository.all() if o.location})

    def search(self, query, constraints=None):
        request = DiscoveryRequest(request_id="day5", query=query, profile={"interests": [], "skills": None},
                                   constraints=constraints or {})
        with contextlib.redirect_stdout(io.StringIO()):
            response = asyncio.run(discover(request, self.deps))
        return response.status.value, [r.source_ids for r in response.recommendations]

    def test_conversational_words_are_not_topic_keywords(self):
        self.assertEqual(interpret_query("ودي بدور فرصة تنفع", self.cities).keywords, [])
        self.assertEqual(interpret_query("ادور على شي ينفعني", self.cities).keywords, [])

    def test_the_reported_search_returns_the_remaining_opportunity(self):
        for query in ("ودي بدور فرصة تنفع", "ادور على شي ينفعني"):
            self.assertEqual(self.search(query, REPORTED), ("ok", [["SRC-0332"]]), query)

    def test_a_topic_absent_from_the_data_is_still_no_match(self):
        self.assertEqual(self.search("دورة في الحوسبة الكمومية"), ("no_match", []))

    def test_technology_searches_are_unchanged(self):
        self.assertEqual(self.search("دورة إكسل"), ("ok", [["SRC-0132"]]))
        self.assertEqual(self.search("ورشة بايثون"), ("ok", [["SRC-0139", "SRC-0140"]]))


if __name__ == "__main__":
    unittest.main()
