"""Day 4 - 2.3: the graders in backend/evals/graders.py.

Unit tests use small invented cases. Shared-case tests use the 24 real cases in dev_cases.jsonl with
INVENTED replies (a perfect one, an empty one, a harmful one): they check the graders, not the app.
The last class runs the real app on two team-defined checks the worksheet asks for (needs_info and
details consistency); those are NOT shared cases. No network: the model is off.
Run from backend/: .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
import json
import math
import os
import unittest
from dataclasses import replace
from pathlib import Path

os.environ["FURSAH_EVALUATION_TIME"] = "2026-10-18T12:00:00+03:00"
os.environ["FURSAH_MODEL_API_KEY"] = ""  # automated tests never call a real model, whatever backend/.env holds
os.environ["FURSAH_TRACE_JSONL_PATH"] = ""  # ...and never write into the real trace file named in backend/.env

from fastapi.testclient import TestClient  # noqa: E402

from app.infrastructure.config import Settings  # noqa: E402
from app.infrastructure.dependencies import get_app_dependencies  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas import DiscoveryResponse, Recommendation  # noqa: E402
from evals import graders  # noqa: E402
from evals.graders import (GRADERS, grade_availability, grade_constraints, grade_contract,  # noqa: E402
                           grade_rank, grade_recall, grade_recommendations, grade_status)
from evals.run_eval import CaseSpans, run_graders  # noqa: E402  (only its helpers; main() is not run)

CASES = [json.loads(line) for line in
         (Path(__file__).resolve().parents[1] / "evals" / "dev_cases.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
NO_SPANS = CaseSpans([])


def rec(ids, oid=None, **fields):
    return Recommendation(opportunity_id=oid or "opp-" + "-".join(ids), title="t", eligibility_status="uncertain",
                          reason="r", source_ids=list(ids), **fields)


def reply(*recs, status="ok"):
    return DiscoveryResponse(request_id="T", status=status, message="m", recommendations=list(recs))


EMPTY_NO_MATCH = DiscoveryResponse(request_id="T", status="no_match", message="m")


def case(status="ok", acceptable=(), excluded=None, uncertain=None, constraints=None, time="2026-10-18T12:00:00+03:00"):
    return {"case_id": "T", "query": "q", "profile": {"interests": [], "skills": None}, "constraints": constraints or {},
            "evaluation_time": time,
            "expected": {"status": status, "acceptable_source_ids": list(acceptable),
                         "excluded_source_ids": excluded or {}, "uncertain_source_ids": uncertain or {}}}


def grade(grader, c, r):
    return grader(c, r, NO_SPANS)


class Recommendations(unittest.TestCase):
    def test_acceptable_only_is_right(self):
        g = grade(grade_recommendations, case(acceptable=["A1"]), reply(rec(["A1"])))
        self.assertEqual((g.metric, g.score), ("e2e.recommendations", 1.0))

    def test_an_excluded_source_makes_it_wrong_even_beside_an_acceptable_one(self):
        c = case(acceptable=["A1"], excluded={"E1": "over_budget"})
        g = grade(grade_recommendations, c, reply(rec(["A1", "E1"])))
        self.assertEqual(g.score, 0.0)
        self.assertIn("E1:over_budget", g.reason)
        self.assertIn("expected any of ['A1']", g.reason)
        self.assertIn("got [['A1', 'E1']]", g.reason)

    def test_only_uncertain_sources_is_not_graded(self):
        c = case(acceptable=["A1"], uncertain={"U1": "status_unknown"})
        self.assertIsNone(grade(grade_recommendations, c, reply(rec(["U1"]))))
        g = grade(grade_recommendations, c, reply(rec(["A1"]), rec(["U1"])))
        self.assertEqual(g.score, 1.0)  # the uncertain one counts neither for nor against

    def test_a_source_in_no_list_is_wrong(self):
        g = grade(grade_recommendations, case(acceptable=["A1"]), reply(rec(["X9"])))
        self.assertEqual(g.score, 0.0)
        self.assertIn("off topic", g.reason)
        self.assertIn("X9", g.reason)

    def test_share_of_graded_recommendations(self):
        c = case(acceptable=["A1"], excluded={"E1": "deadline_passed"})
        g = grade(grade_recommendations, c, reply(rec(["A1"]), rec(["E1"])))
        self.assertEqual(g.score, 0.5)

    def test_nothing_returned_is_not_applicable(self):
        self.assertIsNone(grade(grade_recommendations, case("no_match"), EMPTY_NO_MATCH))
        self.assertIsNone(grade(grade_recommendations, case(acceptable=["A1"]), EMPTY_NO_MATCH))

    def test_any_recommendation_in_a_no_match_case_is_wrong(self):
        c = case("no_match", excluded={"E1": "location_not_preferred"})
        g = grade(grade_recommendations, c, reply(rec(["E1"])))
        self.assertEqual(g.score, 0.0)
        self.assertIn("expected no recommendation", g.reason)


class Recall(unittest.TestCase):
    def test_not_applicable_without_acceptable_sources(self):
        self.assertIsNone(grade(grade_recall, case("no_match"), EMPTY_NO_MATCH))

    def test_an_empty_reply_scores_zero_not_none(self):
        g = grade(grade_recall, case(acceptable=["A1"]), EMPTY_NO_MATCH)
        self.assertEqual(g.score, 0.0)
        self.assertIn("status 'no_match'", g.reason)
        self.assertIn("missing ['A1']", g.reason)

    def test_partial_recall_and_the_missing_list(self):
        c = case(acceptable=["A1", "A2", "A3"])
        g = grade(grade_recall, c, reply(rec(["A1"]), rec(["A2"])))
        self.assertAlmostEqual(g.score, 2 / 3)
        self.assertIn("missing ['A3']", g.reason)

    def test_only_right_recommendations_earn_credit(self):
        c = case(acceptable=["A1"], excluded={"E1": "over_budget"})
        self.assertEqual(grade(grade_recall, c, reply(rec(["A1", "E1"]))).score, 0.0)

    def test_a_repost_counts_with_its_original(self):
        c = case(acceptable=["A1", "A2"])
        self.assertEqual(grade(grade_recall, c, reply(rec(["A1", "A2"]))).score, 1.0)


class Constraints(unittest.TestCase):
    def test_label_proves_each_kind_of_violation(self):
        for reason, kind in [("over_budget", "budget"), ("location_not_preferred", "location"),
                             ("delivery_not_requested", "delivery"), ("type_not_requested", "type"),
                             ("outside_dates", "dates"), ("fee_unknown", "budget (fee unknown)")]:
            g = grade(grade_constraints, case(excluded={"E1": reason}), reply(rec(["E1"])))
            self.assertEqual(g.score, 0.0, reason)
            self.assertIn(kind, g.reason)
            self.assertIn(f"E1:{reason}", g.reason)

    def test_an_availability_only_exclusion_proves_nothing_about_constraints(self):
        self.assertIsNone(grade(grade_constraints, case(excluded={"E1": "deadline_passed"}), reply(rec(["E1"]))))

    def test_right_recommendations_respect_the_constraints(self):
        c = case(acceptable=["A1"], excluded={"E1": "over_budget"}, constraints={"max_budget_sar": 100})
        self.assertEqual(grade(grade_constraints, c, reply(rec(["A1"]))).score, 1.0)
        self.assertEqual(grade(grade_constraints, c, reply(rec(["A1"]), rec(["E1"]))).score, 0.5)

    def test_response_fields_prove_a_mismatch_only_where_labels_are_silent(self):
        c = case(constraints={"opportunity_types": ["event"]})
        g = grade(grade_constraints, c, reply(rec(["X9"], opportunity_type="course")))
        self.assertEqual(g.score, 0.0)
        self.assertIn("type", g.reason)
        self.assertIsNone(grade(grade_constraints, c, reply(rec(["X9"], opportunity_type=None))))  # unknown: unproven

    def test_a_label_is_never_contradicted_by_the_response(self):
        c = case(acceptable=["A1"], constraints={"opportunity_types": ["event"]})
        self.assertEqual(grade(grade_constraints, c, reply(rec(["A1"], opportunity_type="volunteering"))).score, 1.0)

    def test_location_online_is_exempt_and_hybrid_delivery_is_not_judged(self):
        loc = case(constraints={"location_preference": ["جدة"]})
        self.assertIsNone(grade(grade_constraints, loc, reply(rec(["X1"], delivery_mode="online", location=None))))
        self.assertEqual(grade(grade_constraints, loc, reply(rec(["X2"], delivery_mode="in_person", location="أبها"))).score, 0.0)
        self.assertEqual(grade(grade_constraints, loc, reply(rec(["X3"], delivery_mode="hybrid", location="أبها"))).score, 0.0)
        deliv = case(constraints={"delivery_modes": ["in_person"]})
        self.assertIsNone(grade(grade_constraints, deliv, reply(rec(["X4"], delivery_mode="hybrid"))))
        self.assertEqual(grade(grade_constraints, deliv, reply(rec(["X5"], delivery_mode="online"))).score, 0.0)

    def test_uncertain_only_is_not_counted(self):
        self.assertIsNone(grade(grade_constraints, case(uncertain={"U1": "status_unknown"}), reply(rec(["U1"]))))


class Rank(unittest.TestCase):
    def test_position_of_the_first_right_recommendation(self):
        c = case(acceptable=["A1"], excluded={"E1": "deadline_passed"})
        self.assertEqual(grade(grade_rank, c, reply(rec(["A1"]), rec(["E1"]))).score, 1.0)
        g = grade(grade_rank, c, reply(rec(["E1"]), rec(["A1"])))
        self.assertEqual(g.score, 0.5)
        self.assertIn("first right at 2 of 2", g.reason)
        self.assertIn("ranked above it: ['E1']", g.reason)

    def test_none_right_is_zero_and_not_applicable_cases_are_none(self):
        c = case(acceptable=["A1"], excluded={"E1": "deadline_passed"})
        self.assertEqual(grade(grade_rank, c, reply(rec(["E1"]))).score, 0.0)
        self.assertIsNone(grade(grade_rank, case("no_match"), EMPTY_NO_MATCH))
        self.assertIsNone(grade(grade_rank, c, EMPTY_NO_MATCH))  # nothing returned: recall reports it


class Availability(unittest.TestCase):
    def test_a_label_proves_unavailability(self):
        for reason in ("deadline_passed", "activity_ended", "status_cancelled", "status_closed", "not_yet_open"):
            g = grade(grade_availability, case(excluded={"E1": reason}), reply(rec(["E1"])))
            self.assertEqual(g.score, 0.0, reason)
            self.assertIn(f"E1:{reason}", g.reason)

    def test_the_response_can_contradict_itself(self):
        c = case(acceptable=["A1"])
        for status in ("closed", "cancelled", "not_yet_open"):
            self.assertEqual(grade(grade_availability, c, reply(rec(["A1"], application_status=status))).score, 0.0)
        g = grade(grade_availability, c, reply(rec(["A1"], deadline="2026-10-17")))
        self.assertEqual(g.score, 0.0)
        self.assertIn("before the evaluation day 2026-10-18", g.reason)
        same_day = grade(grade_availability, c, reply(rec(["A1"], deadline="2026-10-18")))
        self.assertEqual(same_day.score, 1.0)  # same day without a time is uncertain, not proven expired

    def test_open_right_recommendation_and_not_counted_cases(self):
        c = case(acceptable=["A1"], uncertain={"U1": "status_unknown"}, excluded={"E1": "over_budget"})
        self.assertEqual(grade(grade_availability, c, reply(rec(["A1"], deadline="2026-10-25"))).score, 1.0)
        self.assertIsNone(grade(grade_availability, c, reply(rec(["U1"]))))
        self.assertIsNone(grade(grade_availability, c, reply(rec(["E1"]))))  # a budget problem proves nothing about availability


class Contract(unittest.TestCase):
    def test_clean_reply(self):
        self.assertEqual(grade(grade_contract, case(), reply(rec(["SRC-0001"]), rec(["SRC-0002"]))).score, 1.0)

    def test_duplicates_and_invented_ids_fail(self):
        shared = grade(grade_contract, case(), reply(rec(["SRC-0001"], oid="o1"), rec(["SRC-0001", "SRC-0002"], oid="o2")))
        self.assertEqual(shared.score, 0.0)
        self.assertIn("SRC-0001 is in recommendations #1 and #2", shared.reason)
        twice = grade(grade_contract, case(), reply(rec(["SRC-0001"], oid="o1"), rec(["SRC-0002"], oid="o1")))
        self.assertIn("o1 is recommended twice", twice.reason)
        repeated = grade(grade_contract, case(), reply(rec(["SRC-0001", "SRC-0001"])))
        self.assertIn("repeats a source id", repeated.reason)
        invented = grade(grade_contract, case(), reply(rec(["SRC-9999"])))
        self.assertEqual(invented.score, 0.0)
        self.assertIn("SRC-9999", invented.reason)

    def test_nothing_recommended_is_not_applicable(self):
        self.assertIsNone(grade(grade_contract, case("no_match"), EMPTY_NO_MATCH))


class Registry(unittest.TestCase):
    def test_graders_have_unique_metric_names_and_the_given_example_is_kept(self):
        names = []
        for scenario in (reply(rec(["A1"])), EMPTY_NO_MATCH):
            for g in (grader(case(acceptable=["A1"], excluded={"E1": "over_budget"}), scenario, NO_SPANS) for grader in GRADERS):
                if g is not None:
                    names.append(g.metric)
        self.assertEqual(sorted(set(names)), sorted({"e2e.status", "e2e.recommendations", "e2e.constraints", "e2e.recall",
                                                      "e2e.rank", "e2e.availability", "e2e.contract"} & set(names)))
        self.assertEqual(GRADERS[0], grade_status)
        self.assertEqual(grade_status(case(), reply(rec(["A1"])), NO_SPANS).score, 1.0)
        self.assertEqual(grade_status(case("no_match"), reply(rec(["A1"])), NO_SPANS).score, 0.0)

    def test_every_grade_is_none_or_a_finite_score_between_0_and_1(self):
        acceptable, excluded = ["A1", "A2"], {"E1": "over_budget", "E2": "deadline_passed", "E3": "type_not_requested"}
        uncertain = {"U1": "status_unknown"}
        pool = ["A1", "A2", "E1", "E2", "E3", "U1", "X9"]
        scenarios = [EMPTY_NO_MATCH] + [reply(rec([a]), rec([b])) for a in pool for b in pool if a != b] + [reply(rec(pool[:3]))]
        for c in (case(acceptable=acceptable, excluded=excluded, uncertain=uncertain, constraints={"max_budget_sar": 50}),
                  case("no_match", excluded=excluded), case(acceptable=acceptable)):
            for r in scenarios:
                grades, errors = run_graders(GRADERS, c, r, NO_SPANS)  # the harness's own validity check
                self.assertEqual(errors, [])
                for g in grades:
                    self.assertTrue(isinstance(g.metric, str) and g.metric)
                    self.assertTrue(math.isfinite(g.score) and 0 <= g.score <= 1, (g.metric, g.score))
                    self.assertTrue(g.reason)


class SharedCasesWithInventedReplies(unittest.TestCase):
    """The 24 real cases, graded against replies we invent. This tests the graders, not the app."""

    @staticmethod
    def perfect_reply(c):
        acceptable = list(c["expected"]["acceptable_source_ids"])
        if not acceptable:
            return EMPTY_NO_MATCH
        size = math.ceil(len(acceptable) / 3)  # at most 3 recommendations, repost-like groups of sources
        return reply(*[rec(acceptable[i:i + size], oid=f"opp-{i}") for i in range(0, len(acceptable), size)])

    def test_there_are_24_cases_and_each_shape_is_usable(self):
        self.assertEqual(len(CASES), 24)
        for c in CASES:
            self.assertIn(c["expected"]["status"], ("ok", "no_match"))
            self.assertEqual(bool(c["expected"]["acceptable_source_ids"]), c["expected"]["status"] == "ok")

    def test_a_perfect_reply_scores_one_on_every_applicable_metric(self):
        for c in CASES:
            grades, errors = run_graders(GRADERS, c, self.perfect_reply(c), NO_SPANS)
            self.assertEqual(errors, [], c["case_id"])
            self.assertTrue(grades, c["case_id"])  # something is measured: the case is never NOT EVALUATED
            for g in grades:
                self.assertEqual(g.score, 1.0, (c["case_id"], g.metric, g.reason))
            metrics = {g.metric for g in grades}
            if c["expected"]["status"] == "no_match":
                self.assertEqual(metrics, {"e2e.status"}, c["case_id"])
            else:
                self.assertEqual(metrics, {"e2e.status", "e2e.recommendations", "e2e.constraints", "e2e.recall",
                                           "e2e.rank", "e2e.availability", "e2e.contract"}, c["case_id"])

    def test_an_empty_reply_fails_every_ok_case_and_is_not_a_pass(self):
        for c in CASES:
            grades, errors = run_graders(GRADERS, c, EMPTY_NO_MATCH, NO_SPANS)
            self.assertEqual(errors, [])
            by = {g.metric: g for g in grades}
            if c["expected"]["status"] == "ok":
                self.assertEqual(by["e2e.status"].score, 0.0, c["case_id"])
                self.assertEqual(by["e2e.recall"].score, 0.0, c["case_id"])
                self.assertEqual(set(by), {"e2e.status", "e2e.recall"}, c["case_id"])  # nothing else is invented
            else:
                self.assertEqual({m: g.score for m, g in by.items()}, {"e2e.status": 1.0}, c["case_id"])

    def test_recommending_an_excluded_source_beside_an_acceptable_one_fails_the_right_metrics(self):
        for c in CASES:
            if c["expected"]["status"] != "ok":
                continue
            acceptable = c["expected"]["acceptable_source_ids"][0]
            source, reason = next(iter(c["expected"]["excluded_source_ids"].items()))
            grades, errors = run_graders(GRADERS, c, reply(rec([acceptable, source])), NO_SPANS)
            self.assertEqual(errors, [])
            by = {g.metric: g for g in grades}
            self.assertEqual(by["e2e.recommendations"].score, 0.0, c["case_id"])
            self.assertIn(f"{source}:{reason}", by["e2e.recommendations"].reason)
            self.assertEqual(by["e2e.recall"].score, 0.0, c["case_id"])
            self.assertEqual(by["e2e.rank"].score, 0.0, c["case_id"])
            if reason in graders.CONSTRAINT_REASONS:
                self.assertEqual(by["e2e.constraints"].score, 0.0, (c["case_id"], reason))
                self.assertNotIn("e2e.availability", by, (c["case_id"], reason))
            else:
                self.assertEqual(by["e2e.availability"].score, 0.0, (c["case_id"], reason))
                self.assertNotIn("e2e.constraints", by, (c["case_id"], reason))

    def test_uncertain_only_replies_are_not_graded_anywhere(self):
        checked = 0
        for c in CASES:
            uncertain = list(c["expected"]["uncertain_source_ids"])
            if not uncertain:
                continue
            grades, errors = run_graders(GRADERS, c, reply(rec(uncertain[:1])), NO_SPANS)
            self.assertEqual(errors, [])
            self.assertEqual({g.metric for g in grades} - {"e2e.status", "e2e.recall", "e2e.rank", "e2e.contract"}, set(), c["case_id"])
            checked += 1
        self.assertGreater(checked, 5)


class TeamDefinedChecksOnTheRealApp(unittest.TestCase):
    """Two checks the worksheet asks the team to add. NOT shared cases: written by us, run on our app."""

    @classmethod
    def setUpClass(cls):
        settings = Settings(_env_file=None, evaluation_time="2026-10-18T12:00:00+03:00", trace_jsonl_path="")
        deps = replace(get_app_dependencies(), settings=settings, model_config=None)
        app.dependency_overrides[get_app_dependencies] = lambda: deps
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()

    def discover(self, request_id, query, constraints=None):
        body = {"request_id": request_id, "query": query, "profile": {"interests": [], "skills": None},
                "constraints": constraints or {}}
        return DiscoveryResponse.model_validate(self.client.post("/api/v1/discover", json=body).json())

    def test_needs_info_is_graded_without_inventing_scores(self):
        # Our documented rule: a place in the query that disagrees with the saved preference asks the user.
        response = self.discover("TEAM-01", "ورشة في جدة", {"location_preference": ["الرياض"]})
        self.assertEqual(response.status.value, "needs_info")
        team_case = case("needs_info", constraints={"location_preference": ["الرياض"]})
        team_case["case_id"] = "TEAM-01"
        grades, errors = run_graders(GRADERS, team_case, response, NO_SPANS)
        self.assertEqual(errors, [])
        self.assertEqual({g.metric: g.score for g in grades}, {"e2e.status": 1.0})  # every other metric returns None
        wrong = case("ok", acceptable=["SRC-0001"])
        self.assertEqual(grade_status(wrong, response, NO_SPANS).score, 0.0)

    def test_details_match_the_recommendation(self):
        response = self.discover("TEAM-02", "ورشة")
        self.assertEqual(response.status.value, "ok")
        for recommendation in response.recommendations:
            details = self.client.get(f"/api/v1/opportunities/{recommendation.opportunity_id}")
            self.assertEqual(details.status_code, 200)
            body = details.json()
            self.assertEqual(body["title"], recommendation.title)
            self.assertEqual(body["deadline"], recommendation.deadline)
            self.assertEqual(body["source_ids"], recommendation.source_ids)
            self.assertEqual(body["opportunity_type"], recommendation.opportunity_type)
            self.assertEqual(body["application_status"], recommendation.application_status)


if __name__ == "__main__":
    unittest.main()
