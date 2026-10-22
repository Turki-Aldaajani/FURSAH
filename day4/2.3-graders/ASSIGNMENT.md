# 2.3 Choose metrics and write graders · 30 minutes

**Day 4:** [Overview](../README.md) · **Previous:** [2.2 Instrument](../2.2-instrument/ASSIGNMENT.md) · **Next:** [2.4 Diagnose and fix](../2.4-diagnose/ASSIGNMENT.md)

## Goal

One scoring function per component, and a working `run_eval` that applies them to the provided cases.

## What you are given

| File | What it does |
|---|---|
| `backend/evals/run_eval.py` | Starts your app in-process (with its startup code) and sends each case to `POST /api/v1/discover`: no network, but the same route, validation and dependencies as the UI. Collects each case's spans, runs your graders, and prints a score per metric. Do not edit it. |
| `backend/evals/dev_cases.jsonl` | 24 cases with their expected answers |
| `backend/evals/graders.py` | `Grade`, the helper `tool_inputs()`, the example `grade_status`, and the `GRADERS` list. **You write your graders here.** |

### A case

Shortened: the real DEV-10 lists 27 excluded and 2 uncertain sources.

```json
{"case_id": "DEV-10", "query": "أبحث عن نشاط في تصوير القصص بالهاتف برسوم لا تتجاوز 150 ريال",
 "profile": {"interests": [], "skills": null}, "constraints": {"max_budget_sar": 150},
 "evaluation_time": "2026-10-18T12:00:00+03:00",
 "expected": {"status": "ok",
              "acceptable_source_ids": ["SRC-0557", "SRC-0639"],
              "excluded_source_ids": {"SRC-0701": "over_budget", "SRC-0133": "status_cancelled"},
              "uncertain_source_ids": {"SRC-0955": "status_unknown"}}}
```

**Answers are source IDs**, not opportunity IDs: every team groups announcements and numbers opportunities its own way.

| List | Meaning | Reasons |
|---|---|---|
| `acceptable_source_ids` | On topic, available, and meets every constraint | — |
| `excluded_source_ids` | On topic, but must **not** be recommended | `deadline_passed`, `activity_ended`, `status_cancelled`, `status_closed`, `not_yet_open`, `location_not_preferred`, `delivery_not_requested`, `type_not_requested`, `over_budget`, `fee_unknown`, `outside_dates` |
| `uncertain_source_ids` | On topic and meets the constraints, but availability is genuinely unknown at the evaluation time | `deadline_today_no_time`, `status_unknown`, `status_conflict`, `deadline_conflict`, `location_conflict`, `fee_conflict`, `type_conflict`, `date_conflict` |

**How one recommendation is judged**, by its `source_ids`:

| Its sources | Verdict |
|---|---|
| Any source is excluded | **Wrong**, even if an acceptable source is attached to the same recommendation |
| At least one acceptable source, none excluded | **Right** |
| Every source is uncertain | **Not graded**: it counts neither for nor against you |
| In none of the lists (off topic) | **Wrong** |

