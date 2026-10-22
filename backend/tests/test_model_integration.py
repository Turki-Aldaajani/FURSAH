"""Model-assisted query interpretation (OpenRouter, OpenAI-compatible protocol).

No test here contacts a network: every provider reply comes from httpx.MockTransport, and
the key is a fake string. Run from backend/: .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
import asyncio
import contextlib
import io
import json
import os
import unittest
from dataclasses import replace
from datetime import datetime

os.environ["FURSAH_EVALUATION_TIME"] = "2026-10-18T12:00:00+03:00"
os.environ["FURSAH_MODEL_API_KEY"] = ""  # automated tests never call a real model, whatever backend/.env holds
os.environ["FURSAH_TRACE_JSONL_PATH"] = ""  # ...and never write into the real trace file named in backend/.env

import httpx  # noqa: E402

from app import agent  # noqa: E402
from app.infrastructure.config import Settings  # noqa: E402
from app.infrastructure.dependencies import ModelClientConfig, build_model_config, get_app_dependencies  # noqa: E402
from app.infrastructure.tracing import Tracer  # noqa: E402
from app.schemas import DiscoveryRequest  # noqa: E402
from app.workflow import discover  # noqa: E402

TEST_KEY = "test-key-not-real-1234567890"
EVAL = datetime.fromisoformat("2026-10-18T12:00:00+03:00")
FALLBACK_LINE = "القواعد الأساسية"
MODEL_LINE = "بمساعدة النموذج"


def config(**changes):
    values = dict(provider="openrouter", model_name="deepseek/deepseek-v4-flash", api_key=TEST_KEY,
                  base_url="https://openrouter.ai/api/v1", timeout_seconds=5, max_retries=1)
    return ModelClientConfig(**{**values, **changes})


def reply(filters, alternatives=False):
    plan = {"filters": filters, "city_or_mode_alternatives": alternatives}
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(plan, ensure_ascii=False)}}]})


def flt(field, value, quote):
    return {"field": field, "value": value, "quote": quote}


class ModelIntegration(unittest.TestCase):
    def setUp(self):
        self._backoff, agent.RETRY_BACKOFF_SECONDS = agent.RETRY_BACKOFF_SECONDS, 0

    def tearDown(self):
        agent.RETRY_BACKOFF_SECONDS = self._backoff
        agent.HTTP_TRANSPORT = None

    def run_query(self, query, handler=None, cfg="default", profile=None):
        calls = []

        async def wrapped(request):
            calls.append(request)
            result = handler(request)
            return await result if asyncio.iscoroutine(result) else result

        agent.HTTP_TRANSPORT = httpx.MockTransport(wrapped)
        model = config() if cfg == "default" else cfg
        deps = replace(get_app_dependencies(), model_config=model, tracer=Tracer(), evaluation_time=EVAL)
        request = DiscoveryRequest(request_id="t1", query=query, profile=profile or {"interests": [], "skills": None},
                                   constraints={})
        trace = io.StringIO()
        with contextlib.redirect_stdout(trace):
            response = asyncio.run(discover(request, deps))
        body = response.model_dump(mode="json")
        self.assertNotIn(TEST_KEY, json.dumps(body, ensure_ascii=False))
        self.assertNotIn(TEST_KEY, trace.getvalue())
        return body, calls, trace.getvalue()

    def baseline(self, query):
        deps = replace(get_app_dependencies(), model_config=None, tracer=Tracer(), evaluation_time=EVAL)
        with contextlib.redirect_stdout(io.StringIO()):
            return asyncio.run(discover(DiscoveryRequest(request_id="t0", query=query, profile={"interests": [], "skills": None},
                                                         constraints={}), deps)).model_dump(mode="json")

    @staticmethod
    def ids(body):
        return [r["opportunity_id"] for r in body["recommendations"]]

    def assert_fell_back(self, query, body, trace, code):
        base = self.baseline(query)
        self.assertEqual((body["status"], self.ids(body)), (base["status"], self.ids(base)))
        self.assertTrue(any(FALLBACK_LINE in f for f in body["interpreted_filters"]))
        self.assertIn(f'"outcome": "{code}"', trace)

    # --- configuration -------------------------------------------------------------------
    def test_openrouter_settings_build_a_config_only_with_a_key(self):
        base = dict(_env_file=None, model_provider="openrouter", model_name="deepseek/deepseek-v4-flash",
                    model_base_url="https://openrouter.ai/api/v1")
        self.assertIsNone(build_model_config(Settings(**base, model_api_key="")))
        built = build_model_config(Settings(**base, model_api_key=TEST_KEY))
        self.assertEqual((built.provider, built.model_name), ("openrouter", "deepseek/deepseek-v4-flash"))
        self.assertNotIn(TEST_KEY, repr(built))

    def test_no_model_configured_means_no_network_call(self):
        calls = []
        agent.HTTP_TRANSPORT = httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(500))
        body = self.baseline("أبحث عن ورشة")
        self.assertEqual((body["status"], calls), ("ok", []))
        self.assertFalse(any("تفسير الطلب" in f for f in body["interpreted_filters"]))

    # --- request shape and privacy -------------------------------------------------------
    def test_request_goes_to_openrouter_with_minimal_data(self):
        seen = {}

        def handler(request):
            seen["url"], seen["auth"] = str(request.url), request.headers.get("authorization")
            seen["body"] = json.loads(request.content)
            return reply([flt("type", "workshop", "ورشة")])

        profile = {"city": "الرياض", "field_of_study": "علوم الحاسب", "interests": ["الصحة العامة"],
                   "skills": [{"name": "Arduino", "level": None}]}
        body, calls, _ = self.run_query("أريد ورشة", handler, profile=profile)
        self.assertEqual(len(calls), 1)
        self.assertEqual(seen["url"], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(seen["auth"], f"Bearer {TEST_KEY}")
        sent = seen["body"]
        self.assertEqual((sent["model"], sent["temperature"], sent["response_format"]),
                         ("deepseek/deepseek-v4-flash", 0, {"type": "json_object"}))
        self.assertEqual(sent["reasoning"], {"enabled": False})  # a reasoning model must not spend max_tokens thinking
        payload = json.loads(sent["messages"][1]["content"])
        self.assertEqual(set(payload), {"request", "cities"})
        text = json.dumps(sent, ensure_ascii=False)
        for private in ("علوم الحاسب", "الصحة العامة", "Arduino", TEST_KEY):
            self.assertNotIn(private, text)
        self.assertTrue(any(MODEL_LINE in f for f in body["interpreted_filters"]))

    # --- accepted plans ------------------------------------------------------------------
    def test_model_resolves_a_spelling_the_base_rules_cannot(self):
        query = "أبغى أتعلم بيثون"
        self.assertEqual(self.baseline(query)["status"], "no_match")
        body, _, _ = self.run_query(query, lambda r: reply([flt("topic", "Python", "بيثون")]))
        self.assertEqual(body["status"], "ok")
        self.assertTrue(all("python" in r["title"].lower() for r in body["recommendations"]))
        self.assertTrue(any(MODEL_LINE in f for f in body["interpreted_filters"]))

    def test_model_never_removes_what_the_base_rules_found(self):
        query = "ورشة في الدمام"
        body, _, _ = self.run_query(query, lambda r: reply(
            [flt("type", "workshop", "ورشة"), flt("city", "الدمام", "الدمام")]))
        base = self.baseline(query)
        self.assertEqual(self.ids(body), self.ids(base))

    def test_alternatives_flag_needs_the_word_or_in_the_request(self):
        handler = lambda r: reply([flt("city", "أبها", "أبها"), flt("delivery_mode", "online", "عن بعد")], alternatives=True)
        body, _, _ = self.run_query("ورشة في أبها عن بعد", handler)
        self.assertFalse(any("بدائل" in f for f in body["interpreted_filters"]))

    # --- rejected plans fall back, never invent ------------------------------------------
    def test_quote_not_in_the_request_is_rejected(self):
        query = "ورشة بايثون"
        body, calls, trace = self.run_query(query, lambda r: reply([flt("city", "الدمام", "في الدمام")]))
        self.assert_fell_back(query, body, trace, "FURSAH_INVALID_MODEL_OUTPUT")

    def test_city_outside_the_closed_list_is_rejected(self):
        query = "ورشة في نيوم"
        body, _, trace = self.run_query(query, lambda r: reply([flt("city", "نيوم", "نيوم")]))
        self.assert_fell_back(query, body, trace, "FURSAH_INVALID_MODEL_OUTPUT")

    def test_plan_that_drops_an_explicit_city_is_rejected(self):
        query = "ورشة في الدمام"
        body, _, trace = self.run_query(query, lambda r: reply([flt("type", "workshop", "ورشة")]))
        self.assert_fell_back(query, body, trace, "FURSAH_INVALID_MODEL_OUTPUT")

    def test_plan_that_changes_the_budget_is_rejected(self):
        query = "ورشة مجانية"
        body, _, trace = self.run_query(query, lambda r: reply(
            [flt("type", "workshop", "ورشة"), flt("max_budget_sar", 50, "مجانية")]))
        self.assert_fell_back(query, body, trace, "FURSAH_INVALID_MODEL_OUTPUT")

    def test_not_json_and_wrong_shape_are_rejected(self):
        for content in ("هذا ليس JSON", json.dumps({"filters": "x"})):
            body, _, trace = self.run_query("أبحث عن ورشة", lambda r, c=content: httpx.Response(
                200, json={"choices": [{"message": {"content": c}}]}))
            self.assert_fell_back("أبحث عن ورشة", body, trace, "FURSAH_INVALID_MODEL_OUTPUT")

    # --- regression: HTTP 200 with no text must never crash the request -----------------
    def test_null_or_empty_content_falls_back_instead_of_crashing(self):
        # Seen live: a reasoning model used all max_tokens thinking and returned content=null
        # with finish_reason "length". That raised AttributeError -> HTTP 500 -> the browser
        # (no CORS header on that 500) showed FURSAH_NETWORK_ERROR while the server was fine.
        for message in ({"content": None}, {"content": ""}, {"content": "   "}, {"content": ["part"]}, {}):
            body, calls, trace = self.run_query("فرص صحية في أبها أو عن بعد", lambda r, m=message: httpx.Response(
                200, json={"choices": [{"finish_reason": "length", "message": m}]}))
            self.assertEqual(len(calls), 1, message)
            self.assert_fell_back("فرص صحية في أبها أو عن بعد", body, trace, "FURSAH_INVALID_MODEL_OUTPUT")

    def test_reasoning_switch_is_sent_only_to_openrouter(self):
        sent = {}

        def handler(request):
            sent.update(json.loads(request.content))
            return reply([])

        self.run_query("أبحث عن ورشة", handler, cfg=config(provider="openai", base_url="https://api.openai.com/v1"))
        self.assertNotIn("reasoning", sent)  # other OpenAI-compatible APIs reject unknown parameters

    # --- provider failures ---------------------------------------------------------------
    def test_auth_failure_is_not_retried_and_falls_back(self):
        body, calls, trace = self.run_query("أبحث عن ورشة", lambda r: httpx.Response(401, json={"error": {"message": "bad key"}}))
        self.assertEqual(len(calls), 1)
        self.assert_fell_back("أبحث عن ورشة", body, trace, "FURSAH_MODEL_ERROR")

    def test_server_error_is_retried_once_then_accepted(self):
        answers = [httpx.Response(500), reply([flt("type", "workshop", "ورشة")])]
        body, calls, _ = self.run_query("أبحث عن ورشة", lambda r: answers.pop(0))
        self.assertEqual(len(calls), 2)
        self.assertTrue(any(MODEL_LINE in f for f in body["interpreted_filters"]))

    def test_repeated_timeouts_stop_after_the_configured_retries(self):
        def handler(request):
            raise httpx.ReadTimeout("slow", request=request)
        body, calls, trace = self.run_query("أبحث عن ورشة", handler)
        self.assertEqual(len(calls), 2)  # 1 + FURSAH_MODEL_MAX_RETRIES
        self.assert_fell_back("أبحث عن ورشة", body, trace, "FURSAH_MODEL_ERROR")

    def test_slow_provider_is_cut_off_by_the_model_time_budget(self):
        async def slow(request):
            await asyncio.sleep(3)
            return reply([])
        started = datetime.now()
        body, _, trace = self.run_query("أبحث عن ورشة", slow, cfg=config(timeout_seconds=1))
        self.assertLess((datetime.now() - started).total_seconds(), 2.5)
        self.assert_fell_back("أبحث عن ورشة", body, trace, "FURSAH_MODEL_ERROR")

    def test_unsupported_provider_and_insecure_url_make_no_request(self):
        for bad in (config(provider="unknown"), config(base_url="http://example.com/v1"),
                    config(base_url="https://user:pass@openrouter.ai/api/v1")):
            body, calls, trace = self.run_query("أبحث عن ورشة", lambda r: reply([]), cfg=bad)
            self.assertEqual(calls, [])
            self.assert_fell_back("أبحث عن ورشة", body, trace, "FURSAH_MODEL_ERROR")


if __name__ == "__main__":
    unittest.main()
