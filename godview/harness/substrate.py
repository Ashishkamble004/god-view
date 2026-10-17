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
"""God-View Court Agent substrate (GODVIEW_SURFACE), AST script sandbox, and HookRunner."""

from __future__ import annotations

import ast
import fnmatch
import io
import json
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from godview.harness.core import prompts
from godview.harness.core.spec import InstallMode, Substrate, SurfaceSpec
from godview.harness.core.tree import HarnessTree

GODVIEW_SURFACE = SurfaceSpec(
    name="godview",
    always_on_files={
        "c1": "systemprompt.md",
        "c2": "guardrails.md",
        "c3": "LongTermMEMORY.md",
        "c4": "tool_descriptions/tool_guidance.md",
    },
    sub_agents_glob="sub_agents/*/AGENT.md",
    skills_glob="skills/*/SKILL.md",
    scripts_glob="scripts/*.py",
    hooks_file="hooks.json",
    always_on_max=12_000,
    hook_events=frozenset({"PreToolUse", "PostToolUse", "Stop"}),
)

_BANNED_IMPORT_ROOTS: frozenset[str] = frozenset(
    {
        "subprocess",
        "pty",
        "socket",
        "urllib",
        "http",
        "requests",
        "httpx",
        "aiohttp",
        "ftplib",
        "telnetlib",
        "ctypes",
        "multiprocessing",
        "pickle",
        "marshal",
        "shutil",
        "tempfile",
        "webbrowser",
    }
)

_BANNED_OS_CALLS: frozenset[str] = frozenset(
    {
        "system",
        "popen",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "fork",
        "kill",
        "remove",
        "unlink",
        "rmdir",
        "removedirs",
        "chmod",
        "chown",
    }
)

_BANNED_BUILTINS: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "breakpoint",
        "input",
    }
)

_BANNED_DUNDERS: frozenset[str] = frozenset(
    {
        "__subclasses__",
        "__globals__",
        "__code__",
        "__builtins__",
        "__closure__",
    }
)


class ScriptValidationError(ValueError):
    """Raised when a c7 script fails the AST sandbox check."""


def validate_script_ast(
    path: Path | str,
    src: str | None = None,
    *,
    raise_on_error: bool = False,
) -> list[str]:
    """Validate that a c7 script is deterministic and contains no shell/network/runtime escapes.

    Returns a list of error strings (empty when safe). If `raise_on_error=True`,
    raises `ScriptValidationError` when any violation is found.
    """
    p = Path(path)
    source = src if src is not None else p.read_text(encoding="utf-8")
    errors: list[str] = []

    try:
        tree = ast.parse(source, filename=str(p))
    except SyntaxError as exc:
        errors.append(f"{p.name}: syntax error at line {exc.lineno}: {exc.msg}")
        if raise_on_error:
            raise ScriptValidationError("; ".join(errors)) from exc
        return errors

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _BANNED_IMPORT_ROOTS:
                    errors.append(
                        f"{p.name}:{node.lineno}: forbidden import `{alias.name}` in c7 script sandbox"
                    )
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod in _BANNED_IMPORT_ROOTS:
                errors.append(
                    f"{p.name}:{node.lineno}: forbidden import from `{node.module}` in c7 script sandbox"
                )
            if mod == "os":
                for alias in node.names:
                    if alias.name in _BANNED_OS_CALLS:
                        errors.append(
                            f"{p.name}:{node.lineno}: forbidden `from os import {alias.name}` in c7 script sandbox"
                        )
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                fn = node.func.id
                if fn in _BANNED_BUILTINS:
                    errors.append(
                        f"{p.name}:{node.lineno}: forbidden builtin call `{fn}()` in c7 script sandbox"
                    )
                elif fn == "open" and len(node.args) >= 2:
                    mode_arg = node.args[1]
                    if isinstance(mode_arg, ast.Constant) and isinstance(mode_arg.value, str):
                        if any(m in mode_arg.value for m in ("w", "a", "x", "+")):
                            errors.append(
                                f"{p.name}:{node.lineno}: write-mode `open(..., {mode_arg.value!r})` forbidden in c7 script"
                            )
            elif isinstance(node.func, ast.Attribute):
                attr = node.func.attr
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                    if attr in _BANNED_OS_CALLS:
                        errors.append(
                            f"{p.name}:{node.lineno}: forbidden `os.{attr}()` call in c7 script sandbox"
                        )
        elif isinstance(node, ast.Attribute):
            if node.attr in _BANNED_DUNDERS:
                errors.append(
                    f"{p.name}:{node.lineno}: forbidden dunder attribute `{node.attr}` in c7 script sandbox"
                )

    if raise_on_error and errors:
        raise ScriptValidationError("; ".join(errors))
    return errors


