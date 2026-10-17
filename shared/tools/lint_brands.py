#!/usr/bin/env python3
"""TDD I-01 Brand Cleanliness Linter (`make lint-brands`).

Scans the repository for forbidden competitor, APM-vendor, brokerage, real-bank,
or real-automaker brand names. Excludes `.venv/`, `node_modules/`, `plans/`
(historical design notes), and build caches.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Split token parts so this file itself never matches a raw grep over the repo
_FORBIDDEN_BRAND_PARTS: tuple[tuple[str, str], ...] = (
    ("app", "dynamics"),
    ("dyna", "trace"),
    ("data", "dog"),
    ("new", "relic"),
    ("new ", "relic"),
    ("spl", "unk"),
    ("pager", "duty"),
    ("grafana ", "labs"),
    ("zero", "dha"),
    ("gro", "ww"),
    ("ups", "tox"),
    ("angel ", "one"),
    ("robin", "hood"),
    ("hd", "fc"),
    ("ici", "ci"),
    ("kot", "ak"),
    ("id", "fc"),
    ("baj", "aj"),
    ("mahin", "dra"),
    ("open", "ai"),
    ("anthro", "pic"),
)

FORBIDDEN_BRANDS: tuple[str, ...] = tuple(a + b for a, b in _FORBIDDEN_BRAND_PARTS)

_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".terraform",
        "dist",
        "build",
        "plans",
    }
)

_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".md",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".json",
        ".yaml",
        ".yml",
        ".tf",
        ".tfvars",
        ".tftpl",
        ".sql",
        ".sh",
        ".toml",
        ".html",
        ".css",
    }
)


def _compile_pattern() -> re.Pattern[str]:
    escaped = [re.escape(brand) for brand in FORBIDDEN_BRANDS]
    return re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)


def scan_paths(paths: list[Path]) -> list[str]:
    """Scan paths for forbidden brands and return formatted violation lines."""
    pattern = _compile_pattern()
    self_path = Path(__file__).resolve()
    violations: list[str] = []

    for root in paths:
        root_resolved = root.resolve()
        if root_resolved.is_file():
            files = [root_resolved]
        else:
            files = [p for p in root_resolved.rglob("*") if p.is_file()]

        for file_path in sorted(files):
            if file_path == self_path:
                continue
            rel_parts = (
                set(file_path.relative_to(root_resolved).parts)
                if file_path != root_resolved
                else set()
            )
            if rel_parts & _EXCLUDED_DIRS:
                continue
            if file_path.suffix.lower() not in _TEXT_EXTENSIONS:
                continue
            try:
                content = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(content.splitlines(), start=1):
                match = pattern.search(line)
                if match:
                    violations.append(f"{file_path}:{lineno}: forbidden brand '{match.group(0)}'")
    return violations


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    repo_root = Path(__file__).resolve().parents[2]
    targets = [Path(a) for a in args] if args else [repo_root]
    violations = scan_paths(targets)
    if violations:
        for v in violations:
            print(v, file=sys.stderr)
        print(f"lint-brands FAILED: {len(violations)} violation(s) found.", file=sys.stderr)
        return 1
    print("lint-brands OK: 0 forbidden brand hits.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
