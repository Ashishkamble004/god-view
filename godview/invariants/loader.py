"""INVARIANT.md frontmatter parser, validator, and seed loader (TDD §2.4, Agent Spec §7, Design §6.2)."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import sqlglot
import yaml
from godview_shared.contracts import InvariantDoc, InvariantFrontmatter
from pydantic import ValidationError
from sqlglot import exp

INVARIANTS_ROOT: Path = Path(__file__).resolve().parent

_FRONTMATTER_RE = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n(.*))?\Z", re.DOTALL)

ALLOWED_PYTHON_FUNCTIONS: frozenset[str] = frozenset(
    {
        "abs",
        "min",
        "max",
        "round",
        "len",
        "float",
        "int",
        "bool",
    }
)

_FORBIDDEN_SQL_FUNCTIONS: frozenset[str] = frozenset(
    {
        "pg_terminate_backend",
        "pg_cancel_backend",
        "set_config",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "pg_sleep",
        "pg_promote",
        "pg_switch_wal",
    }
)

_FORBIDDEN_SQL_PREFIXES: tuple[str, ...] = (
    "lo_",
    "dblink",
    "pg_advisory_",
    "pg_backup_",
    "txid_",
)


def discover_invariant_names(
    journey_key: str | None = None,
    *,
    root_dir: Path | None = None,
) -> tuple[str, ...]:
    """Discover all invariant slugs under <root_dir>/[<journey_key>/]*/INVARIANT.md."""
    base = root_dir if root_dir is not None else INVARIANTS_ROOT
    target_dir = base / journey_key if journey_key else base
    if not target_dir.is_dir():
        return ()
    return tuple(sorted(p.parent.name for p in target_dir.glob("*/INVARIANT.md") if p.is_file()))


SEED_INVARIANT_NAMES: tuple[str, ...] = discover_invariant_names()


class InvariantValidationError(ValueError):
    """Raised when an INVARIANT.md file has invalid frontmatter or an unparseable check statement."""


def _validate_python_ast(tree: ast.Expression) -> None:
    """Ensure a Python invariant expression is a pure, side-effect-free boolean/arithmetic expression."""
    forbidden_node_types = (
        ast.Attribute,
        ast.Subscript,
        ast.Lambda,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
        ast.Await,
        ast.Yield,
        ast.YieldFrom,
        ast.NamedExpr,
    )
    for node in ast.walk(tree):
        if isinstance(node, forbidden_node_types):
            raise InvariantValidationError(
                f"Forbidden Python AST construct '{type(node).__name__}' in invariant check.statement"
            )
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise InvariantValidationError(
                f"Forbidden dunder/private identifier '{node.id}' in invariant check.statement"
            )
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_PYTHON_FUNCTIONS:
                func_name = (
                    node.func.id if isinstance(node.func, ast.Name) else type(node.func).__name__
                )
                raise InvariantValidationError(
                    f"Disallowed function call '{func_name}' in Python invariant (allowed: {sorted(ALLOWED_PYTHON_FUNCTIONS)})"
                )


def _validate_sql_read_only(parsed: exp.Expression, dialect: str) -> None:
    """Reject locking clauses, SELECT INTO, and mutating/administrative SQL functions."""
    for node in parsed.walk():
        if isinstance(node, (exp.Lock, exp.Into, exp.Command, exp.DDL, exp.DML)):
            raise InvariantValidationError(
                f"Invariant {dialect} query must be strictly read-only (disallowed construct: {type(node).__name__})"
            )
        if isinstance(node, exp.Func):
            fn_name = (node.sql_name() if hasattr(node, "sql_name") else "").lower()
            if isinstance(node, exp.Anonymous) and node.name:
                fn_name = node.name.lower()
            if fn_name in _FORBIDDEN_SQL_FUNCTIONS or any(
                fn_name.startswith(p) for p in _FORBIDDEN_SQL_PREFIXES
            ):
                raise InvariantValidationError(
                    f"Disallowed mutating/administrative SQL function '{fn_name}' in invariant check.statement"
                )


def validate_check_statement_syntax(dialect: str, statement: str) -> None:
    """Validate SQL or Python check syntax using sqlglot or ast.parse."""
    stmt = (statement or "").strip()
    if not stmt:
        raise InvariantValidationError("Invariant check.statement must not be empty")

    if dialect in ("bigquery", "postgres"):
        try:
            statements = sqlglot.parse(
                stmt,
                read=dialect,
                error_level=sqlglot.ErrorLevel.RAISE,
            )
        except Exception as exc:
            raise InvariantValidationError(
                f"Invalid {dialect} SQL in invariant check.statement: {exc}"
            ) from exc
        if len(statements) != 1 or statements[0] is None:
            raise InvariantValidationError(
                f"Invariant {dialect} statement must be a single SELECT query (got {len(statements)} statements)"
            )
        parsed = statements[0]
        if not isinstance(parsed, (exp.Select, exp.Union, exp.Subquery)):
            raise InvariantValidationError(
                f"Invariant {dialect} statement must be a SELECT query (got {type(parsed).__name__})"
            )
        _validate_sql_read_only(parsed, dialect)
        return

    if dialect == "python":
        try:
            tree = ast.parse(stmt, mode="eval")
        except SyntaxError as exc:
            raise InvariantValidationError(
                f"Invalid Python expression syntax in invariant check.statement: {exc}"
            ) from exc
        _validate_python_ast(tree)
        return

    raise InvariantValidationError(f"Unsupported invariant check dialect '{dialect}'")


def parse_invariant_markdown(
    content: str,
    *,
    git_path: str | None = None,
    git_sha: str | None = None,
) -> InvariantDoc:
    """Parse and validate an INVARIANT.md string into an InvariantDoc."""
    match = _FRONTMATTER_RE.match(content.strip())
    if not match:
        raise InvariantValidationError(
            "INVARIANT.md must begin with YAML frontmatter delimited by '---'"
        )
    raw_yaml, body_md = match.group(1), (match.group(2) or "").strip()

    try:
        parsed_yaml: dict[str, Any] = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        raise InvariantValidationError(f"Invalid YAML in INVARIANT.md frontmatter: {exc}") from exc

    if not isinstance(parsed_yaml, dict):
        raise InvariantValidationError("INVARIANT.md frontmatter must deserialize to a mapping")

    try:
        frontmatter = InvariantFrontmatter.model_validate(parsed_yaml)
    except ValidationError as exc:
        raise InvariantValidationError(
            f"INVARIANT.md frontmatter failed schema validation: {exc}"
        ) from exc

    validate_check_statement_syntax(frontmatter.check.dialect, frontmatter.check.statement)

    return InvariantDoc(
        doc_id=f"inv-{frontmatter.name}",
        sentinel_id=f"snt-invariant_sql-{frontmatter.name}",
        body_md=body_md,
        git_path=git_path,
        git_sha=git_sha,
        **frontmatter.model_dump(),
    )


def load_invariant_file(
    path: Path | str,
    *,
    git_sha: str | None = None,
) -> InvariantDoc:
    """Read, parse, and validate an INVARIANT.md file from disk."""
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"INVARIANT.md file not found: {file_path}")
    text = file_path.read_text(encoding="utf-8")
    return parse_invariant_markdown(text, git_path=str(file_path), git_sha=git_sha)


def resolve_invariant_path(
    name: str,
    *,
    journey_key: str | None = None,
    root_dir: Path | None = None,
) -> Path:
    """Resolve the filesystem path for a named invariant under <root_dir>/[<journey_key>/]<name>/INVARIANT.md."""
    base = root_dir if root_dir is not None else INVARIANTS_ROOT
    if journey_key:
        direct = base / journey_key / name / "INVARIANT.md"
    else:
        direct = base / name / "INVARIANT.md"
    if direct.is_file():
        return direct
    matches = list(base.glob(f"**/{name}/INVARIANT.md"))
    if matches:
        return matches[0]
    return direct


def load_seed_invariants(
    journey_key: str | None = None,
    *,
    root_dir: Path | None = None,
) -> list[InvariantDoc]:
    """Load and validate all seed invariants discovered under the given journey_key or root_dir."""
    slugs = discover_invariant_names(journey_key=journey_key, root_dir=root_dir)
    return [
        load_invariant_file(
            resolve_invariant_path(slug, journey_key=journey_key, root_dir=root_dir)
        )
        for slug in slugs
    ]
