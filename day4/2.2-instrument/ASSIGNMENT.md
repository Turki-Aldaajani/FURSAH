# 2.2 Instrument the agent · 25 minutes

**Day 4:** [Overview](../README.md) · **Previous:** [2.1 Audit](../2.1-audit/ASSIGNMENT.md)

## Goal

Every run produces **one** trace. Your framework, if you use one, records its own steps automatically; you add spans where it cannot see.

## What you are given

**`backend/app/infrastructure/observability.py`**

| Function | What it does |
|---|---|
| `setup_observability()` | Registers the tracer provider (where spans are sent: Phoenix) and switches on the **instrumentor** for what you installed. Returns the list of instrumentors it enabled. Does nothing unless `FURSAH_PHOENIX_ENABLED=true`. |
| `start_span(name, **attributes)` | Opens a custom span, nested under the framework span that is running. Use it as a `with` block. |

**What is an instrumentor?** A callback inside your framework that opens a span on every *start* event and closes it on the matching *end* event: the graph, each node, each model call, each tool call. Anything that is plain Python and does not go through your framework, such as reading your store, filtering, or validation, is invisible to it. That is where you add custom spans.

**Choose the instrumentor by what your code calls**, not by what is installed:

| Your code calls the model through | Set `FURSAH_PHOENIX_INSTRUMENT=` | Spans you get automatically |
|---|---|---|
| LangChain or LangGraph | `langchain` | Graph, nodes, model calls, tools registered with the framework |
| PydanticAI | `pydantic_ai` | Agent run, model calls, tools registered with the agent |
| The `openai` SDK directly | `openai` | Each model call (no tool or node spans: your code has to add those) |
| Something else (plain HTTP, another SDK) | `none` | None: you add the model and tool spans yourself (step 6) |

`auto` (the default) guesses from the **installed** packages. A framework you installed on Day 2 but no longer use wins the guess, and then your real model calls are not traced at all. Set the value explicitly.

**Also given:** `backend/requirements-observability.txt` and the `FURSAH_PHOENIX_*` settings in `backend/.env.example`.

## What you do

### Part A · Automatic instrumentation (10 min)

1. **Install and start Phoenix.** From `fursah-starter/backend/` with your virtual environment active:
   ```bash
   python -m pip install -r requirements-observability.txt
   phoenix serve
   ```
   Keep that terminal open; Phoenix runs at <http://localhost:6006>. In `backend/.env` add, with the value from the table above:
   ```dotenv
   FURSAH_PHOENIX_ENABLED=true
   FURSAH_PHOENIX_INSTRUMENT=openai   # or langchain / pydantic_ai / none: from the table
   ```
2. **Call `setup_observability()` once when the app starts, before the first request.** Decide where in your code that is.
3. **Add a root span around `workflow.discover()`.** Without it, every step that does not run inside one framework call becomes its own trace: two model calls through the `openai` SDK give two traces, and with `none` there is no trace at all. Record `request_id`, so you can match the Phoenix trace with the existing JSONL trace, and `deps.evaluation_time`:
   ```python
   from app.infrastructure.observability import start_span

   with start_span("workflow.discover", request_id=request.request_id,
                   evaluation_time=deps.evaluation_time.isoformat()):
       ...  # the existing body of discover()
   ```
4. **Restart the backend, run one search from the UI, and open Phoenix.** You should see a project named `fursah` with **one** trace for the search, rooted at `workflow.discover`. Check that the startup log printed `Phoenix tracing -> ...` with the instrumentors you expected. With `none`, the root span is the only span so far: that is expected.
5. **Note what is missing.** Anything that is plain Python and not called through your framework will not be there; with `none`, neither are your model and tool calls.

### Part B · Custom spans where the framework is blind (10 min)

6. **Only for `openai` or `none`: record what no instrumentor sees.** With `openai`, wrap each tool call. With `none`, wrap each model call and each tool call too. Use the attribute names Phoenix and the 2.3 helper `tool_inputs()` read:
   ```python
   with start_span("tool.search_opportunities", **{"openinference.span.kind": "TOOL",
                   "tool.name": "search_opportunities", "input.value": json.dumps(arguments)}):
       results = ...

   with start_span("llm.generate", **{"openinference.span.kind": "LLM", "input.value": prompt}) as span:
       reply = ...  # your model call
       span.set_attribute("output.value", reply_text)
       span.set_attribute("llm.token_count.prompt", prompt_tokens)
       span.set_attribute("llm.token_count.completion", completion_tokens)
   ```
   With `langchain` or `pydantic_ai`, skip this step: the instrumentor already records them.
7. **Add at least two custom spans** for the **Missing signal** column of your 2.1 table:
   ```python
   with start_span("repo.search", criteria=criteria.model_dump_json()) as span:
       results = ...
       span.set_attribute("candidate_ids", [r.source_id for r in results])
   ```
8. **Rerun.** Confirm your spans appear **inside** the root span and the framework spans around them, not as separate traces.

Attribute values must be `str`, `bool`, `int`, `float`, or lists of those. Record IDs, counts and short values, not whole objects.

### Part C · Verify (5 min)

9. **Check the final trace against this list.** Each item comes from your instrumentor or, where it sees nothing, from your own span (step 6):
   - [ ] One trace per search, rooted at `workflow.discover`
   - [ ] Input
   - [ ] Output
   - [ ] Tool calls with their arguments
   - [ ] Latency
   - [ ] **One LLM span per model call** your code makes. None means the wrong `FURSAH_PHOENIX_INSTRUMENT`, or, with `none`, a model call without its step 6 span; two per call means a model call is traced twice
   - [ ] Tokens in and out on those LLM spans
   - [ ] Custom spans nested correctly, not separate traces
10. **Fill any remaining gaps from your 2.1 table.**

## Hand in

One trace per search, rooted at `workflow.discover`, with an LLM span for each model call and a span for each tool call (from your instrumentor, or your own with `none`), plus **at least two custom spans** for missing signals, nested correctly, with the checklist ticked. Instructors will check it in Phoenix with you.

## Common problems

| Symptom | Cause and fix |
|---|---|
| No `fursah` project appears in Phoenix | `setup_observability()` was never called, or `FURSAH_PHOENIX_ENABLED` is not `true`: check for the `Phoenix tracing ->` line at startup. With `none`, the line can be there and still no trace appears until the root span (step 3) runs. |
| The startup line appears but still no traces | The endpoint port differs from Phoenix's (`FURSAH_PHOENIX_ENDPOINT`), **or the UI is sending requests to another backend**: an old server still running on port 8000. Check with `lsof -iTCP:8000 -sTCP:LISTEN` and stop the old process. |
| One search appears as several traces, or a custom span as its own trace | It ran outside the root span. Check that the root span (step 3) wraps the whole of `discover()`. |
| No LLM spans at all, although your code calls a model | `FURSAH_PHOENIX_INSTRUMENT` does not match what your code calls (the startup line shows what was chosen): set it explicitly from the table above. With `none`, add the LLM span yourself (step 6). |
| No tokens on model spans | The model is called outside an instrumented framework or SDK. Record token counts yourself on the LLM span (step 6: `llm.token_count.prompt`, `llm.token_count.completion`). |
| `Unknown FURSAH_PHOENIX_INSTRUMENT value` | Use `auto`, `langchain`, `pydantic_ai`, `openai` or `none`. |
| `FURSAH_PHOENIX_ENABLED=true but the tracing packages are not installed` | Run `pip install -r requirements-observability.txt` locally. In a deployed container, leave `FURSAH_PHOENIX_ENABLED=false`: your `start_span` calls stay in the code and do nothing. |
