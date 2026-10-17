# Copyright 2026 Harness² Authors & Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Prompt templates for the proposer (Appendix C.2) + the God-View surface spec.

Four distinct prompts drive Harness²:
  - PROPOSE_PROMPT: single-candidate mutation (sequential refinement or one
    branch of parallel exploration).
  - AGGREGATE_PROMPT: Section 3.4 multi-parent crossover (synthesize a child
    combining complementary strengths of the top-m candidates).
  - EXPERIENCE_PROMPT: Section 3.5 reflection step on adoption (write
    EXPERIENCE.md + update the domain INDEX.md entry).
  - PROJECTED_PREAMBLE: injected at the top of systemprompt.md when running in
    `projected` install mode so the target agent actively leverages the full
    harness tree from disk even on substrates lacking native subagent/hook wires.
"""

from __future__ import annotations

from godview.harness.core.spec import SurfaceSpec

_SURFACE_TEXTS: dict[str, str] = {
    "godview": """\
Target substrate: **God-View Court Agent (Vertex AI Gemini 4 Argon / 3.8 Flash)**
Working directory layout inside `context/harness/`:
  c1 Role & Persona          -> `systemprompt.md`
  c2 Guardrails & Policy     -> `guardrails.md`
  c3 Long-Term Memory        -> `LongTermMEMORY.md`
  c4 Tool Guidance           -> `tool_descriptions/tool_guidance.md`
  c5 Sub-Agents (on-demand)  -> `sub_agents/<name>/AGENT.md`
  c6 Skills (on-demand)      -> `skills/<slug>/SKILL.md`
  c7 Executable Scripts      -> `scripts/<name>.py`
  c8 Lifecycle Hooks         -> `hooks.json`

How God-View loads this tree at rollout time:
  - `systemprompt.md`, `guardrails.md`, `LongTermMEMORY.md`, and
    `tool_descriptions/tool_guidance.md` are concatenated into the Court Agent
    Always-on system instruction. Their combined length MUST stay <= 12,000 tokens.
  - Each `skills/<slug>/SKILL.md` MUST begin with YAML frontmatter containing
    `name`, `description`, and a non-empty `autotrigger:` rule (e.g.
    `autotrigger: when telemetry or logs indicate schema/offset/drift anomalies`).
  - Each `sub_agents/<name>/AGENT.md` begins with YAML frontmatter (`name`,
    `description`, `mode: subagent`) followed by the specialist prompt.
  - `scripts/<name>.py` are deterministic diagnostic and invariant helpers invoked
    via `python3 scripts/<name>.py` from skills or hooks. Every script MUST pass
    the AST sandbox validator (no shell escapes, no subprocesses, no eval/exec).
  - `hooks.json` binds `PreToolUse`, `PostToolUse`, or `Stop` events to commands:
    `{"hooks": {"PostToolUse": [{"matcher": "sense.*", "commands": ["python3 scripts/check.py"]}]}}`.""",
    "opencode": """\
Target substrate: **OpenCode CLI**
Working directory layout inside `context/harness/`:
  c1 Role & Persona          -> `systemprompt.md`
  c2 Guardrails & Policy     -> `guardrails.md`
  c3 Long-Term Memory        -> `LongTermMEMORY.md`
  c4 Tool Guidance           -> `tool_descriptions/tool_guidance.md`
  c5 Sub-Agents (on-demand)  -> `sub_agents/<name>.md`
  c6 Skills (on-demand)      -> `skills/<name>/SKILL.md`
  c7 Executable Scripts      -> `scripts/<name>.py`
  c8 Lifecycle Hooks         -> `hooks.json`""",
    "codex": """\
Target substrate: **Codex CLI**
Working directory layout inside `context/harness/`:
  c1 Role & Persona          -> `AGENTS.md`
  c2 Guardrails & Policy     -> `guardrails.md`
  c3 Long-Term Memory        -> `MEMORY.md`
  c4 Tool Guidance           -> `tool_guidance.md`
  c5 Sub-Agents (on-demand)  -> `agents/<name>.md`
  c6 Skills (on-demand)      -> `skills/<name>/SKILL.md`
  c7 Executable Scripts      -> `scripts/<name>.py`
  c8 Lifecycle Hooks         -> `hooks.json`""",
}


PROJECTED_PREAMBLE = """\
<!-- HARNESS2 PROJECTED SURFACE -->
You are operating inside a structured **Harness² capability tree** mounted at
`.harness2/` in your working directory. Before acting on any non-trivial step:
1. Check `.harness2/skills/` — if a skill's `autotrigger:` matches your current
   sub-goal, read its `SKILL.md` and follow its workflow.
