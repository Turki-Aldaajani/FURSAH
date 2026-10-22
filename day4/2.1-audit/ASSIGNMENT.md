# 2.1 Audit the agent for evaluability · 10 minutes

**Day 4:** [Overview](../README.md) · **Next:** [2.2 Instrument the agent](../2.2-instrument/ASSIGNMENT.md)

## Goal

Before writing any evaluation code, decide what in your agent can actually be measured, and how.

## Tests or evaluation?

| | Traditional test | Evaluation |
|---|---|---|
| Use it when | The correct answer is fixed by a rule for every input | The correct answer **cannot be fixed in advance for every input**: the component is AI-based, **or** its input is open-ended human language |
| Shape | Fixed input → deterministic code → expected result (pass/fail) | Many realistic inputs → the system → a score from a grader |

Traditional tests can be used as **graders inside an evaluation**. Example: checking that a returned ID equals the correct ID is a traditional test; running that check over 200 real requests and reporting the match rate is an evaluation.

## What you do

1. **List every component of your agent:** the planner or router, each tool, memory or storage, the final answer step. Start from your Day 1 component map, then check it against your code.
2. **For each component, write down what it outputs.** Use the real function names from your code.
3. **Label each output as one kind of check:**
   - **Deterministic:** there is one correct answer that code can check. *Example: the tool was called with the right arguments.*
   - **Verifiable:** there is a fact you can check, but it needs data or state. *Example: the retrieved record was relevant; the record was actually updated.*
   - **Subjective:** reasonable people could disagree. *Example: the answer was helpful; the tone was right.* This is where an **LLM judge** would be used. Today you will write code graders only, but note where a judge would be needed and what evidence it would need to see.
4. **Mark anything you cannot observe today because nothing logs it.** Before writing "not logged", check every place data is kept: your storage, the logs, the existing JSONL trace (`FURSAH_TRACE_JSONL_PATH`), and any checkpoint store.

Before labelling something **subjective**, ask whether it could be made deterministic. Instructors will challenge these labels.

## Hand in

Fill in [`audit_template.md`](audit_template.md): one row per component.

| Column | What to write |
|---|---|
| Component | The function or module name, e.g. `repository.search(criteria)` |
| Output | What it returns, with field names |
| Kind of check | Deterministic, Verifiable or Subjective, plus one line on how you would check it |
| Missing signal | What you would need to see but cannot today |

Keep this table. In 2.2 your custom spans come from the **Missing signal** column, and in 2.3 each deterministic or verifiable row becomes one grader.

## Example: the maintenance assistant from Day 3

This is the audit of the instructor's example, not of FURSAH. Use it for the format, not the answers.

| Component | Output | Kind of check | Missing signal |
|---|---|---|---|
| `extract_issue` | `IssueExtraction`: `asset_type`, `symptoms`, `safety_signals`, `missing_fields` | **Deterministic**: compare each field to a labelled answer | The raw model reply is not logged; a parse failure silently becomes an empty result |
| `lookup_asset` → `find_assets(asset_type, location)` | `selected_asset_id` or `candidate_assets` | **Verifiable**: needs the assets table and the correct answer | Which branch produced the result: a room match, a fallback to the first device, or a generic device |
| `retrieve_sop` → `retrieve_procedures(asset_type, symptoms, safety_signals)` | `retrieved_procedures`: `ProcedureChunk(source, content, score)` | **Verifiable**: relevance needs the manuals and labels | The trace shows only how many sections came back, not which ones or in what order |
| `compose_recommendation(procedures, context)` | `recommended_action` | **Subjective**: is it clear and helpful? (LLM judge) | Whether the technician followed it, and whether it helped |
| `POST /api/maintenance/requests` | `status`, `analysis.priority`, `ticket.status` | **Deterministic**: a hazard must end as `critical` and `escalated` | The correct priority: nobody confirms afterwards whether it really was a hazard |
