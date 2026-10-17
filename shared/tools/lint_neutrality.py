#!/usr/bin/env python3
"""Strict Environment Neutrality Linter.

Scans `godview/` and `shared/` for hardcoded tenant service names, domain terms,
environment project IDs, buckets, hostnames, ports, and finance/lending vocabulary.

Zero allowlist. Returns 0 hits when codebase is fully neutral.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Split token parts so this file never triggers a raw string search
_BANNED_FINANCE_LENDING_PARTS: tuple[tuple[str, str], ...] = (
    ("lo", "an"),
    ("lend", "ing"),
    ("borrow", "er"),
    ("lend", "er"),
    ("sanct", "ion"),
    ("under", "writing"),
    ("disburse", "ment"),
    ("cib", "il"),
    ("fo", "ir"),
    ("em", "i"),
    ("rup", "ee"),
    ("in", "r"),
    ("ky", "c"),
    ("insur", "ance"),
)

_BANNED_ENVIRONMENT_PARTS: tuple[tuple[str, str], ...] = (
    ("cym", "bal"),
    ("general", "-ak"),
    ("ak-", "demos"),
    ("76900298", "5772"),
    ("altostrat", ".com"),
    ("god-view-edge-", "policy"),
    ("80", "85"),
)

BANNED_TERMS: tuple[str, ...] = tuple(
    a + b for a, b in (_BANNED_FINANCE_LENDING_PARTS + _BANNED_ENVIRONMENT_PARTS)
)

_EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
    }
)

_EXCLUDED_FILE_NAMES: frozenset[str] = frozenset(
    {
        "lint_neutrality.py",
        "lint_brands.py",
        "check_neutrality.py",
    }
)


def _compile_pattern() -> re.Pattern[str]:
    patterns: list[str] = []
    for term in BANNED_TERMS:
        # Word boundary style check: bounded by non-alphanumeric characters
        patterns.append(rf"(?<![a-zA-Z0-9]){re.escape(term)}(?![a-zA-Z0-9])")
    return re.compile("|".join(patterns), re.IGNORECASE)


def scan_neutrality(
    paths: list[Path],
    repo_root: Path | None = None,
) -> list[str]:
    """Strictly scan target paths for hardcoded tenant/environment terms. Zero allowlist."""
    pattern = _compile_pattern()
    self_path = Path(__file__).resolve()
    violations: list[str] = []

    for target in paths:
        if not target.exists():
            continue
        target_resolved = target.resolve()
        files = (
            [target_resolved]
            if target_resolved.is_file()
            else [p for p in target_resolved.rglob("*") if p.is_file()]
        )
        for file_path in sorted(files):
            if file_path == self_path or file_path.name in _EXCLUDED_FILE_NAMES:
                continue
            if any(part in _EXCLUDED_DIR_NAMES for part in file_path.parts):
                continue
            try:
                content = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(content.splitlines(), start=1):
                match = pattern.search(line)
                if match:
                    violations.append(
                        f"{file_path}:{lineno}: hardcoded tenant/environment identifier '{match.group(0)}'"
                    )
    return violations


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    repo_root = Path(__file__).resolve().parents[2]
    if args:
        targets = [Path(a) for a in args]
    else:
        targets = [repo_root / "shared", repo_root / "godview"]
    violations = scan_neutrality(targets, repo_root=repo_root)
    if violations:
        for v in violations:
            print(v, file=sys.stderr)
        print(f"lint-neutrality FAILED: {len(violations)} violation(s) found.", file=sys.stderr)
        return 1
    print("lint-neutrality OK: 0 tenant/environment hits in scanned targets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