2. Check `.harness2/scripts/` — run deterministic verification or helper scripts
   via `python3 .harness2/scripts/<script>.py` instead of re-deriving logic by hand.
3. Check `.harness2/sub_agents/` (or `.harness2/agents/`) — adopt the specialist
   checklist when decomposing sub-problems.
4. Before finishing, run every command listed under `"Stop"` in `.harness2/hooks.json`
   and fix any reported issues before declaring completion.
<!-- END HARNESS2 PROJECTED SURFACE -->
"""


def propose(
    surface: SurfaceSpec,
    *,
    round_idx: int,
    candidate_tag: str,
    branch_hint: str = "",
) -> str:
    surface_doc = _SURFACE_TEXTS.get(surface.name, _SURFACE_TEXTS["godview"])
    hint_block = f"\nBranch diversity directive: {branch_hint}\n" if branch_hint else ""
    return f"""\
You are the **Harness² Meta-Agent Proposer** (Round {round_idx}, candidate `{candidate_tag}`).
Your job is to engineer the **agent harness tree** inside `./context/harness/` so that the
target coding/research agent succeeds on the task in `./context/task.md`.

{surface_doc}
{hint_block}
### Context Workspace Protocol
Read these files in `./context/` before editing `./context/harness/`:
- `./context/task.md` — the task description the target agent will be given.
- `./context/library_index.md` (if present) — reusable domain harnesses from the Harness Library.
- `./context/reference_experience.md` (if present) — prior adaptation lessons from related tasks.
- `./context/prior_candidates/<tag>/meta.json` and `summary.txt` — scores and failure traces from
  earlier rounds. Inspect `logs/` when you need deeper stack traces or tool transcripts.

### Principles of Effective Harness Engineering (Section 3.3)
1. **Keep Always-on Context lean (<= {surface.always_on_max} tokens combined across c1..c4).**
   Put crisp operating procedures, failure modes to avoid, and pointers to skills/scripts in c1..c4;
   push detailed playbooks into `skills/<name>/SKILL.md` (c6) and deterministic checks into
   `scripts/<name>.py` (c7).
2. **Turn recurring reasoning errors into executable scripts + hooks.**
   Whenever a prior candidate failed because it forgot to verify an invariant, edge case, output
   format, or test assertion, write a Python script under `scripts/` that checks it deterministically
   and wire it into `hooks.json` (`Stop` or `PostToolUse`) and/or reference it from `SKILL.md`.
3. **Every `SKILL.md` MUST include a non-empty `autotrigger:` line** in its frontmatter or header
   AND begin with `---` or `#`.
4. **Every `python3 scripts/foo.py` referenced anywhere MUST exist in `context/harness/scripts/foo.py`**
   and compile without syntax errors.

### Required Output
Modify the files inside `./context/harness/` directly using your file-editing tools.
When finished, end your final response with a concise `<notes>...</notes>` block explaining:
- Which failure mode or task bottleneck you targeted, and
- Which files in c1..c8 you added or modified.
"""


def aggregate(
    surface: SurfaceSpec, *, round_idx: int, candidate_tag: str, parent_tags: list[str]
) -> str:
    surface_doc = _SURFACE_TEXTS.get(surface.name, _SURFACE_TEXTS["godview"])
    parents_str = ", ".join(parent_tags)
    return f"""\
You are the **Harness² Crossover Aggregator** (Round {round_idx}, candidate `{candidate_tag}`).
Inspect the top-performing parent candidates ({parents_str}) under `./context/prior_candidates/`
and synthesize a **unified child harness** inside `./context/harness/` that combines their
complementary strengths while eliminating their remaining failure modes.

{surface_doc}

Steps:
1. Compare `meta.json`, `summary.txt`, and `harness/` across {parents_str}.
2. Merge non-conflicting skills (`c6`), verification scripts (`c7`), and hooks (`c8`) that proved
   useful, and reconcile `c1..c4` so the combined Always-on Context stays <= {surface.always_on_max} tokens.
3. Verify every referenced `python3 scripts/<name>.py` exists and every `SKILL.md` has `autotrigger:`.
4. End your response with `<notes>...</notes>` summarizing what you inherited from each parent.
"""


def experience(*, task_id: str, domain: str, best_tag: str) -> str:
    return f"""\
