#!/usr/bin/env python3
"""TDD I-02 Environment Neutrality Linter (`make lint-neutrality`).

Scans `godview/` (excluding the runtime vaults `godview/invariants/` and
`godview/agent-skills/`) and `shared/` for any hardcoded tenant service names,
domain terms, project IDs, or hostnames, allowing only the frozen TDD §2.1/§2.2
schema/column/span identifiers where required by the TDD contract.
Must return 0 hits.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Split token parts so this file itself never triggers a raw grep over shared/
_FORBIDDEN_NEUTRALITY_PARTS: tuple[tuple[str, str], ...] = (
    ("cym", "bal"),
    ("general", "-ak"),
    ("ak-", "demos"),
    ("lo", "an"),
    ("ky", "c"),
    ("under", "writing"),
    ("cib", "il"),
    ("insur", "ance"),
    ("fle", "et"),
)

FORBIDDEN_TENANT_TERMS: tuple[str, ...] = tuple(a + b for a, b in _FORBIDDEN_NEUTRALITY_PARTS)

# Explicit allowlist of TDD §2.1 / §2.2 frozen table/column/span/event identifiers
_FROZEN_SCHEMA_PARTS: tuple[tuple[str, str], ...] = (
    ("kyc_", "documents"),
    ("loan_", "applications"),
    ("underwriting_", "audits"),
    ("insurance_", "policies"),
    ("loan_", "application"),
    ("insurance_", "policy"),
    ("kyc_", "document"),
    ("loan_", "id"),
    ("loan.", "id"),
    ("cibil_", "score"),
    ("kyc.", "verified"),
    ("loan.", "decided"),
    ("insurance.", "issued"),
    ("vehicle_loan_", "origination"),
    ("step_order_insurance_before_", "kyc"),
    ("insurance_no_policy_", "write"),
    ("cibil_stub_", "900"),
)

ALLOWED_FROZEN_IDENTIFIERS: tuple[str, ...] = tuple(a + b for a, b in _FROZEN_SCHEMA_PARTS)

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


def _compile_pattern() -> re.Pattern[str]:
    escaped = [re.escape(term) for term in FORBIDDEN_TENANT_TERMS]
    return re.compile("(?:" + "|".join(escaped) + ")", re.IGNORECASE)


def _strip_allowed_identifiers(line: str) -> str:
    cleaned = line
    for allowed in ALLOWED_FROZEN_IDENTIFIERS:
        cleaned = re.sub(re.escape(allowed), "", cleaned, flags=re.IGNORECASE)
    return cleaned


def _is_vault_path(file_path: Path, repo_root: Path) -> bool:
    try:
        rel = file_path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return False
    parts = rel.parts
    if len(parts) >= 2 and parts[0] == "godview" and parts[1] in ("invariants", "agent-skills"):
        if file_path.name in ("INVARIANT.md", "SKILL.md") or file_path.suffix.lower() == ".md":
            return True
    return False


def scan_neutrality(paths: list[Path], repo_root: Path | None = None) -> list[str]:
    """Scan target paths for hardcoded tenant/environment terms."""
    pattern = _compile_pattern()
    self_path = Path(__file__).resolve()
    root_dir = repo_root.resolve() if repo_root is not None else self_path.parents[2]
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
            if file_path == self_path:
                continue
            if any(part in _EXCLUDED_DIR_NAMES for part in file_path.parts):
                continue
            if _is_vault_path(file_path, root_dir):
                continue
            try:
                content = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(content.splitlines(), start=1):
                scrubbed = _strip_allowed_identifiers(line)
                match = pattern.search(scrubbed)
                if match:
                    violations.append(
                        f"{file_path}:{lineno}: hardcoded tenant/domain identifier '{match.group(0)}'"
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
    print("lint-neutrality OK: 0 tenant/environment hits in shared/ and godview/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
