"""Day 4 - 2.4: the three fixes found by the baseline run (see handoff report)."""
import os
import unittest
from datetime import date, datetime

os.environ["FURSAH_EVALUATION_TIME"] = "2026-10-18T12:00:00+03:00"
os.environ["FURSAH_MODEL_API_KEY"] = ""
os.environ["FURSAH_TRACE_JSONL_PATH"] = ""

from app.infrastructure.dependencies import get_app_dependencies  # noqa: E402
from app.repository import SearchCriteria, deadline_state  # noqa: E402

EVAL = datetime.fromisoformat("2026-10-18T12:00:00+03:00")


class Fixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = get_app_dependencies().repository

    def opp(self, source_id):
        return next(o for o in self.repo.all() if source_id in o.source_ids)

    def test_deadline_before_the_evaluation_day_has_passed(self):
        self.assertEqual(deadline_state(date(2026, 10, 17), EVAL), "passed")
        self.assertEqual(deadline_state(date(2026, 10, 18), EVAL), "uncertain")  # same day, no cutoff time
        self.assertEqual(deadline_state(date(2026, 10, 19), EVAL), "not_passed")
        self.assertEqual(deadline_state(None, EVAL), "unknown")

    def test_ended_application_phrase_closes_the_opportunity(self):
        self.assertEqual(self.opp("SRC-0717").application_status(EVAL), "closed")  # DEV-19
        self.assertEqual(self.opp("SRC-0797").application_status(EVAL), "closed")  # DEV-20
        # DEV-23: the later update says applications ended, so it overrides the earlier "open"
        self.assertEqual(self.opp("SRC-0981").application_status(EVAL), "closed")

    def test_a_budget_limit_excludes_opportunities_without_a_stated_fee(self):
        criteria = SearchCriteria(evaluation_time=EVAL, max_budget=150)
        diagnostics = {}
        found = {m.opportunity.opportunity_id for m in self.repo.search(criteria, diagnostics)}
        for source_id in ("SRC-0314", "SRC-0319"):  # no fee stated
            self.assertNotIn(self.opp(source_id).opportunity_id, found)
            self.assertIn(self.opp(source_id).opportunity_id, diagnostics["excluded_ids"]["fee_unknown"])
        free = SearchCriteria(evaluation_time=EVAL)  # no budget asked: kept, with a caveat
        match = next(m for m in self.repo.search(free) if m.opportunity.opportunity_id == self.opp("SRC-0319").opportunity_id)
        self.assertTrue(any("الرسوم" in c for c in match.caveats))

    def test_an_activity_that_ended_before_the_evaluation_day_is_excluded(self):
        opp = self.opp("SRC-0653")  # DEV-05: a one-day activity on 2026-09-29, no deadline stated
        diagnostics = {}
        found = {m.opportunity.opportunity_id for m in self.repo.search(SearchCriteria(evaluation_time=EVAL), diagnostics)}
        self.assertNotIn(opp.opportunity_id, found)
        self.assertIn(opp.opportunity_id, diagnostics["excluded_ids"]["activity_ended"])


if __name__ == "__main__":
    unittest.main()