You are the **Harness² Library Curator** (Section 3.5 Reflection).
Candidate `{best_tag}` solved or achieved the best score on task `{task_id}` (domain `{domain}`).
Inspect `./context/task.md`, `./context/prior_candidates/`, and `./context/harness/` and write two files:

1. `./context/EXPERIENCE.md` — structured post-mortem with three sections:
   - `## Task Fingerprint`: problem archetype, domain invariants, and failure traps.
   - `## Evolution Trajectory`: how `h0 -> ... -> {best_tag}` changed across rounds and why.
   - `## Reusable Components`: which skills, scripts, and hooks in `{best_tag}` generalize to future
     tasks in domain `{domain}` and when to reuse them.

2. `./context/INDEX_ENTRY.md` — a single markdown bullet (2-4 lines) indexing `{task_id}` for
   `{domain}/INDEX.md`, listing its key skills/scripts and trigger keywords.
"""


def compose_round(
    surface: SurfaceSpec,
    *,
    round_idx: int,
    candidate_tag: str,
    prior_tags: list[str],
    branch_hint: str = "",
) -> str:
    """Unified round prompt (Section 3.3 / 3.4)."""
    surface_doc = _SURFACE_TEXTS.get(surface.name, _SURFACE_TEXTS["godview"])
    priors_str = ", ".join(prior_tags) if prior_tags else "none"
    hint_block = f"\nBranch diversity directive: {branch_hint}\n" if branch_hint else ""
    return f"""\
You are the **Harness² Meta-Agent Proposer** (Round {round_idx}, candidate `{candidate_tag}`).
Your job is to engineer the **agent harness tree** inside `./context/harness/` so that the
target coding/research agent succeeds on the task in `./context/task.md`.

{surface_doc}
{hint_block}
### Prior Candidates Available Under `./context/prior_candidates/`
Available tags: {priors_str}
Each directory `./context/prior_candidates/<tag>/` contains:
- `meta.json` — score, pass/fail, token usage, parent_tags, and proposer notes.
- `summary.txt` — condensed rollout trajectory and failure excerpt.
- `logs/` — full stdout/stderr and agent trace when you need deeper inspection.
- `harness/` — that candidate's complete harness tree.

Note: `./context/harness/` is pre-seeded with the highest-scoring prior candidate's tree.

### Step 1 — Choose Your Evolution Operator
Inspect the prior candidates (`meta.json`, `summary.txt`, and `harness/`) alongside
`./context/task.md` (plus `library_index.md` / `reference_experience.md` if present).
Decide whether this round is best served by:
- **Refinement (`mode: refine`)** — focus on the single strongest prior candidate, diagnose
  its specific failure mode or bottleneck, and perform targeted mutations on `./context/harness/`.
- **Crossover / Aggregation (`mode: crossover`)** — when two or more prior candidates exhibit
  complementary strengths (e.g., different useful scripts, skills, or guardrails that each
  solved part of the problem), merge their best components from `./context/prior_candidates/*/harness/`
  into `./context/harness/` and reconcile any conflicts.

### Step 2 — Apply Harness Engineering Principles (Section 3.3)
1. **Keep Always-on Context lean (<= {surface.always_on_max} tokens combined across c1..c4).**
   Put crisp operating procedures, failure modes to avoid, and pointers to skills/scripts in c1..c4;
   push detailed playbooks into `skills/<name>/SKILL.md` (c6) and deterministic checks into
   `scripts/<name>.py` (c7).
2. **Turn recurring reasoning errors into executable scripts + hooks.**
   Whenever a prior candidate failed because it forgot to verify an invariant, edge case, output
   format, or test assertion, write a Python script under `scripts/` that checks it deterministically
   and wire it into `hooks.json` (`Stop` or `PostToolUse`) and/or reference it from `SKILL.md`.
3. **Every `SKILL.md` MUST include a non-empty `autotrigger:` line** in its frontmatter or header
   AND begin with `---` or `#`.
4. **Every `python3 scripts/foo.py` referenced anywhere MUST exist in `context/harness/scripts/foo.py`**
   and compile without syntax errors.

### Required Output
Modify the files inside `./context/harness/` directly using your file-editing tools.
When finished, end your final response with a concise `<notes>...</notes>` block stating:
- `mode: refine` or `mode: crossover` and which parent tag(s) (`parents: t1, t2`) you drew from,
- Which failure mode or task bottleneck you targeted, and
- Which files in c1..c8 you added or modified.
"""
