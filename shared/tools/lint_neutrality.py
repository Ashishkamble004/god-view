#!/usr/bin/env python3
"""Strict Environment Neutrality Linter.

Scans target trees for hardcoded tenant service names, domain terms,
environment project IDs, buckets, hostnames, ports, IP prefixes, and finance/lending stems.

Zero allowlist. Returns 0 hits when codebase is fully neutral.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Word stems for finance/lending domain concepts.
# Split into parts so this script source does not match raw substrings.
_BANNED_STEMS_PARTS: tuple[tuple[str, str], ...] = (
    ("lo", "an"),
    ("disb", "urs"),
    ("rup", "ee"),
    ("le", "nd"),
    ("borr", "ow"),
    ("ky", "c"),
    ("under", "writ"),
    ("ins", "ur"),
    ("sanct", "ion"),
    ("vehic", "le"),
    ("cib", "il"),
    ("fo", "ir"),
    ("amort", "iz"),
    ("install", "ment"),
    ("ten", "ure"),
    ("principal_", "amount"),
    ("aad", "haar"),
)

# Currency symbols or specific tokens to strictly disallow
_BANNED_SYMBOLS: tuple[str, ...] = (
    "\u20b9",  # Rupee sign ₹
)

# Acronyms where prefix/suffix boundary is needed to avoid false positives (e.g. emit, emitter)
_BANNED_ACRONYMS_PARTS: tuple[tuple[str, str], ...] = (
    ("em", "i"),
    ("in", "r"),
)

# Known environment identifiers, tenant services, IP prefixes, and infrastructure identifiers
_BANNED_ENV_IDENTIFIERS_PARTS: tuple[tuple[str, str], ...] = (
    ("cym", "bal"),
    ("general", "-ak"),
    ("ak-", "demos"),
    ("76900298", "5772"),
    ("altostrat", ".com"),
    ("godview", "_lms"),
    ("god-view-edge-", "policy"),
    ("loan-orchestrator-", "api"),
    ("customer-loan-portal-", "web"),
    ("vehicle-fleet-catalog-", "service"),
    ("underwriting-los-", "svc"),
    ("motor-insurance-", "svc"),
    ("core-banking-cibil-", "vm"),
    ("10.", "10."),
    ("10.", "128."),
    ("34.", "120."),
    ("80", "85"),
)

BANNED_STEMS: tuple[str, ...] = tuple(a + b for a, b in _BANNED_STEMS_PARTS)
BANNED_ACRONYMS: tuple[str, ...] = tuple(a + b for a, b in _BANNED_ACRONYMS_PARTS)
BANNED_ENV_IDENTIFIERS: tuple[str, ...] = tuple(a + b for a, b in _BANNED_ENV_IDENTIFIERS_PARTS)
BANNED_SYMBOLS: tuple[str, ...] = _BANNED_SYMBOLS
BANNED_TERMS: tuple[str, ...] = BANNED_STEMS + BANNED_ACRONYMS + BANNED_ENV_IDENTIFIERS + BANNED_SYMBOLS

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


def compile_pattern() -> re.Pattern[str]:
    patterns: list[str] = []
    # Stems: preceded by word boundary / non-alphanumeric, matched as a prefix of inflected forms
    for stem in BANNED_STEMS:
        patterns.append(rf"(?<![a-zA-Z0-9]){re.escape(stem)}")

    # Acronyms: bounded so they match EMI, emi, emi_amount, emis, but not emit or gemini
    for acr in BANNED_ACRONYMS:
        patterns.append(rf"(?<![a-zA-Z0-9]){re.escape(acr)}(?=[_sS\b]|\Z)")

    # Environment identifiers
    for ident in BANNED_ENV_IDENTIFIERS:
        patterns.append(re.escape(ident))

    # Explicit banned symbols (e.g. currency signs)
    for sym in BANNED_SYMBOLS:
        patterns.append(re.escape(sym))

    return re.compile("|".join(patterns), re.IGNORECASE)


def scan_neutrality(
    paths: list[Path],
    repo_root: Path | None = None,
) -> list[str]:
    """Strictly scan target paths for hardcoded tenant/environment terms. Zero allowlist."""
    pattern = compile_pattern()
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
            # Only self-exemption allowed: this linter script itself
            if file_path == self_path:
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
