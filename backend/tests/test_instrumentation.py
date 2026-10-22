"""Day 4 - 2.2: structured trace events (JSONL) for the search path.

No test contacts a network (model replies come from httpx.MockTransport; the key is a fake string).
Run from backend/: .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest import mock

os.environ["FURSAH_EVALUATION_TIME"] = "2026-10-18T12:00:00+03:00"
os.environ["FURSAH_MODEL_API_KEY"] = ""  # automated tests never call a real model, whatever backend/.env holds
os.environ["FURSAH_TRACE_JSONL_PATH"] = ""  # ...and never write into the real trace file named in backend/.env

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as main_module  # noqa: E402
import app.routes as routes_module  # noqa: E402
from app import agent, instrumentation as ins  # noqa: E402
from app.infrastructure.config import Settings  # noqa: E402
from app.infrastructure.dependencies import ModelClientConfig, get_app_dependencies  # noqa: E402
from app.infrastructure.errors import WorkflowBudgetExceededError  # noqa: E402
from app.infrastructure.tracing import Tracer, clean_attributes  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas import TraceEvent  # noqa: E402

EVAL = datetime.fromisoformat("2026-10-18T12:00:00+03:00")
TEST_KEY = "test-key-not-real-1234567890"
NO_PROFILE = {"interests": [], "skills": None}
CORE = ("request_id", "sequence", "step", "component", "outcome", "elapsed_ms", "evidence_ids")


def model_config(**changes):
    values = dict(provider="openrouter", model_name="deepseek/deepseek-v4-flash", api_key=TEST_KEY,
                  base_url="https://openrouter.ai/api/v1", timeout_seconds=5, max_retries=1)
    return ModelClientConfig(**{**values, **changes})


def provider_reply(filters, **extra):
    content = json.dumps({"filters": filters, "city_or_mode_alternatives": False}, ensure_ascii=False)
    body = {"provider": "TestProvider", "model": "deepseek/deepseek-v4-flash",
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": 0.0001,
                      "completion_tokens_details": {"reasoning_tokens": 0}}, **extra}
    return httpx.Response(200, json=body), content


class InstrumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.real = get_app_dependencies()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.trace = Path(self.tmp.name) / f"{self._testMethodName}.jsonl"
        self.install(str(self.trace))
        self._backoff, agent.RETRY_BACKOFF_SECONDS = agent.RETRY_BACKOFF_SECONDS, 0

    def tearDown(self):
        app.dependency_overrides.clear()
        agent.HTTP_TRANSPORT = None
        agent.RETRY_BACKOFF_SECONDS = self._backoff

    def install(self, trace_path, model=None):
        self.settings = Settings(_env_file=None, trace_jsonl_path=trace_path, evaluation_time="2026-10-18T12:00:00+03:00")
        deps = replace(self.real, settings=self.settings, model_config=model)
        app.dependency_overrides[get_app_dependencies] = lambda: deps
        self.client = TestClient(app, raise_server_exceptions=False)

    # -- helpers -------------------------------------------------------------------------
    def post(self, request_id, query, profile=None, constraints=None, **extra):
        body = {"request_id": request_id, "query": query, "profile": profile or NO_PROFILE,
                "constraints": constraints or {}, **extra}
        return self.client.post("/api/v1/discover", json=body)

    def events(self, request_id=None):
        """Every line must be an independent JSON object (that is the point of JSONL)."""
        if not self.trace.is_file():
            return []
        lines = self.trace.read_text(encoding="utf-8").splitlines()
        parsed = [json.loads(line) for line in lines]
        return [e for e in parsed if request_id is None or e["request_id"] == request_id]

    def by_step(self, events):
        return {e["step"]: e for e in events}

    def file_text(self):
        return self.trace.read_text(encoding="utf-8") if self.trace.is_file() else ""

    # -- the chain -----------------------------------------------------------------------
    def test_successful_request_produces_a_linked_chain(self):
        response = self.post("t-ok", "ورشة")
        body = response.json()
        self.assertEqual((response.status_code, body["status"]), (200, "ok"))
        events = self.events("t-ok")
        self.assertEqual([e["step"] for e in events],
                         ["request_received", "retrieval_completed", "eligibility_checked", "response_ready"])
        self.assertEqual([e["sequence"] for e in events], [1, 2, 3, 4])
        self.assertEqual({e["request_id"] for e in events}, {body["request_id"]})
        self.assertEqual(len(self.events()), len(events))  # one JSON object per line, nothing else in the file
        for e in events:  # compatible with the organizer's TraceEvent schema (extra attributes are ignored)
            TraceEvent.model_validate(e)
            TraceEvent.model_validate({k: e[k] for k in CORE})
        ready = events[-1]
        recs = body["recommendations"]
        self.assertEqual(ready["attributes"]["opportunity_ids"], [r["opportunity_id"] for r in recs])
        self.assertEqual(ready["evidence_ids"], [s for r in recs for s in r["source_ids"]])
        self.assertEqual(ready["attributes"]["evidence_version"], recs[0]["evidence_version"])
        self.assertEqual((ready["attributes"]["status"], ready["outcome"]), ("ok", "ok"))
        self.assertGreater(ready["elapsed_ms"], 0)

    def test_request_received_is_structured_and_private(self):
        profile = {"city": "الرياض", "field_of_study": "علوم الحاسب", "interests": ["الصحة العامة"],
                   "skills": [{"name": "Arduino", "level": None}], "user_id": "user-123"}
        query = "ورشة ذات موضوع خاص جدًا"
        self.post("t-priv", query, profile=profile, constraints={"max_budget_sar": 100, "delivery_modes": ["online"]})
        attrs = self.by_step(self.events("t-priv"))["request_received"]["attributes"]
        self.assertEqual(attrs["evaluation_time"], EVAL.isoformat())
        self.assertEqual(attrs["evaluation_time_source"], "fixed")
        self.assertEqual(attrs["query"], {"chars": len(query), "sha256_16": hashlib.sha256(query.encode()).hexdigest()[:16]})
        self.assertEqual((attrs["constraints"]["max_budget_sar"], attrs["constraints"]["delivery_modes"]), (100.0, ["online"]))
        self.assertEqual(attrs["profile"], {"has_city": True, "has_field_of_study": True, "has_education_level": False,
                                            "has_user_id": True, "n_interests": 1, "n_skills": 1, "skills_provided": True})
        self.assertEqual(attrs["config"]["jsonl_sink"], True)
        text = self.file_text()
        for private in (query, "علوم الحاسب", "الصحة العامة", "Arduino", "user-123"):
            self.assertNotIn(private, text)

    def test_retrieval_completed_lists_candidates_and_exclusions(self):
        body = self.post("t-ret", "ورشة", constraints={"max_budget_sar": 100}).json()
        attrs = self.by_step(self.events("t-ret"))["retrieval_completed"]["attributes"]
        self.assertEqual(attrs["criteria"]["types"], ["workshop"])
        self.assertEqual(attrs["criteria"]["max_budget_sar"], 100.0)
        ranking = attrs["ranking"]
        self.assertEqual([r["rank"] for r in ranking], list(range(1, len(ranking) + 1)))
        self.assertEqual([r["opportunity_id"] for r in ranking if r["returned"]],
                         [r["opportunity_id"] for r in body["recommendations"]])
        scores = [(r["score"], r["profile_score"]) for r in ranking]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(attrs["n_opportunities"], len(self.real.repository.all()))
        # every opportunity is either a candidate or excluded for exactly one reason
        self.assertEqual(attrs["n_candidates"] + sum(attrs["excluded_counts"].values()), attrs["n_opportunities"])
        self.assertIn("type", attrs["excluded_counts"])
        for reason, ids in attrs["excluded_ids_sample"].items():
            self.assertLessEqual(len(ids), ins.MAX_EXCLUDED_IDS)
            self.assertLessEqual(len(ids), attrs["excluded_counts"][reason])

    def test_eligibility_facts_separate_known_from_unknown(self):
        repo, skills = self.real.repository, set()
        unknown = ins.eligibility_facts(repo.find("SRC-0004"), EVAL, skills)  # "لم تُحدد رسوم المشاركة", no deadline
        self.assertEqual((unknown["fee"]["known"], unknown["deadline"]["known"]), (False, False))
        self.assertIn("fee_unknown", unknown["uncertain_because"])
        self.assertIn("deadline_unknown", unknown["uncertain_because"])
        conflict = ins.eligibility_facts(repo.find("SRC-0172"), EVAL, skills)  # 120 vs 135 across two sources
        self.assertEqual((conflict["fee"]["conflicting"], conflict["fee"]["claims_sar"]), (True, [120.0, 135.0]))
        self.assertIn("fee_conflicting", conflict["uncertain_because"])
        self.assertTrue(ins.eligibility_facts(repo.find("SRC-0062"), EVAL, skills)["cancelled"])
        study = ins.eligibility_facts(repo.find("SRC-0010"), EVAL, skills)  # "السنة الأخيرة" is mandatory
        self.assertIn("study_year_requirement_not_collected_by_profile", study["uncertain_because"])
        known = ins.eligibility_facts(repo.find("SRC-0006"), EVAL, skills)
        self.assertEqual((known["fee"]["known"], known["fee"]["value_sar"]), (True, 120.0))
        self.assertEqual(known["deadline"]["state"], "passed")
        needs_python = ins.eligibility_facts(repo.find("SRC-0925"), EVAL, skills)  # "معرفة Python مطلوبة"
        self.assertIn("required_skill_not_in_profile:Python", needs_python["uncertain_because"])
        self.assertEqual(needs_python["requirements"]["profile_has_skill"], {"Python": False})
        has_python = ins.eligibility_facts(repo.find("SRC-0925"), EVAL, {"python"})
        self.assertNotIn("required_skill_not_in_profile:Python", has_python["uncertain_because"])

    def test_eligibility_checked_matches_the_returned_recommendations(self):
        body = self.post("t-elig", "ورشة").json()
        event = self.by_step(self.events("t-elig"))["eligibility_checked"]
        self.assertEqual([r["opportunity_id"] for r in event["attributes"]["recommendations"]],
                         [r["opportunity_id"] for r in body["recommendations"]])
        self.assertEqual(event["outcome"], "uncertain")
        for rec in event["attributes"]["recommendations"]:
            self.assertEqual(rec["eligibility_status"], "uncertain")
            self.assertIn("known", rec["fee"])

    # -- the model step ------------------------------------------------------------------
    def test_model_interpretation_records_what_the_provider_returned(self):
        reply, content = provider_reply([{"field": "topic", "value": "Python", "quote": "بيثون"}])
        agent.HTTP_TRANSPORT = httpx.MockTransport(lambda request: reply)
        self.install(str(self.trace), model=model_config())
        self.post("t-model", "أبغى أتعلم بيثون")
        event = self.by_step(self.events("t-model"))["model_interpretation"]
        a = event["attributes"]
        self.assertEqual(event["outcome"], "accepted")
        self.assertEqual(a["configured"], {"provider": "openrouter", "model": "deepseek/deepseek-v4-flash",
                                           "host": "openrouter.ai", "reasoning_disabled": True})
        self.assertEqual(a["upstream"], {"upstream_provider": "TestProvider", "upstream_model": "deepseek/deepseek-v4-flash"})
        self.assertEqual((a["finish_reason"], a["http_status"], a["attempts"]), ("stop", 200, 1))
        self.assertEqual(a["usage"], {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "reasoning_tokens": 0})
        self.assertEqual(a["cost_usd"], 0.0001)
        self.assertEqual(a["raw"], {"chars": len(content), "sha256_16": hashlib.sha256(content.encode()).hexdigest()[:16]})
        self.assertEqual(a["validation"], {"accepted": True, "rejection": None})
        self.assertEqual(a["plan"]["topics"], ["Python"])
        self.assertTrue(a["filters_checked"][0]["quote_found"])
        text = self.file_text()
        self.assertNotIn(content, text)            # the raw reply is never stored
        self.assertNotIn("أبغى أتعلم بيثون", text)  # nor the request
        self.assertNotIn(TEST_KEY, text)

    def test_unknown_search_terms_are_logged_as_hashes_known_ones_in_clear(self):
        # The model's topic and the rule keywords are fragments of the request. A term that occurs in
        # the announcement titles is kept (evaluation needs it); any other term becomes a short hash.
        query = "ورشة في الحوسبة الكمومية للمريخ"
        reply, _ = provider_reply([{"field": "type", "value": "workshop", "quote": "ورشة"},
                                   {"field": "topic", "value": "الحوسبة الكمومية", "quote": "الحوسبة الكمومية"}])
        agent.HTTP_TRANSPORT = httpx.MockTransport(lambda request: reply)
        self.install(str(self.trace), model=model_config())
        self.assertEqual(self.post("t-red", query).json()["status"], "no_match")
        text = self.file_text()
        for word in ("الحوسبة", "الكمومية", "للمريخ", "مريخ", "كموميه", "حوسبه"):
            self.assertNotIn(word, text, word)
        events = self.by_step(self.events("t-red"))
        plan_topics = events["model_interpretation"]["attributes"]["plan"]["topics"]
        self.assertTrue(all(t.startswith("?") for t in plan_topics[0].split()), plan_topics)
        keywords = events["retrieval_completed"]["attributes"]["criteria"]["keywords"]
        self.assertTrue(keywords and all(k.startswith("?") for k in keywords), keywords)
        # a term that IS in the titles stays readable
        self.post("t-known", "ورشة بايثون")
        known = self.by_step(self.events("t-known"))["retrieval_completed"]["attributes"]["criteria"]["keywords"]
        self.assertEqual(known, ["python"])

    def test_a_rejected_value_typed_by_the_user_is_not_logged(self):
        reply, _ = provider_reply([{"field": "city", "value": "نيوم", "quote": "نيوم"}])
        agent.HTTP_TRANSPORT = httpx.MockTransport(lambda request: reply)
        self.install(str(self.trace), model=model_config())
        self.post("t-city", "ورشة في نيوم")
        attrs = self.by_step(self.events("t-city"))["model_interpretation"]["attributes"]
        self.assertEqual(attrs["validation"]["rejection"]["code"], "value_not_allowed")
        checked = attrs["filters_checked"][0]
        self.assertEqual((checked["field"], checked["value_allowed"]), ("city", False))
        self.assertTrue(checked["value"].startswith("?"))
        self.assertNotIn("نيوم", self.file_text())

    def test_rejected_plan_records_a_machine_readable_reason(self):
        reply, _ = provider_reply([{"field": "type", "value": "workshop", "quote": "ورشة"}])
        agent.HTTP_TRANSPORT = httpx.MockTransport(lambda request: reply)
        self.install(str(self.trace), model=model_config())
        self.assertEqual(self.post("t-rej", "ورشة في الدمام").json()["status"], "ok")  # still answered
        event = self.by_step(self.events("t-rej"))["model_interpretation"]
        rejection = event["attributes"]["validation"]["rejection"]
        self.assertEqual(event["outcome"], "FURSAH_INVALID_MODEL_OUTPUT")
        self.assertEqual((rejection["code"], rejection["info"]), ("dropped_explicit_constraint", {"constraint": "cities"}))
        self.assertFalse(event["attributes"]["validation"]["accepted"])
        self.assertIsNone(event["attributes"]["plan"])
        self.assertEqual(event["attributes"]["rules_reading"]["cities"], ["الدمام"])

    def test_provider_failures_and_empty_replies_are_explained(self):
        cases = [
            ("http_401", lambda r: httpx.Response(401, json={"error": "bad key"}), {"http_status": 401, "attempts": 1}),
            ("no_text", lambda r: httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": None}}],
                                                             "usage": {"completion_tokens": 500}}),
             {"finish_reason": "length", "http_status": 200}),
        ]
        for code, handler, expected in cases:
            agent.HTTP_TRANSPORT = httpx.MockTransport(handler)
            self.install(str(self.trace), model=model_config())
            rid = f"t-{code}"
            self.assertEqual(self.post(rid, "أبحث عن ورشة").status_code, 200)
            a = self.by_step(self.events(rid))["model_interpretation"]["attributes"]
            self.assertEqual(a["validation"]["rejection"]["code"], code)
            for key, value in expected.items():
                self.assertEqual(a[key], value, (code, key))

    # -- other outcomes ------------------------------------------------------------------
    def test_no_match_explains_itself(self):
        body = self.post("t-nm", "ورشة في الحوسبة الكمومية للمريخ").json()
        self.assertEqual(body["status"], "no_match")
        events = self.events("t-nm")
        self.assertEqual([e["step"] for e in events], ["request_received", "retrieval_completed", "response_ready"])
        retrieval, ready = events[1], events[2]
        self.assertEqual((retrieval["outcome"], retrieval["attributes"]["n_candidates"]), ("no_candidates", 0))
        self.assertTrue(retrieval["attributes"]["excluded_counts"])
        self.assertEqual(ready["attributes"]["no_match"]["cause"], "no_candidates_after_filters")
        self.assertEqual(ready["evidence_ids"], [])

    def test_needs_info_records_the_conflict(self):
        body = self.post("t-ni", "ورشة في جدة", constraints={"location_preference": ["الرياض"]}).json()
        self.assertEqual(body["status"], "needs_info")
        events = self.events("t-ni")
        self.assertEqual([e["step"] for e in events], ["request_received", "response_ready"])
        info = events[-1]["attributes"]["needs_info"]
        self.assertEqual((info["missing_fields"], info["constraint_conflict"]), (["constraints.location_preference"], True))

    def test_invalid_request_is_traced_by_field_names_only(self):
        secret = "سر" * 1500  # 3000 characters: over the 2000 limit, so rejected
        response = self.client.post("/api/v1/discover", headers={"x-request-id": "t-422"},
                                    json={"query": secret, "profile": NO_PROFILE, "constraints": {}})
        self.assertEqual(response.status_code, 422)
        with mock.patch.object(main_module, "settings", self.settings):
            response = self.client.post("/api/v1/discover", headers={"x-request-id": "t-422"},
                                        json={"query": secret, "profile": NO_PROFILE, "constraints": {}})
        self.assertEqual(response.status_code, 422)
        events = self.events("t-422")
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["step"], events[0]["outcome"]), ("request_failed", "FURSAH_REQUEST_VALIDATION_ERROR"))
        self.assertEqual(events[0]["attributes"], {"n_errors": 1, "invalid_fields": ["query"]})
        self.assertNotIn(secret[:20], self.file_text())

    def test_runtime_error_is_traced_by_type_only(self):
        async def boom(request, deps):
            raise RuntimeError("private detail 987654")
        with mock.patch.object(routes_module, "discover", boom):
            response = self.post("t-500", "ورشة")
        self.assertEqual(response.status_code, 500)
        events = self.events("t-500")
        self.assertEqual([e["step"] for e in events], ["request_received", "request_failed"])
        self.assertEqual((events[-1]["outcome"], events[-1]["attributes"]), ("FURSAH_INTERNAL_ERROR", {"error": {"exception_type": "RuntimeError"}}))
        self.assertNotIn("987654", self.file_text())

    def test_controlled_error_is_traced_with_its_public_code(self):
        async def slow(request, deps):
            raise WorkflowBudgetExceededError()
        with mock.patch.object(routes_module, "discover", slow):
            response = self.post("t-503", "ورشة")
        self.assertEqual(response.status_code, 503)
        failed = self.events("t-503")[-1]
        self.assertEqual((failed["step"], failed["outcome"]), ("request_failed", "FURSAH_INTERNAL_ERROR"))
        self.assertEqual(failed["attributes"]["error"]["kind"], "WorkflowBudgetExceededError")

    # -- tracing must never change or break a search -------------------------------------
    def test_tracing_does_not_change_the_response(self):
        queries = ["ورشة", "دورة بايثون للمبتدئين", "فرص صحية في أبها أو عن بعد", "ورشة في الحوسبة الكمومية للمريخ", "أبغى تدريب عملي في الدمام"]
        traced = [self.post(f"t-same-{i}", q).json() for i, q in enumerate(queries)]
        self.install("")  # no JSONL sink (stdout only), exactly like before this change
        plain = [self.post(f"t-same-{i}", q).json() for i, q in enumerate(queries)]
        self.assertEqual(traced, plain)

    def test_unwritable_trace_does_not_stop_the_search(self):
        expected = self.post("t-w", "ورشة").json()
        for broken in (self.tmp.name,                                   # a directory, not a file
                       str(Path(self.tmp.name) / "afile" / "x.jsonl")):  # parent is a regular file
            (Path(self.tmp.name) / "afile").write_text("x", encoding="utf-8")
            self.install(broken)
            response = self.post("t-w", "ورشة")
            self.assertEqual(response.status_code, 200, broken)
            self.assertEqual(response.json(), expected, broken)

    def test_emit_never_raises(self):
        tracer = Tracer(self.tmp.name)  # unwritable on purpose
        event = tracer.emit(request_id="t-e", step="x", component="c", outcome="o", attributes={"bad": object(), "nan": float("nan")})
        self.assertIsNotNone(event)  # stdout still worked
        self.assertIsNone(Tracer("").emit(request_id="", step="x", component="c", outcome="o"))  # invalid event is dropped, not raised

    def test_console_that_cannot_show_arabic_still_gets_the_event(self):
        # Seen while testing: with stdout piped through a legacy code page, printing Arabic raised
        # UnicodeEncodeError. The event must still reach the console (escaped) and the JSONL file.
        console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", write_through=True)
        with contextlib.redirect_stdout(console):
            Tracer(str(self.trace)).emit(request_id="t-enc", step="request_received", component="api", outcome="success",
                                         attributes={"cities": ["الرياض"]})
        shown = console.buffer.getvalue().decode("ascii")
        self.assertEqual(json.loads(shown)["attributes"]["cities"], ["الرياض"])  # escapes decode back to the Arabic
        self.assertEqual(self.events("t-enc")[0]["attributes"]["cities"], ["الرياض"])  # the file keeps real UTF-8
        self.assertIn("الرياض", self.file_text())

    def test_attribute_sanitizer(self):
        cleaned = clean_attributes({"api_key": "k", "Authorization": "a", "total_tokens": 5, "model_api_key": "k",
                                    "long": "x" * 1000, "items": list(range(500)), "tags": {"b", "a"},
                                    "when": EVAL, "nested": {"password": "p", "ok": 1}, "nan": float("nan")})
        self.assertNotIn("api_key", cleaned)
        self.assertNotIn("Authorization", cleaned)
        self.assertNotIn("model_api_key", cleaned)
        self.assertEqual(cleaned["total_tokens"], 5)
        self.assertEqual(len(cleaned["long"]), 301)
        self.assertEqual(len(cleaned["items"]), 50)
        self.assertEqual(cleaned["tags"], ["a", "b"])
        self.assertEqual(cleaned["when"], EVAL.isoformat())
        self.assertEqual(cleaned["nested"], {"ok": 1})
        self.assertIsNone(cleaned["nan"])
        json.dumps(cleaned)  # always serializable

    def test_no_model_means_no_model_event(self):
        self.post("t-nomodel", "ورشة")
        self.assertNotIn("model_interpretation", [e["step"] for e in self.events("t-nomodel")])


if __name__ == "__main__":
    unittest.main()
