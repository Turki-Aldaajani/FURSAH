# 2.4 Run, diagnose, fix, rerun · 30 minutes

**Day 4:** [Overview](../README.md) · **Previous:** [2.3 Graders](../2.3-graders/ASSIGNMENT.md)

## Goal

See a score move because you found and fixed a real problem.

## What you do

1. **Run the evaluation on all cases and record the baseline score per metric.**
   ```bash
   python -m evals.run_eval --label baseline
   ```
2. **For each failing case:**
   - Read the `expected … got …` line printed under it.
   - Copy its `trace_id` → Phoenix → **Traces** → filter: `trace_id == "<trace_id>"`.
   - Walk the tree **from the result back to the cause**, one span at a time, and write: what failed, what the trace shows, your hypothesis for the cause.
3. **Fix the agent:** the prompt, a tool description, routing logic, or argument handling.
4. **Rerun and compare.** Which failures were fixed? Did any case that passed before now fail?
   ```bash
   python -m evals.run_eval --label after-fix-1
   ```
   To check one case quickly while fixing: `--only DEV-10`. Always finish with a full run, so a fix that breaks another case is caught.

Start from the metric (which score dropped?), then the case (which input?), then the trace (why?).

## Example from the maintenance assistant

**1. What failed** (from `run_eval`):
```
HAZ-03  FAIL   trace_id=b02db45cf7f6d12f60c7b66afcc626d5
        extraction.safety_recall   expected ['exposed_wires'], got []
        e2e.hazard_recall          expected status 'critical', got 'success'
```

**2. What the trace shows, from the result back to the cause:**
```
create_ticket      → priority: low
evaluate_policy    ← safety_signals: []
extract_issue      → safety_signals: []
llm.generate       → the prompt allows exposed_wires and the input says "أسلاك مكشوفة", but the raw reply has none
```

**3. Hypothesis:** the priority rules are right and the prompt is right; the code that produces the reply never detects the signal.

**4. Fix:** detect `exposed_wires` when the report mentions exposed or cut wires.

**5. Result:**

| Metric | Before | After |
|---|---|---|
| `extraction.safety_recall` | 0.75 | **1.00** |
| `e2e.hazard_recall` | 0.75 | **1.00** |

New failures: none. A score of 1.00 means "passes the cases we have", not "detects every hazard": the next step would be adding new wordings as cases.

## Hand in

**Before/after table**

| Metric | Before | After | What changed |
|---|---|---|---|
|  |  |  |  |

**Diagnosis log**: one row per defect, not per metric (one defect often moves several metrics)

| Case | What failed | What the trace shows | Hypothesis | Fix | Result |
|---|---|---|---|---|---|
|  |  |  |  |  |  |
