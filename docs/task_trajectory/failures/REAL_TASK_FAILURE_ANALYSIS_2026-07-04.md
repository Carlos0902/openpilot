# REAL_TASK_FAILURE_ANALYSIS_2026-07-04.md

## Purpose

This document summarizes the latest **real-task failure phenomena** observed
after the task-trajectory evidence layer, read-only analysis guard, and first
command-path hardening pass were added.

The goal is not to rush into repair. The goal is to:

- summarize what actually failed;
- separate symptoms from likely root causes;
- preserve the evidence trail for later validation tasks.

---

## 1. Task and run evidence

Executed task:

> 请梳理从 CLI 入口到主执行运行时的核心链路，并指出关键模块之间的关系。

Execution command:

```bash
cd /Users/abab/Documents/openpilot/Code
PYTHONPATH=src python -m ui.cli run --once "请梳理从 CLI 入口到主执行运行时的核心链路，并指出关键模块之间的关系。"
```

Observed run:

- run id: `5539f44c73914d56b66e2588f92c982c`
- trajectory dir:
  `/Users/abab/Documents/openpilot/Code/data/runtime_diagnostics/task_trajectory/5539f44c73914d56b66e2588f92c982c/`

Key files:

- `run.json`
- `events.jsonl`
- `artifacts.jsonl`
- `summary.json`

Summary highlights:

- final status: `failed`
- completion reason: `runtime session failed`
- event count: `32`
- tool called count: `6`
- tool succeeded count: `2`
- tool failed count: `4`

This was **not** a timeout. It was a normal failed run with usable evidence.

---

## 2. Failure phenomena

## 2.1 Early evidence gathering can succeed

The task successfully completed early evidence steps such as:

- project structure reading;
- sketch-based evidence collection.

This means:

- the basic runtime path is alive;
- the evidence layer can persist a full run;
- failure is not at process startup.

## 2.2 The system still hallucinates concrete file paths

Observed failed path guesses included:

- `setup.py`
- `cli.py or ./main.py (to be determined after listing)`
- `/openpilot/selfdrive/cli.py`
- `/openpilot`

These are not grounded in current project evidence.

## 2.3 The failure shifts from “execution layer only” to “planning + synthesis”

The run did not fail only because a command could not execute.

It also failed because later planning returned:

- empty `decision_needs`; or
- non-routable planning output.

Terminal-level final failure:

> Tool planning requires decomposition after empty decision_needs plan

This happened on the synthesis/documentation stage after earlier evidence had
already been collected.

## 2.4 The system can gather evidence but cannot reliably cash it out into a final deliverable

This is an important distinction:

- the observation phase is partially working;
- the “turn observations into a clean answer artifact” phase is still unstable.

---

## 3. Symptom clusters

The current symptoms cluster into three groups.

### Cluster A: path hallucination after evidence exists

The model still invents:

- old repository layouts;
- generic entry filenames;
- external or legacy root paths.

### Cluster B: weak evidence-to-plan transition

After reading real structure evidence, later steps still fall back to:

- guessed files;
- guessed module names;
- guessed deliverable paths.

### Cluster C: synthesis-stage planning collapse

When the task changes from:

- “gather evidence”

to:

- “write the explanation / produce the diagram”

the planner can fail to emit a valid next-step `decision_needs` plan.

---

## 4. Candidate root problems

Below are **suspected root problems**, not final conclusions.

## Root problem 1: evidence is collected, but not enforced as the only admissible grounding source

### Why this is suspected

The run already had:

- top-level project structure evidence;
- `sketch.json` evidence;
- earlier successful reads.

But later steps still guessed:

- `/openpilot`
- `/openpilot/selfdrive/cli.py`
- `setup.py`
- `cli.py or ./main.py`

### What this suggests

The current system encourages evidence, but later planning stages are still
allowed to drift away from that evidence.

### Stronger formulation

The likely root issue is not only “path resolution is weak”.

It is:

> **the planning stack does not yet enforce evidence-anchored file selection
> strongly enough across multi-step synthesis.**

---

## Root problem 2: the planner is stronger at decomposition / evidence gathering than at synthesis / deliverable formation

### Why this is suspected

The first two subgoals completed.

The later subgoals failed when they became:

- summarize the chain;
- create the diagram / markdown deliverable.

### What this suggests

The system is better at:

- reading;
- listing;
- locating structure;

than at:

- using gathered evidence to generate a final structured artifact plan.

### Stronger formulation

The likely root issue is:

> **tool planning for read-only synthesis tasks is under-specified after the
> evidence phase.**

The planner can inspect, but it cannot always transition from inspection to
answer assembly.

---

## Root problem 3: decomposition fallback is still too brittle for empty-plan recovery

### Why this is suspected

The failure mode was not random tool crash alone.

It ended in:

> empty or unroutable `decision_needs` → decomposition fallback → failure

### What this suggests

The system has a recovery story for:

- empty plan;
- non-routable plan;

but that story is still too weak when the task is:

- analytical;
- read-only;
- multi-step;
- final-output-oriented.

### Stronger formulation

The likely root issue is:

> **the current empty-plan recovery policy is not specialized enough for
> analysis/synthesis tasks.**

---

## Root problem 4: path hardening has improved command execution, but not the higher-level file-target selection policy

### Why this is suspected

Recent fixes already improved:

- `command_cwd`
- `command_executable_path`
- `command_data_path`
- `command_redirection_path`

This reduced one class of command-path hallucination.

But the current real-task failure still shows hallucinated file targets during
planning and synthesis.

### What this suggests

The unresolved problem has moved upward:

- from command execution path governance
- to task planning / target selection governance

### Stronger formulation

The likely root issue is:

> **path governance below the command layer is improving, but evidence-backed
> target selection above the command layer is still too permissive.**

---

## 5. Current best interpretation

At this point, the best working interpretation is:

1. the evidence layer is now strong enough to capture the run;
2. command-path governance is materially better than before;
3. the main remaining instability is now in:
   - evidence-to-plan transition;
   - final synthesis planning;
   - evidence-anchored target selection.

So the bottleneck has shifted from:

- “can the runtime execute safely?”

toward:

- “can the planner stay grounded in evidence all the way to the final answer?”

---

## 6. Suggested next validation directions

Do not repair immediately. First validate the suspicions.

### Validation direction A

Construct read-only analytical tasks that require:

- evidence gathering;
- then synthesis only;
- without any file mutation.

Goal:

Check whether empty-plan collapse consistently appears at the synthesis stage.

### Validation direction B

Construct tasks with known project roots and known entry files.

Goal:

Check whether later subtasks still invent:

- old repo roots;
- generic entry files;
- legacy path layouts.

### Validation direction C

Inspect LLM planning artifacts from this run.

Goal:

Determine whether the model:

- ignored available evidence;
- or was never given enough structured synthesis affordances.

---

## 7. Status judgment

This run is valuable because it shows:

- the new trajectory evidence layer is operational;
- the task now fails with a much clearer internal shape;
- the next likely system problem is no longer “raw execution blindness”;
- it is now “evidence-grounded synthesis reliability”.

That is progress, even though the task still failed.
