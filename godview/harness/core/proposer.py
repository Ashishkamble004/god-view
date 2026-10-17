# Copyright 2026 Harness² Authors & Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Proposer interface and base class for Harness² (Section 3.3 & Appendix C.1)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from godview.harness.core.config import load_env
from godview.harness.core.spec import Channel
from godview.harness.core.tree import estimate_tokens

_NOTES_RE = re.compile(r"<notes>(.*?)</notes>", re.DOTALL | re.IGNORECASE)
_PARENTS_RE = re.compile(r"parents\s*:\s*([^\n<]+)", re.IGNORECASE)


@dataclass
class ProposerCallResult:
    """Result of a single proposer invocation over a staged context workspace."""

    ok: bool
    tokens_used: int
    notes: str
    raw_output: str
    returncode: int
    selected_parents: list[str] = field(default_factory=list)


class Proposer:
    """Invokes the meta-agent proposer inside a staged `<work_dir>/context/` workspace."""

    def __init__(
        self,
        channel: Channel = "vertex_gemini",
        model: str | None = None,
        timeout_sec: int = 900,
    ) -> None:
        load_env()
        self.channel: Channel = channel
        self.model = model or os.environ.get("PROPOSER_MODEL", "gemini-4-argon")
        self.timeout_sec = timeout_sec

    def run(self, work_dir: Path, prompt: str) -> ProposerCallResult:
        """Run the proposer in `work_dir` (which already contains `./context/`)."""
        work_dir.mkdir(parents=True, exist_ok=True)
        prompt_file = work_dir / "proposer_prompt.md"
        prompt_file.write_text(prompt, encoding="utf-8")

        cmd, env = self._build_cmd(prompt, work_dir)
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(work_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                check=False,
            )
            raw = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
            rc = proc.returncode
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode("utf-8", "replace") if exc.stdout else ""
            raw = f"[TIMEOUT after {self.timeout_sec}s]\n" + out
            rc = 124

        (work_dir / "proposer_output.log").write_text(raw, encoding="utf-8")
        notes = _extract_notes(raw)
        parents = _extract_parents(notes or raw)
        tokens = estimate_tokens(prompt) + estimate_tokens(raw)
        return ProposerCallResult(
            ok=(rc == 0),
            tokens_used=tokens,
            notes=notes,
            raw_output=raw,
            returncode=rc,
            selected_parents=parents,
        )

    def _build_cmd(self, prompt: str, work_dir: Path) -> tuple[list[str], dict[str, str]]:
        env = os.environ.copy()
        if self.channel == "claude_code":
            bin_path = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"
            cmd = [
                bin_path,
                "-p",
                prompt,
                "--dangerously-skip-permissions",
                "--model",
                self.model,
            ]
            return cmd, env

        if self.channel == "opencode":
            bin_path = os.environ.get("OPENCODE_BIN") or shutil.which("opencode") or "opencode"
            cmd = [bin_path, "run", "--model", self.model, prompt]
            return cmd, env

        raise ValueError(
            f"Unsupported subprocess proposer channel: {self.channel}. "
            "Use VertexGeminiProposer for channel='vertex_gemini'."
        )


def _extract_notes(raw: str) -> str:
    matches = _NOTES_RE.findall(raw)
    if matches:
        return matches[-1].strip()
    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
    return "\n".join(lines[-8:])[:1200]


def _extract_parents(text: str) -> list[str]:
    m = _PARENTS_RE.search(text)
    if not m:
        return []
    raw_list = m.group(1)
    tags = [t.strip().strip("`'\"[]()") for t in re.split(r"[,\s]+", raw_list) if t.strip()]
    return [t for t in tags if t and t.lower() != "none"]
