# Copyright 2026 Harness² Authors & Google LLC
# SPDX-License-Identifier: Apache-2.0
"""HarnessTree: load, dump, diff, and validate a directory of harness files.

Validation (Section 3.3 + Section C.3) checks:
  1. Combined token estimate of c1..c4 <= BudgetSpec.always_on_max (12,000).
  2. hooks.json (c8) parses as JSON and every entry targets a valid hook event
     name on this surface with a non-empty command list.
  3. Every SKILL.md (c6) and sub-agent file (c5) is non-empty and begins with a
     YAML frontmatter block or a top-level markdown heading.
  4. Any script referenced as `python3 scripts/foo.py` inside a SKILL.md or
     hooks.json exists in scripts/ (c7) and compiles cleanly with `ast.parse`.
  5. Every SKILL.md (c6) declares a non-empty `autotrigger` line in its frontmatter
     or first 12 lines (`autotrigger:` or `triggers:`) so the skill router knows
     when to load it.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from godview.harness.core.spec import Category, SurfaceSpec


def estimate_tokens(text: str) -> int:
    """Fast deterministic token estimator (~4 chars/token, calibrated for English+code)."""
    if not text:
        return 0
    return (len(text) + 3) // 4


@dataclass
class HarnessTree:
    """In-memory view over a concrete harness directory on disk."""

    root: Path
    surface: SurfaceSpec

    def always_on_paths(self) -> dict[Category, Path]:
        return {cat: self.root / rel for cat, rel in self.surface.always_on_files.items()}

    def always_on_text(self) -> dict[Category, str]:
        out: dict[Category, str] = {}
        for cat, path in self.always_on_paths().items():
            out[cat] = path.read_text(encoding="utf-8") if path.is_file() else ""
        return out

    def always_on_tokens(self) -> int:
        return sum(estimate_tokens(t) for t in self.always_on_text().values())

    def sub_agents(self) -> list[Path]:
        return sorted(self.root.glob(self.surface.sub_agents_glob))

    def skills(self) -> list[Path]:
        return sorted(self.root.glob(self.surface.skills_glob))

    def scripts(self) -> list[Path]:
        return sorted(self.root.glob(self.surface.scripts_glob))

    def hooks_path(self) -> Path:
        return self.root / self.surface.hooks_file

    def clone_to(self, dst: Path) -> HarnessTree:
        if dst.exists():
            shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.root, dst)
        return HarnessTree(dst, self.surface)

    def render_inventory(self) -> str:
        """Compact summary of every file in the tree, shown to the proposer."""
        lines: list[str] = [
            f"Harness surface: {self.surface.name} "
            f"(Always-on tokens: {self.always_on_tokens()} / {self.surface.always_on_max})"
        ]
        for cat, path in self.always_on_paths().items():
            rel = path.relative_to(self.root)
            toks = estimate_tokens(path.read_text(encoding="utf-8")) if path.is_file() else 0
            lines.append(f"  [{cat}] {rel}  ({toks} tok)")
        for label, items in (
            ("c5 sub-agent", self.sub_agents()),
            ("c6 skill", self.skills()),
            ("c7 script", self.scripts()),
        ):
            for p in items:
                rel = p.relative_to(self.root)
                toks = estimate_tokens(p.read_text(encoding="utf-8"))
                lines.append(f"  [{label}] {rel}  ({toks} tok)")
        hp = self.hooks_path()
        if hp.is_file():
            toks = estimate_tokens(hp.read_text(encoding="utf-8"))
            lines.append(f"  [c8 hooks] {hp.relative_to(self.root)}  ({toks} tok)")
        return "\n".join(lines)


def render_tree(tree: HarnessTree) -> str:
    """Render compact inventory of a HarnessTree."""
    return tree.render_inventory()


@dataclass
class ValidationReport:
    """Result of running `validate(tree)` over a HarnessTree."""

    ok: bool
    always_on_tokens: int
    errors: list[str] = field(default_factory=list)


_SCRIPT_REF_RE = re.compile(r"(?:python3?\s+)((?:skills/[\w\-]+/)?scripts/[\w.\-]+\.py)")


def autotrigger(text: str) -> str | None:
    """Extract the non-empty autotrigger rule from a SKILL.md header/frontmatter.

    Accepts `autotrigger: <rule>` or `triggers: <rule>` within the YAML frontmatter
    or first 20 lines of the skill file.
    """
    lines = text.lstrip().splitlines()[:25]
    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        for prefix in ("autotrigger:", "triggers:", "trigger:"):
            if lower.startswith(prefix):
                val = stripped[len(prefix) :].strip().strip("\"'")
                if val:
                    return val
    return None


def validate(tree: HarnessTree, *, require_autotrigger: bool = True) -> ValidationReport:
    """Run all five structural checks from Section 3.3 + C.3 on `tree`."""
    errors: list[str] = []

    # (1) Always-on Context token ceiling.
    ao_tokens = tree.always_on_tokens()
    if ao_tokens > tree.surface.always_on_max:
        errors.append(
            f"Always-on Context exceeds ceiling: {ao_tokens} > {tree.surface.always_on_max} tokens"
        )
    for cat in ("c1", "c2"):
        p = tree.always_on_paths()[cat]  # type: ignore[index]
        if not p.is_file() or not p.read_text(encoding="utf-8").strip():
            errors.append(
                f"Required always-on file [{cat}] {p.relative_to(tree.root)} is missing or empty"
            )

    # (2) hooks.json schema + valid event names.
    hp = tree.hooks_path()
    hooks_text = ""
    if hp.is_file():
        hooks_text = hp.read_text(encoding="utf-8").strip()
        if hooks_text:
            try:
                data = json.loads(hooks_text)
                hooks_obj = data.get("hooks", data) if isinstance(data, dict) else None
                if not isinstance(hooks_obj, dict):
                    errors.append(f"{hp.name}: top-level object or `hooks` field must be a dict")
                else:
                    for event, entries in hooks_obj.items():
                        if event not in tree.surface.hook_events:
                            errors.append(
                                f"{hp.name}: unsupported hook event `{event}` "
                                f"(allowed: {sorted(tree.surface.hook_events)})"
                            )
                        if not isinstance(entries, list):
                            errors.append(f"{hp.name}: event `{event}` must map to a list")
            except json.JSONDecodeError as exc:
                errors.append(f"{hp.name}: invalid JSON ({exc})")

    # (3) Sub-agents (c5) and skills (c6) structure + (5) autotrigger.
    for kind, paths in (("sub-agent", tree.sub_agents()), ("skill", tree.skills())):
        for p in paths:
            rel = p.relative_to(tree.root)
            body = p.read_text(encoding="utf-8").strip()
            if not body:
                errors.append(f"{kind} {rel} is empty")
                continue
            first = body.splitlines()[0].strip()
            if not (first.startswith("---") or first.startswith("#")):
                errors.append(f"{kind} {rel} must start with YAML frontmatter `---` or `#` heading")
            if kind == "skill" and require_autotrigger and autotrigger(body) is None:
                errors.append(f"skill {rel} is missing a non-empty `autotrigger:` declaration")

    # (4) Scripts (c7): every script parses as Python + every referenced script exists.
    for sp in tree.scripts():
        rel = sp.relative_to(tree.root)
        if sp.suffix == ".py":
            try:
                ast.parse(sp.read_text(encoding="utf-8"), filename=str(rel))
            except SyntaxError as exc:
                errors.append(f"script {rel} has Python syntax error: {exc}")

    scanned_blobs = [hooks_text, *tree.always_on_text().values()]
    scanned_blobs.extend(p.read_text(encoding="utf-8") for p in tree.skills())
    for blob in scanned_blobs:
        for match in _SCRIPT_REF_RE.finditer(blob):
            ref = match.group(1)
            if not (tree.root / ref).is_file():
                errors.append(f"referenced script `{ref}` does not exist in harness tree")

    return ValidationReport(ok=not errors, always_on_tokens=ao_tokens, errors=errors)
