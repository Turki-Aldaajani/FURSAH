"""Day 4 - 2.2: OpenTelemetry spans of one search, collected in memory (no Phoenix, no network).

Checks the worksheet list: one trace per search rooted at workflow.discover, the interpretation
and search spans nested inside it, the tool arguments, one LLM span per model step with tokens,
and no request text in any attribute.
"""
import asyncio
import contextlib
import io
import json
import os
import unittest
from dataclasses import replace
from datetime import datetime

os.environ["FURSAH_MODEL_API_KEY"] = ""
os.environ["FURSAH_TRACE_JSONL_PATH"] = ""

import httpx  # noqa: E402
from opentelemetry import trace  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

from app import agent  # noqa: E402
from app.infrastructure.dependencies import ModelClientConfig, get_app_dependencies  # noqa: E402
from app.infrastructure.tracing import Tracer  # noqa: E402
from app.schemas import DiscoveryRequest  # noqa: E402
from app.workflow import discover  # noqa: E402

EVAL = datetime.fromisoformat("2026-10-18T12:00:00+03:00")
MEMORY = InMemorySpanExporter()


def setUpModule():
    provider = trace.get_tracer_provider()
    if isinstance(provider, trace.ProxyTracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    provider.add_span_processor(SimpleSpanProcessor(MEMORY))


class Spans(unittest.TestCase):
    def tearDown(self):
        agent.HTTP_TRANSPORT = None

    def search(self, query, constraints=None, model=None):
        MEMORY.clear()
        deps = replace(get_app_dependencies(), model_config=model, tracer=Tracer(), evaluation_time=EVAL)
        request = DiscoveryRequest(request_id="span-1", query=query, profile={"interests": [], "skills": None},
                                   constraints=constraints or {})
        with contextlib.redirect_stdout(io.StringIO()):
            response = asyncio.run(discover(request, deps))
        return response, {s.name: s for s in MEMORY.get_finished_spans()}, MEMORY.get_finished_spans()

    def assert_nested(self, spans, child, parent="workflow.discover"):
        self.assertEqual(spans[child].context.trace_id, spans[parent].context.trace_id)
        self.assertEqual(spans[child].parent.span_id, spans[parent].context.span_id)

    def assert_no_query_text(self, all_spans, query):
        for span in all_spans:
            for value in span.attributes.values():
                self.assertNotIn(query, str(value), span.name)

    def test_one_trace_rooted_at_workflow_discover(self):
        query = "ورشة بايثون في الرياض"
        response, spans, all_spans = self.search(query)
        self.assertEqual({s.context.trace_id for s in all_spans}, {spans["workflow.discover"].context.trace_id})
        self.assertIsNone(spans["workflow.discover"].parent)
        self.assert_nested(spans, "query.interpret")
        self.assert_nested(spans, "tool.search_opportunities")
        root = spans["workflow.discover"].attributes
        self.assertEqual(root["request_id"], "span-1")
        self.assertEqual(root["output.status"], response.status.value)
        self.assertEqual(list(root["output.opportunity_ids"]), [r.opportunity_id for r in response.recommendations])
        tool = spans["tool.search_opportunities"].attributes
        self.assertEqual(tool["tool.name"], "search_opportunities")
        arguments = json.loads(tool["input.value"])
        self.assertEqual(arguments["cities"], ["الرياض"])
        self.assertIn("python", arguments["keywords"])
        returned = [r.opportunity_id for r in response.recommendations]
        self.assertEqual(list(tool["candidate_ids"])[:len(returned)], returned)
        self.assertGreaterEqual(spans["workflow.discover"].end_time, spans["workflow.discover"].start_time)  # latency (Windows clock can tick coarsely)
        self.assertNotIn("llm.generate", spans)  # no model configured: no LLM span
        self.assert_no_query_text(all_spans, query)

    def test_needs_info_records_the_conflict_and_no_search(self):
        response, spans, _ = self.search("ورشة في جدة", {"location_preference": ["الرياض"]})
        self.assertEqual(response.status.value, "needs_info")
        self.assertEqual(list(spans["query.interpret"].attributes["conflicts"]), ["constraints.location_preference"])
        self.assertNotIn("tool.search_opportunities", spans)
        self.assertEqual(spans["workflow.discover"].attributes["output.status"], "needs_info")

    def test_one_llm_span_per_model_step_with_tokens(self):
        plan = {"filters": [{"field": "type", "value": "workshop", "quote": "ورشة"}], "city_or_mode_alternatives": False}
        body = {"choices": [{"message": {"content": json.dumps(plan, ensure_ascii=False)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 321, "completion_tokens": 45, "total_tokens": 366}}
        agent.HTTP_TRANSPORT = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
        model = ModelClientConfig(provider="openrouter", model_name="test/model", api_key="test-key-not-real",
                                  base_url="https://example.invalid/v1", timeout_seconds=5, max_retries=0)
        query = "ورشة تصميم"
        _, spans, all_spans = self.search(query, model=model)
        self.assertEqual(sum(1 for s in all_spans if s.name == "llm.generate"), 1)
        self.assert_nested(spans, "llm.generate")
        llm = spans["llm.generate"].attributes
        self.assertEqual((llm["openinference.span.kind"], llm["llm.model_name"]), ("LLM", "test/model"))
        self.assertEqual((llm["llm.token_count.prompt"], llm["llm.token_count.completion"]), (321, 45))
        self.assertEqual(llm["model.outcome"], "accepted")
        self.assertTrue(spans["query.interpret"].attributes["model_plan_applied"])
        self.assert_no_query_text(all_spans, query)
        for span in all_spans:
            self.assertNotIn("test-key-not-real", json.dumps(dict(span.attributes), ensure_ascii=False, default=str))


if __name__ == "__main__":
    unittest.main()
