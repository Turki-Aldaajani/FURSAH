# Day 4: Evaluate your FURSAH agent

**Overview:** [Main guide](../README.md)

Today you measure the agent you built on Days 1–3. You do not rebuild it. Each part produces something the next part uses:

| Part | Time | You produce | Used in |
|---|---|---|---|
| [2.1 Audit the agent for evaluability](2.1-audit/ASSIGNMENT.md) | 10 min | An audit table: what each component outputs, how it can be checked, what is not logged | 2.2, 2.3 |
| [2.2 Instrument the agent](2.2-instrument/ASSIGNMENT.md) | 25 min | One Phoenix trace per search: your framework's spans, if any, plus your own spans | 2.3, 2.4 |
| [2.3 Choose metrics and write graders](2.3-graders/ASSIGNMENT.md) | 30 min | One grader per component and a baseline score | 2.4 |
| [2.4 Run, diagnose, fix, rerun](2.4-diagnose/ASSIGNMENT.md) | 30 min | A before/after score table and a diagnosis log | — |

## Before you start

- Your Days 1–3 code runs locally: `POST /api/v1/discover` returns a real response, not `FURSAH_NOT_IMPLEMENTED`.
- You have your Day 1 component map at hand.
- Python 3.11+ and your backend virtual environment are active.
- **Set the classroom instant** in `backend/.env`, then restart the backend:
  ```dotenv
  FURSAH_EVALUATION_TIME=2026-10-18T12:00:00+03:00
  ```
  Every Day 4 case was labelled at this instant, and the hidden test set is graded at it. With the variable blank your app uses the real clock, so the UI would disagree with the evaluation: deadlines, statuses and even which announcements are already published depend on "now". Outside the exercise, leave it blank.

## What was added to the starter for today

| File | Purpose |
|---|---|
| `day4/` | These instructions and the audit template |
| `backend/app/infrastructure/observability.py` | Phoenix tracing: `setup_observability()` and `start_span()` |
| `backend/requirements-observability.txt` | Phoenix and the tracing packages |
| `backend/app/infrastructure/config.py`, `backend/.env.example` | `FURSAH_PHOENIX_*` settings |
| `backend/evals/run_eval.py` | Sends the cases to your app in-process and prints a score per metric |
| `backend/evals/dev_cases.jsonl` | 24 cases with expected answers |
| `backend/evals/graders.py` | Where you write your graders (2.3) |

None of these change how your application behaves. Tracing stays off until you set `FURSAH_PHOENIX_ENABLED=true`.
