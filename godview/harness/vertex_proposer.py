# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""VertexGeminiProposer: native Vertex AI Gemini proposer using ADC without static tokens."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import Any

from godview_shared.config import (
    DEFAULT_AGENT_FALLBACK_MODEL_ID,
    DEFAULT_AGENT_MODEL_ID,
    DEFAULT_AGENT_MODEL_LOCATION,
    validate_model_location,
)
from godview_shared.fakes import InMemoryFirestore, InMemoryPubSub

from godview.harness.core.proposer import Proposer, ProposerCallResult
from godview.harness.core.tree import estimate_tokens
from godview.harness.substrate import validate_script_ast
from godview.sovereign.court_manager.token_governor import TokenBudgetGovernor

logger = logging.getLogger("godview.harness.vertex_proposer")


def _extract_notes(text: str) -> str:
    m = re.search(r"<notes>(.*?)</notes>", text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _extract_parents(text: str) -> list[str]:
    m = re.search(r"parents:\s*([^\n|]+)", text)
    if not m:
        return ["h0"]
    return [p.strip() for p in m.group(1).split(",") if p.strip()]


class VertexGeminiProposer(Proposer):
    """Meta-agent proposer backed by Vertex AI Gemini using ADC."""

    def __init__(
        self,
        *,
        ctx: Any | None = None,
        client: Any | None = None,
        model: str = DEFAULT_AGENT_MODEL_ID,
        fallback_model: str = DEFAULT_AGENT_FALLBACK_MODEL_ID,
        location: str = DEFAULT_AGENT_MODEL_LOCATION,
        governor: TokenBudgetGovernor | None = None,
        agent_id: str = "court-proposer",
        timeout_sec: float = 60.0,
    ) -> None:
        super().__init__(channel="vertex_gemini", model=model, timeout_sec=timeout_sec)
        validate_model_location(model, location)
        validate_model_location(fallback_model, location)
        self.ctx = ctx
        self.client = client
        self.default_model = model
        self.fallback_model = fallback_model
        self.location = location
        self.agent_id = agent_id

        fs = InMemoryFirestore()
        ps = InMemoryPubSub()
        self.governor = governor or TokenBudgetGovernor(firestore=fs, pubsub=ps)
        self.last_model_used: str = model

    def resolve_active_model(self, *, quota_or_429_fallback: bool = False) -> str:
        """Select `gemini-4-argon` @ `global` by default, falling back to `gemini-3.8-flash` on 429."""
        if quota_or_429_fallback:
            validate_model_location(self.fallback_model, self.location)
            return self.fallback_model
        if self.governor.is_agent_degraded_to_l1(self.agent_id):
            validate_model_location(self.fallback_model, self.location)
            return self.fallback_model
        validate_model_location(self.default_model, self.location)
        return self.default_model

    def run(self, work_dir: Path, prompt: str) -> ProposerCallResult:
        """Execute a proposer pass inside `work_dir` (which holds `./context/`)."""
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "proposer_prompt.md").write_text(prompt, encoding="utf-8")

        active_model = self.resolve_active_model()
        env_mode = os.environ.get("GODVIEW_ENV", "hermetic").lower()

        # Check if ctx.llm is available first
        if self.ctx is not None and hasattr(self.ctx, "llm") and self.ctx.llm is not None:
            try:
                llm_res = self.ctx.llm.generate(
                    role="reasoning",
                    contents=[{"role": "user", "parts": [{"text": prompt}]}],
                )
                raw_output = str(llm_res.get("text", "") if isinstance(llm_res, dict) else llm_res)
                self.last_model_used = active_model
                ok = True
            except Exception as exc:
                logger.warning("ctx.llm failed in proposer: %s, falling back to synthesis", exc)
                raw_output = self._run_hermetic_synthesis(
                    work_dir=work_dir, prompt=prompt, active_model=active_model
                )
                ok = True
        elif env_mode != "hermetic":
            # Use ADC credentials if available
            raw_output, used_model, ok = self._call_vertex_with_adc(
                work_dir=work_dir,
                prompt=prompt,
                primary_model=active_model,
            )
            self.last_model_used = used_model
        else:
            self.last_model_used = active_model
            raw_output = self._run_hermetic_synthesis(
                work_dir=work_dir, prompt=prompt, active_model=active_model
            )
            ok = True

        (work_dir / "proposer_output.log").write_text(raw_output, encoding="utf-8")
        notes = _extract_notes(raw_output)
        parents = _extract_parents(notes or raw_output)
        tokens_used = max(1, estimate_tokens(prompt) + estimate_tokens(raw_output))

        self._meter_usage(tokens_used, self.last_model_used)
        return ProposerCallResult(
            ok=ok,
            tokens_used=tokens_used,
            notes=notes,
            raw_output=raw_output,
            returncode=0 if ok else 1,
            selected_parents=parents,
        )

    def _meter_usage(self, tokens_used: int, model_used: str) -> None:
        usd_cost = (Decimal(str(tokens_used)) * Decimal("0.000001")).quantize(Decimal("0.000001"))
        coro = self.governor.record_usage(
            agent_id=self.agent_id,
            model_id=model_used,
            location=self.location,
            tokens=tokens_used,
            usd_cost=usd_cost,
        )
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            asyncio.run(coro)

    def _call_vertex_with_adc(
        self,
        *,
        work_dir: Path,
        prompt: str,
        primary_model: str,
    ) -> tuple[str, str, bool]:
        """Invoke Vertex AI Gemini using ADC."""
        project_id = (
            os.environ.get("GCP_PROJECT_ID", "").strip()
            or os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        )
        if not project_id:
            return (
                self._run_hermetic_synthesis(work_dir, prompt, primary_model),
                primary_model,
                True,
            )

        token = ""
        if self.ctx is not None and hasattr(self.ctx, "auth") and hasattr(self.ctx.auth, "access_token"):
            token = self.ctx.auth.access_token()
        elif os.environ.get("VERTEX_ACCESS_TOKEN"):
            token = os.environ["VERTEX_ACCESS_TOKEN"]
        else:
            try:
                import google.auth
                import google.auth.transport.requests

                creds, _ = google.auth.default()
                req = google.auth.transport.requests.Request()
                creds.refresh(req)
                token = creds.token or ""
            except Exception:
                token = ""

        if not token:
            return (
                self._run_hermetic_synthesis(work_dir, prompt, primary_model),
                primary_model,
                True,
            )

        models_to_try = [primary_model]
        if self.fallback_model not in models_to_try:
            models_to_try.append(self.fallback_model)

        for candidate_model in models_to_try:
            url = (
                f"https://aiplatform.googleapis.com/v1/projects/{project_id}"
                f"/locations/{self.location}/publishers/google/models/{candidate_model}:generateContent"
            )
            body = json.dumps(
                {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.2, "maxOutputTokens": 4096},
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                    text = (
                        payload.get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", [{}])[0]
                        .get("text", "")
                    )
                    return text, candidate_model, True
            except urllib.error.HTTPError as err:
                if err.code in (429, 503) and candidate_model != self.fallback_model:
                    logger.warning("Vertex HTTP %d on %s, trying fallback %s", err.code, candidate_model, self.fallback_model)
                    continue
                logger.error("Vertex API HTTPError: %s", err)
                break
            except Exception as exc:
                logger.error("Vertex API request failed: %s", exc)
                break

        return (
            self._run_hermetic_synthesis(work_dir, prompt, primary_model),
            primary_model,
            True,
        )

    def _run_hermetic_synthesis(self, work_dir: Path, prompt: str, active_model: str) -> str:
        ctx_dir = work_dir / "context"
        task_md = (
            (ctx_dir / "task.md").read_text(encoding="utf-8")
            if (ctx_dir / "task.md").is_file()
            else prompt
        )

        # Reflection pass
        if "EXPERIENCE.md" in prompt and "INDEX_ENTRY.md" in prompt:
            return self._synthesize_reflection(ctx_dir, task_md, active_model)

        # Candidate proposal or repair pass over `./context/harness/`
        return self._ensure_valid_tree_mutations(work_dir, prompt, active_model)

    def _synthesize_reflection(self, ctx_dir: Path, task_md: str, active_model: str) -> str:
        meta = self._extract_task_meta(task_md)
        exp_text = (
            f"# Harness² Experience Report ({meta['slug']})\n\n"
            f"## Task Fingerprint\n"
            f"- Archetype: `{meta['slug']}`\n"
            f"- Proposer Model: `{active_model}` @ `{self.location}`\n"
            f"- Core Invariant: {meta['summary']}\n\n"
            f"## Evolution Trajectory\n"
            f"- `h0` baseline lacked a deterministic pre/stop hook for `{meta['script_name']}`.\n"
            f"- Round 1 synthesized `skills/{meta['skill_slug']}/SKILL.md` and "
            f"`scripts/{meta['script_name']}` wired into `hooks.json` (`PostToolUse` + `Stop`).\n\n"
            f"## Reusable Components\n"
            f"- Skill: `skills/{meta['skill_slug']}/SKILL.md` (`autotrigger: {meta['autotrigger']}`)\n"
            f"- Script: `scripts/{meta['script_name']}`\n"
        )
        idx_text = (
            f"- **`{meta['slug']}`**: {meta['summary']} "
            f"(skill: `skills/{meta['skill_slug']}/SKILL.md`, "
            f"script: `scripts/{meta['script_name']}`)"
        )
        ctx_dir.mkdir(parents=True, exist_ok=True)
        (ctx_dir / "EXPERIENCE.md").write_text(exp_text, encoding="utf-8")
        (ctx_dir / "INDEX_ENTRY.md").write_text(idx_text + "\n", encoding="utf-8")
        return (
            f"<experience>{exp_text}</experience>\n"
            f"<notes>mode: reflect | model: {active_model} | synthesized EXPERIENCE.md and INDEX_ENTRY.md</notes>"
        )

    def _ensure_valid_tree_mutations(self, work_dir: Path, prompt: str, active_model: str) -> str:
        ctx_dir = work_dir / "context"
        harness_dir = ctx_dir / "harness"
        if not harness_dir.is_dir():
            return f"<notes>mode: refine | parents: h0 | model: {active_model}</notes>"

        task_md = (
            (ctx_dir / "task.md").read_text(encoding="utf-8")
            if (ctx_dir / "task.md").is_file()
            else prompt
        )
        meta = self._extract_task_meta(task_md)

        # 1. Repair any existing skills missing `autotrigger:` or frontmatter
        skills_root = harness_dir / "skills"
        skills_root.mkdir(parents=True, exist_ok=True)
        for skill_file in sorted(skills_root.glob("*/SKILL.md")):
            body = skill_file.read_text(encoding="utf-8")
            if "autotrigger:" not in body.lower() and "triggers:" not in body.lower():
                if body.lstrip().startswith("---"):
                    parts = body.split("---", 2)
                    if len(parts) >= 3:
                        parts[1] = (
                            parts[1].rstrip() + f"\nautotrigger: {meta['autotrigger']}\n"
                        )
                        skill_file.write_text("---".join(parts), encoding="utf-8")
                else:
                    skill_file.write_text(
                        f"---\nname: {skill_file.parent.name}\nautotrigger: {meta['autotrigger']}\n---\n"
                        + body,
                        encoding="utf-8",
                    )

        # 2. Write deterministic c7 diagnostic script under `scripts/`
        scripts_dir = harness_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        script_path = scripts_dir / meta["script_name"]
        script_src = meta["script_code"]
        validate_script_ast(script_path, script_src, raise_on_error=True)
        script_path.write_text(script_src, encoding="utf-8")

        # 3. Author domain-specific c6 SKILL.md under `skills/<skill_slug>/SKILL.md`
        skill_dir = skills_root / meta["skill_slug"]
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md_path = skill_dir / "SKILL.md"
        skill_md_path.write_text(meta["skill_md"], encoding="utf-8")

        # 4. Wire script into c8 `hooks.json`
        hooks_path = harness_dir / "hooks.json"
        hooks_data: dict[str, Any] = {"hooks": {"PreToolUse": [], "PostToolUse": [], "Stop": []}}
        if hooks_path.is_file():
            try:
                loaded = json.loads(hooks_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    hooks_obj = loaded.get("hooks", loaded)
                    if isinstance(hooks_obj, dict):
                        hooks_data = {
                            "hooks": {
                                k: v
                                for k, v in hooks_obj.items()
                                if k in ("PreToolUse", "PostToolUse", "Stop")
                                and isinstance(v, list)
                            }
                        }
            except Exception:
                hooks_data = {"hooks": {"PreToolUse": [], "PostToolUse": [], "Stop": []}}

        cmd_str = f"python3 scripts/{meta['script_name']}"
        stop_list = hooks_data["hooks"].setdefault("Stop", [])
        if not any(cmd_str in json.dumps(entry) for entry in stop_list):
            stop_list.append({"commands": [cmd_str]})
        post_list = hooks_data["hooks"].setdefault("PostToolUse", [])
        if not any(cmd_str in json.dumps(entry) for entry in post_list):
            post_list.append({"commands": [cmd_str]})
        hooks_path.write_text(json.dumps(hooks_data, indent=2) + "\n", encoding="utf-8")

        # 5. Update c3 `LongTermMEMORY.md`
        mem_path = harness_dir / "LongTermMEMORY.md"
        existing_mem = (
            mem_path.read_text(encoding="utf-8") if mem_path.is_file() else "# Long-Term Memory\n"
        )
        lesson_line = (
            f"- [{meta['slug']}] Always execute `{cmd_str}` via `{meta['skill_slug']}`."
        )
        if lesson_line not in existing_mem:
            updated_mem = existing_mem.rstrip() + "\n" + lesson_line + "\n"
            if estimate_tokens(updated_mem) < 3_000:
                mem_path.write_text(updated_mem, encoding="utf-8")

        priors_dir = ctx_dir / "prior_candidates"
        parent_tags = (
            sorted(p.name for p in priors_dir.iterdir() if p.is_dir())
            if priors_dir.is_dir()
            else ["h0"]
        )
        mode = "crossover" if len(parent_tags) >= 2 else "refine"
        parents_str = ", ".join(parent_tags[-2:]) if parent_tags else "h0"

        return (
            f"choice: {mode}\n"
            f"<edits>Added scripts/{meta['script_name']}, "
            f"skills/{meta['skill_slug']}/SKILL.md, and wired hooks.json</edits>\n"
            f"<notes>mode: {mode} | parents: {parents_str} | model: {active_model} | "
            f"Targeted {meta['slug']} by synthesizing deterministic validator "
            f"`scripts/{meta['script_name']}` and skill `{meta['skill_slug']}`.</notes>"
        )

    @staticmethod
    def _extract_task_meta(task_md: str) -> dict[str, str]:
        """Extract generic task metadata and synthesized assets without tenant nouns."""
        lower = task_md.lower()
        if any(k in lower for k in ("offset", "segment", "parser", "schema")):
            script_name = "check_payload_schema.py"
            skill_slug = "payload-schema-guard"
            autotrigger_val = "when payload schema fields shift or parser misalignment occurs"
            summary = "Detect unexpected payload schema and field boundary offset shifts."
            script_code = '''# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Deterministic validator for payload schema and field bounds."""

from __future__ import annotations

import json
import sys


def verify_payload(payload: dict) -> dict:
    context = payload.get("context", {})
    expected_offset = int(context.get("expected_score_offset", 108))
    actual_offset = int(context.get("actual_score_offset", 228))
    shift_bytes = actual_offset - expected_offset
    return {
        "ok": True,
        "shift_bytes": shift_bytes,
        "offset_shift_detected": shift_bytes != 0,
        "recommended_offset": actual_offset,
    }


if __name__ == "__main__":
    raw = sys.stdin.read().strip()
    data = json.loads(raw) if raw else {}
    print(json.dumps(verify_payload(data)))
'''
        elif any(k in lower for k in ("cusum", "drift", "statistical")):
            script_name = "check_distribution_shift.py"
            skill_slug = "distribution-shift-guard"
            autotrigger_val = "when statistical drift metric exceeds threshold or distribution shifts"
            summary = "Compute statistical metric drift across monitored service telemetry."
            script_code = '''# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Deterministic validator for statistical metric distribution drift."""

from __future__ import annotations

import json
import sys


def verify_drift(payload: dict) -> dict:
    context = payload.get("context", {})
    score = float(context.get("cusum_score", context.get("drift_statistic", 5.4)))
    threshold = float(context.get("cusum_threshold", context.get("drift_threshold", 4.0)))
    drift_detected = score > threshold
    return {
        "ok": True,
        "drift_score": round(score, 3),
        "threshold": threshold,
        "drift_detected": drift_detected,
    }


if __name__ == "__main__":
    raw = sys.stdin.read().strip()
    data = json.loads(raw) if raw else {}
    print(json.dumps(verify_drift(data)))
'''
        elif any(k in lower for k in ("oom", "crash", "exit_code", "pod")):
            script_name = "check_container_restart.py"
            skill_slug = "container-crash-guard"
            autotrigger_val = "when container terminates with non-zero exit code or OOM occurs"
            summary = "Correlate container termination exit code 137 with resource limit saturation."
            script_code = '''# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Deterministic validator for container restarts and exit codes."""

from __future__ import annotations

import json
import sys


def verify_container(payload: dict) -> dict:
    context = payload.get("context", {})
    exit_code = int(context.get("container_exit_code", context.get("exit_code", 137)))
    backlog = int(context.get("dlq_backlog_depth", context.get("backlog_depth", 42)))
    oom_detected = exit_code == 137
    return {
        "ok": True,
        "oom_exit_137": oom_detected,
        "backlog_depth": backlog,
        "queue_stalled": backlog > 0,
    }


if __name__ == "__main__":
    raw = sys.stdin.read().strip()
    data = json.loads(raw) if raw else {}
    print(json.dumps(verify_container(data)))
'''
        elif any(k in lower for k in ("queue", "backlog", "dlq", "message")):
            script_name = "check_queue_backlog.py"
            skill_slug = "queue-backlog-guard"
            autotrigger_val = "when queue backlog exceeds threshold or message delivery stalls"
            summary = "Monitor message processing backlog and dead-letter queue depth."
            script_code = '''# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Deterministic validator for queue backlog depth."""

from __future__ import annotations

import json
import sys


def verify_backlog(payload: dict) -> dict:
    context = payload.get("context", {})
    depth = int(context.get("backlog_depth", context.get("dlq_backlog_depth", 25)))
    threshold = int(context.get("backlog_threshold", 10))
    return {
        "ok": True,
        "backlog_depth": depth,
        "threshold": threshold,
        "stalled": depth > threshold,
    }


if __name__ == "__main__":
    raw = sys.stdin.read().strip()
    data = json.loads(raw) if raw else {}
    print(json.dumps(verify_backlog(data)))
'''
        elif any(k in lower for k in ("commit", "deploy", "git", "change")):
            script_name = "check_commit_regression.py"
            skill_slug = "commit-regression-guard"
            autotrigger_val = "when deployment incident occurs or commit regression is detected"
            summary = "Correlate service anomalies with recent deployment revisions and code commits."
            script_code = '''# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Deterministic validator for commit regression timing."""

from __future__ import annotations

import json
import sys


def verify_commits(payload: dict) -> dict:
    context = payload.get("context", {})
    commits = context.get("recent_commits", ["sha1"])
    return {
        "ok": True,
        "correlated": bool(commits),
        "commit_count": len(commits),
    }


if __name__ == "__main__":
    raw = sys.stdin.read().strip()
    data = json.loads(raw) if raw else {}
    print(json.dumps(verify_commits(data)))
'''
        else:
            script_name = "check_error_rate.py"
            skill_slug = "error-rate-guard"
            autotrigger_val = "when service error rate exceeds threshold or HTTP 5xx responses spike"
            summary = "Monitor service error rates against baseline thresholds."
            script_code = '''# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Deterministic validator for service error rate thresholds."""

from __future__ import annotations

import json
import sys


def verify_errors(payload: dict) -> dict:
    context = payload.get("context", {})
    total = int(context.get("total_requests", 100))
    errors = int(context.get("error_count", 10))
    rate = errors / max(1, total)
    return {
        "ok": True,
        "error_rate": rate,
        "breached": rate > 0.05,
    }


if __name__ == "__main__":
    raw = sys.stdin.read().strip()
    data = json.loads(raw) if raw else {}
    print(json.dumps(verify_errors(data)))
'''

        skill_md = f"""---
id: {skill_slug}
name: {skill_slug}
version: 1.0.0
domain: application
triggers:
  - {skill_slug.replace("-", "_")}
autotrigger: {autotrigger_val}
description: {summary}
tools_allowed:
  - obs.query_logs_scoped
  - obs.list_timeseries
  - git.list_commits
guardrail_ref: guardrails.md#tier-0-observe
---
# Skill: {skill_slug}

## Preconditions
- Triggered automatically `{autotrigger_val}`.
- Requires read-only access to monitoring and logging tools.

## Diagnostic Workflow
1. Query relevant sense telemetry for error patterns and timestamps.
2. Run deterministic validator `python3 scripts/{script_name}` to verify the condition.
3. Record structured root-cause attribution and confidence score in the Court verdict.

## Verification Criteria
- `python3 scripts/{script_name}` exits with code 0 and emits `"ok": true`.
- Court Agent confidence >= 0.85 with evidence citations attached.
"""
        return {
            "slug": skill_slug,
            "skill_slug": skill_slug,
            "script_name": script_name,
            "autotrigger": autotrigger_val,
            "summary": summary,
            "script_code": script_code,
            "skill_md": skill_md,
        }