**Every `no_match` case has an empty `uncertain_source_ids`**, so `no_match` is the only correct status there. In `ok` cases an acceptable source always exists, so `ok` is the only correct status.
- `run_eval` sets `FURSAH_EVALUATION_TIME` to the cases' `evaluation_time`, so your route receives it as `deps.evaluation_time`. Your deadline logic must use `deps.evaluation_time`, not the computer's clock. If your `backend/.env` is blank or holds a different instant, `run_eval` warns you: set the [classroom instant](../README.md#before-you-start) so the UI agrees with the evaluation.
- `run_eval` uses `app.main:app`, the same app `uvicorn` starts. If you moved your app, pass it: `--app yourmodule:app`. Startup and cleanup code in your app's lifespan runs once around the whole evaluation.

### A grader

```python
def grade_status(case, response, spans):
    expected = case["expected"]["status"]
    got = response.status.value
    return Grade("e2e.status", float(got == expected), f"expected {expected!r}, got {got!r}")
```

| Argument | What it is |
|---|---|
| `case` | One line of `dev_cases.jsonl` |
| `response` | The `DiscoveryResponse` your workflow returned |
| `spans` | `{span name: attributes}` for this case's trace, from your 2.2 spans and your framework's. `spans.all(name)` returns every span with that name. `tool_inputs(spans, "search_opportunities")` returns the arguments of every call to that tool. |

## What you do

1. **Read the example `grade_status`** in `backend/evals/graders.py`.
2. **For each deterministic or verifiable component in your 2.1 table, write one grader.** Example components: the agent's tool call, search, the final recommendations.
3. **Use what you recorded in 2.2:** `spans["repo.search"]["candidate_ids"]`. If a grader needs a value you did not record, go back and add it to your span.
4. **Put the expected and the actual value in `reason`:** `f"expected {expected}, got {got}"`. That line is printed under every failing case, and it is what you read in 2.4 instead of opening the data.
5. **Return `None` when the metric does not apply** to the case: for example, a search-recall grader on a `no_match` case has no acceptable IDs to find.
6. **Add each grader to `GRADERS` and run:**
   ```bash
   cd fursah-starter/backend
   python -m evals.run_eval --label baseline
   ```
   Start Phoenix first (`phoenix serve`) so each failing case has a trace you can open; without it, grading still runs but no traces are saved. To rerun only some cases: `--only DEV-03,DEV-07`.

Subjective components (the wording of `reason`, for example) need an LLM judge. Do not write one today; your 2.1 table already names what it would need to see.

## Reading the output

```
DEV-10  FAIL   trace_id=f359d75bba497ac7a147b9c903080808
        e2e.recommendations          expected any of ['SRC-0557', 'SRC-0639'], got [['SRC-0557'], ['SRC-0663']]; excluded: ['SRC-0663:deadline_passed']

metric                          score   n  failing cases
e2e.status                       0.96  24  DEV-19

24 cases: 22 PASS, 2 FAIL
```

`n` is the number of cases the metric applied to. Results are also saved in `backend/evals/results/`.

Each case ends in one of seven outcomes:

| Outcome | Meaning | What to do |
|---|---|---|
| `PASS` | Every applicable grade is 1 | — |
| `FAIL` | At least one grade is below 1 | Read the `expected … got …` line, then the trace (2.4) |
| `NOT EVALUATED` | Every grader returned `None`: nothing was measured | Check that your graders apply to this kind of case |
| `PROTOCOL ERROR` | The reply breaks the API contract the UI relies on: a non-2xx HTTP code with a successful body (the UI shows an error instead), a `request_id` that is not the request's, or a body that is not a valid `DiscoveryResponse` | Fix the reply: it is graded as the error the UI would show. Return `request.request_id` unchanged |
| `TIMEOUT` | No reply within 100 seconds, the time after which the UI gives up (your workflow's own budget is 90 s). The case is graded as the error the UI would show. If your code blocks the whole process (a synchronous call that never returns), `run_eval` saves this case and stops the run | Find the slow step in the trace. To try a shorter limit: `--case-timeout 30` |
| `ERROR` | Your app crashed or sent something that is not JSON; the `execution error:` line shows the cause | Fix the crash first: its other grades are not meaningful |
| `GRADER ERROR` | A grader raised an exception, or returned a score that is not a number from 0 to 1 | Fix the grader: its result is not counted, and `run_eval` exits with an error |

A wrong `--only` case ID stops the run with the list of valid IDs.

## What the shared cases cover, and what they do not

The 24 shared cases test search, constraints, availability and the final answer, for one minimal profile: 18 `ok` and 6 `no_match`. **A perfect score on them does not certify your application.** They deliberately leave out behaviour whose right answer depends on your design:

| Behaviour | Why it is not a shared case | Team-defined check |
|---|---|---|
| Asking for missing information (`needs_info`) | When to ask is your design decision | Write 1–2 cases following **your documented rule** in `backend/evals/team_cases.jsonl` (same format, same `evaluation_time`) and run `python -m evals.run_eval --cases evals/team_cases.jsonl`. Your graders must return `None` where a metric does not apply to `needs_info`. |
| Controlled failure | A failure has to be provoked | Start the backend with a wrong `FURSAH_MODEL_API_KEY` and search once: expect `status: "error"`, a safe message, no stack trace, no key in the response. |
| Eligibility at a boundary | It depends on the profile | A team case whose profile conflicts with a mandatory requirement of an opportunity; expect the eligibility your rule says (`uncertain`, or not recommended). |
| Details consistency | It is a separate endpoint | For one recommended opportunity, `GET /api/v1/opportunities/{id}`: title, deadline, fee and `source_ids` must match the recommendation. |

**Do at least one of these** within the 2.3 time.

## Hand in

`backend/evals/graders.py` with one grader per component, the baseline table printed by `run_eval`, and **one team-defined check** from the table above with its result. Label which results come from the shared cases and which test your own design. This is your starting point for 2.4.