@dataclass(frozen=True)
class HookExecutionResult:
    """Result of running a single c8 hook script."""

    event: str
    command: str
    script_rel: str
    passed: bool
    returncode: int
    stdout: str
    stderr: str
    parsed_output: dict[str, Any] = field(default_factory=dict)


_CMD_SCRIPT_RE = re.compile(r"(?:python3?\s+)?((?:skills/[\w\-]+/)?scripts/[\w.\-]+\.py)")


class HookRunner:
    """Deterministic in-process sandboxed executor for c8 `hooks.json` lifecycle hooks."""

    def __init__(self, tree: HarnessTree) -> None:
        self.tree = tree

    def _load_hooks_map(self) -> dict[str, list[Any]]:
        hp = self.tree.hooks_path()
        if not hp.is_file():
            return {}
        raw = hp.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        hooks_obj = data.get("hooks", data)
        return hooks_obj if isinstance(hooks_obj, dict) else {}

    def run_pre_tool_use(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> list[HookExecutionResult]:
        """Run all matching `PreToolUse` hooks for `tool_name`."""
        payload = {
            "event": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": tool_input,
            "context": context or {},
        }
        return self._run_event("PreToolUse", target_name=tool_name, payload=payload)

    def run_post_tool_use(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: Any,
        context: dict[str, Any] | None = None,
    ) -> list[HookExecutionResult]:
        """Run all matching `PostToolUse` hooks for `tool_name`."""
        payload = {
            "event": "PostToolUse",
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_output": tool_output,
            "context": context or {},
        }
        return self._run_event("PostToolUse", target_name=tool_name, payload=payload)

    def run_stop_hooks(
        self,
        final_output: dict[str, Any] | str,
        context: dict[str, Any] | None = None,
    ) -> list[HookExecutionResult]:
        """Run all `Stop` hooks before the Court Agent concludes its rollout."""
        payload = {
            "event": "Stop",
            "final_output": final_output,
            "context": context or {},
        }
        return self._run_event("Stop", target_name="Stop", payload=payload)

    def _run_event(
        self,
        event: str,
        *,
        target_name: str,
        payload: dict[str, Any],
    ) -> list[HookExecutionResult]:
        hooks_map = self._load_hooks_map()
        entries = hooks_map.get(event, [])
        if not isinstance(entries, list):
            return []

        results: list[HookExecutionResult] = []
        for entry in entries:
            matcher = "*"
            commands: list[str] = []
            if isinstance(entry, str):
                commands = [entry]
            elif isinstance(entry, dict):
                matcher = str(entry.get("matcher", "*") or "*")
                if "commands" in entry and isinstance(entry["commands"], list):
                    commands = [str(c) for c in entry["commands"]]
                elif "command" in entry and isinstance(entry["command"], str):
                    commands = [entry["command"]]
                elif "hooks" in entry and isinstance(entry["hooks"], list):
                    for sub in entry["hooks"]:
                        if isinstance(sub, dict) and "command" in sub:
                            commands.append(str(sub["command"]))
                        elif isinstance(sub, str):
                            commands.append(sub)

            if not self._matches(matcher, target_name):
                continue

            for cmd in commands:
                results.append(self._execute_command(event, cmd, payload))
        return results

    @staticmethod
    def _matches(pattern: str, target_name: str) -> bool:
        if pattern in ("*", "", ".*"):
            return True
        if fnmatch.fnmatch(target_name, pattern):
            return True
        try:
            return bool(re.search(pattern, target_name))
        except re.error:
            return False

    def _execute_command(
        self,
        event: str,
        command: str,
        payload: dict[str, Any],
    ) -> HookExecutionResult:
        m = _CMD_SCRIPT_RE.search(command)
        if not m:
            return HookExecutionResult(
                event=event,
                command=command,
                script_rel="",
                passed=False,
                returncode=2,
                stdout="",
                stderr=f"Hook command `{command}` does not reference a valid `scripts/*.py` path",
            )
        script_rel = m.group(1)
        script_path = self.tree.root / script_rel
        if not script_path.is_file():
            return HookExecutionResult(
                event=event,
                command=command,
                script_rel=script_rel,
                passed=False,
                returncode=127,
                stdout="",
                stderr=f"Referenced script `{script_rel}` not found in {self.tree.root}",
            )

        src = script_path.read_text(encoding="utf-8")
        ast_errors = validate_script_ast(script_path, src)
        if ast_errors:
            return HookExecutionResult(
                event=event,
                command=command,
                script_rel=script_rel,
                passed=False,
                returncode=126,
                stdout="",
                stderr="; ".join(ast_errors),
            )

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        stdin_buf = io.StringIO(json.dumps(payload))
        old_stdin = sys.stdin
        old_argv = sys.argv
        rc = 0
        try:
            sys.stdin = stdin_buf
            sys.argv = [str(script_path)]
            exec_globals: dict[str, Any] = {
                "__name__": "__main__",
                "__file__": str(script_path),
                "HOOK_PAYLOAD": payload,
            }
            code_obj = compile(src, filename=str(script_path), mode="exec")
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                exec(code_obj, exec_globals)  # noqa: S102 - AST-validated deterministic c7 script
        except SystemExit as exc:
            if exc.code is None:
                rc = 0
            elif isinstance(exc.code, int):
                rc = exc.code
            else:
                rc = 1
                stderr_buf.write(str(exc.code))
        except Exception as exc:  # noqa: BLE001
            rc = 1
            stderr_buf.write(f"{type(exc).__name__}: {exc}\n")
        finally:
            sys.stdin = old_stdin
            sys.argv = old_argv

        out_str = stdout_buf.getvalue().strip()
        err_str = stderr_buf.getvalue().strip()
        parsed: dict[str, Any] = {}
        if out_str:
            for line in reversed(out_str.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        obj = json.loads(line)
                        if isinstance(obj, dict):
                            parsed = obj
                            break
                    except json.JSONDecodeError:
                        pass

        passed = (rc == 0) and bool(parsed.get("ok", True))
        return HookExecutionResult(
            event=event,
            command=command,
            script_rel=script_rel,
            passed=passed,
            returncode=rc if passed else (rc or 1),
            stdout=out_str,
            stderr=err_str,
            parsed_output=parsed,
        )


class GodViewSubstrate(Substrate):
    """God-View Court Agent substrate mapping H = (c1..c8) onto Court Agent runtime."""

    spec: SurfaceSpec = GODVIEW_SURFACE

    def __init__(
        self,
        domain: str = "application",
        *,
        base_tree_dir: Path | None = None,
        install_mode: InstallMode = "native",
    ) -> None:
        self.domain = domain
        self.install_mode: InstallMode = install_mode
        self._base_tree_dir = base_tree_dir

    def base_tree(self) -> Path:
        if self._base_tree_dir is not None:
            return self._base_tree_dir
        repo_base = Path(__file__).resolve().parent.parent / "agents" / "base_trees" / self.domain
        return repo_base

    def compile_system_instruction(self, tree: HarnessTree) -> str:
        """Assemble Always-on Context (c1..c4) into a single system instruction string."""
        ao = tree.always_on_text()
        sections: list[str] = []
        if self.install_mode == "projected":
            sections.append(prompts.PROJECTED_PREAMBLE.strip())
        labels = {
            "c1": "ROLE & SYSTEM PROMPT (c1)",
            "c2": "GUARDRAILS & SAFETY POLICY (c2)",
            "c3": "LONG-TERM ARCHITECTURAL MEMORY (c3)",
            "c4": "TOOL DESCRIPTIONS & GUIDANCE (c4)",
        }
        for cat in ("c1", "c2", "c3", "c4"):
            body = ao.get(cat, "").strip()  # type: ignore[arg-type]
            if body:
                sections.append(f"## {labels[cat]}\n{body}")
        return "\n\n".join(sections)
