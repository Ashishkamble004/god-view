"""INVARIANT.md compiler to SentinelDefinition (`snt-{class}-{slug}`) and lifecycle manager (TDD §2.4, Agent Spec §7.3, Decision A8)."""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from godview_shared.config import (
    DEFAULT_INVARIANT_AUTO_RETIREMENT_DAYS,
    DEFAULT_INVARIANT_SHADOW_WINDOW_HOURS,
    DEFAULT_SENTINEL_BASELINE_WINDOW_DAYS,
)
from godview_shared.contracts import (
    InvariantDoc,
    InvariantFrontmatter,
    SentinelBaseline,
    SentinelCheck,
    SentinelDefinition,
    SentinelState,
    SentinelWakeCondition,
)
from godview_shared.fakes import InMemoryBigQuery

from godview.invariants.loader import (
    _validate_python_ast,
    validate_check_statement_syntax,
)


class InvariantLifecycleError(ValueError):
    """Raised when an invariant lifecycle transition violates self-consistency, shadow window, or retirement rules."""


def _compare_threshold(value: float, operator: str, threshold: float) -> bool:
    if operator == ">=":
        return value >= threshold
    if operator == ">":
        return value > threshold
    if operator == "<=":
        return value <= threshold
    if operator == "<":
        return value < threshold
    if operator == "==":
        return value == threshold
    if operator == "!=":
        return value != threshold
    raise ValueError(f"Unsupported wake_condition operator '{operator}'")


class InvariantCompiler:
    """Compiles INVARIANT.md documents into 0-token SentinelDefinition checks and evaluates them across bigquery, postgres, and python dialects."""

    def __init__(self, bigquery: InMemoryBigQuery | None = None) -> None:
        self._bigquery = bigquery

    @property
    def bigquery(self) -> InMemoryBigQuery | None:
        return self._bigquery

    def compile(
        self,
        doc: InvariantDoc | InvariantFrontmatter,
        *,
        compiled_at: datetime | str | None = None,
    ) -> SentinelDefinition:
        """Compile an InvariantDoc or InvariantFrontmatter into a SentinelDefinition (`snt-invariant_sql-{slug}`)."""
        validate_check_statement_syntax(doc.check.dialect, doc.check.statement)

        state_map: dict[str, SentinelState] = {
            "proposed": "shadow",
            "shadow": "shadow",
            "enforced": "enforced",
            "retired": "retired",
        }
        sentinel_state = state_map.get(doc.status, "shadow")
        timestamp = compiled_at if compiled_at is not None else datetime.now(tz=UTC).isoformat()

        return SentinelDefinition(
            sentinel_id=f"snt-invariant_sql-{doc.name}",
            sentinel_class="invariant_sql",
            source_ref=f"inv-{doc.name}",
            check=SentinelCheck(
                dialect=doc.check.dialect,
                statement=doc.check.statement.strip(),
                window_s=doc.check.window_s,
            ),
            cadence_s=10,
            baseline=SentinelBaseline(
                model="same_hour",
                window_days=DEFAULT_SENTINEL_BASELINE_WINDOW_DAYS,
            ),
            wake_condition=SentinelWakeCondition(
                operator=doc.wake_condition.operator,
                threshold=doc.wake_condition.threshold,
                consecutive=doc.wake_condition.consecutive,
                window_s=doc.check.window_s,
                target_agent_id=doc.wake_condition.target_agent_id,
            ),
            target_agent_id=doc.wake_condition.target_agent_id,
            state=sentinel_state,
            compiled_by=doc.created_by,
            compiled_at=timestamp,
            last_run_at=None,
            last_result=None,
            consecutive_breaches=0,
        )

    def evaluate_sentinel(
        self,
        sentinel: SentinelDefinition,
        *,
        postgres_runner: Callable[[str], list[dict[str, Any]]] | None = None,
        python_rows: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Evaluate a compiled invariant sentinel against its target dialect (`bigquery`, `postgres`, or `python`)."""
        dialect = sentinel.check.dialect
        stmt = (sentinel.check.statement or "").strip()
        validate_check_statement_syntax(dialect, stmt)

        if dialect == "bigquery":
            if self._bigquery is None:
                raise RuntimeError(
                    "InMemoryBigQuery instance is required to evaluate bigquery sentinel"
                )
            return self._evaluate_bigquery(sentinel.sentinel_id, stmt)

        if dialect == "postgres":
            if postgres_runner is None:
                raise RuntimeError(
                    "postgres_runner callable is required to evaluate postgres sentinel"
                )
            rows = postgres_runner(stmt)
            first = rows[0] if rows else {}
            count = int(first.get("violations", first.get("count", 0)))
            return {"count": count, "violations": count}

        if dialect == "python":
            return self._evaluate_python(stmt, python_rows or ())

        raise ValueError(f"Unsupported sentinel check dialect '{dialect}'")

    def _evaluate_bigquery(self, sentinel_id: str, statement: str) -> dict[str, Any]:
        assert self._bigquery is not None
        res = self._bigquery.query(statement)
        first = res[0] if res else {}
        count = int(first.get("violations", first.get("count", 0)) or 0)
        return {"count": count, "violations": count}

    def _evaluate_python(
        self,
        statement: str,
        rows: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        tree = ast.parse(statement, mode="eval")
        _validate_python_ast(tree)
        code_obj = compile(tree, "<invariant>", "eval")
        violations = 0
        safe_builtins = {
            "abs": abs,
            "min": min,
            "max": max,
            "round": round,
            "len": len,
            "float": float,
            "int": int,
            "bool": bool,
        }
        for row in rows:
            local_env = {
                **safe_builtins,
                **{k: v for k, v in dict(row).items() if not str(k).startswith("_")},
            }
            passed = bool(eval(code_obj, {"__builtins__": {}}, local_env))
            if not passed:
                violations += 1
        return {"count": violations, "violations": violations}


class InvariantLifecycleManager:
    """Enforces the INVARIANT.md lifecycle (`proposed -> shadow -> enforced -> retired`) and shadow_stats (Agent Spec §7.3 & Decision A8)."""

    def __init__(
        self,
        compiler: InvariantCompiler,
        *,
        shadow_window_hours: float = float(DEFAULT_INVARIANT_SHADOW_WINDOW_HOURS),
        max_false_positive_rate: float = 0.05,
        min_enforced_confidence: float = 0.80,
        auto_retirement_days: float = float(DEFAULT_INVARIANT_AUTO_RETIREMENT_DAYS),
    ) -> None:
        self._compiler = compiler
        self._shadow_window_hours = shadow_window_hours
        self._max_false_positive_rate = max_false_positive_rate
        self._min_enforced_confidence = min_enforced_confidence
        self._auto_retirement_days = auto_retirement_days

    def promote_to_shadow(
        self,
        doc: InvariantDoc,
    ) -> tuple[InvariantDoc, SentinelDefinition]:
        """Transition `proposed -> shadow` after verifying syntax, scope resolution, and 0 baseline violations."""
        if doc.status != "proposed":
            raise InvariantLifecycleError(
                f"Only 'proposed' invariants can transition to 'shadow' (got '{doc.status}')"
            )
        if not (doc.scope.service or doc.scope.entity or doc.scope.journey):
            raise InvariantLifecycleError(
                "Invariant scope must resolve at least one of service, entity, or journey"
            )

        validate_check_statement_syntax(doc.check.dialect, doc.check.statement)
        sentinel = self._compiler.compile(doc)

        if doc.check.dialect == "bigquery" and self._compiler.bigquery is not None:
            baseline_res = self._compiler.evaluate_sentinel(sentinel)
            if baseline_res.get("violations", 0) > 0:
                raise InvariantLifecycleError(
                    f"Self-consistency check failed for '{doc.name}': dry-run on baseline returned {baseline_res['violations']} violations"
                )

        updated_doc = doc.model_copy(
            update={"status": "shadow", "sentinel_id": sentinel.sentinel_id}
        )
        updated_sentinel = sentinel.model_copy(update={"state": "shadow"})
        return updated_doc, updated_sentinel

    def record_evaluation(
        self,
        doc: InvariantDoc,
        sentinel: SentinelDefinition,
        *,
        violations: int,
        is_false_positive: bool = False,
    ) -> tuple[InvariantDoc, SentinelDefinition, bool]:
        """Record one sentinel evaluation tick, updating shadow_stats and returning (doc, sentinel, should_wake)."""
        now_iso = datetime.now(tz=UTC).isoformat()
        breached = _compare_threshold(
            float(violations),
            sentinel.wake_condition.operator,
            float(sentinel.wake_condition.threshold),
        )

        if doc.status == "shadow" or sentinel.state == "shadow":
            runs = doc.shadow_stats.runs + 1
            breaches = doc.shadow_stats.breaches + (1 if breached else 0)
            prev_fps = round(doc.shadow_stats.false_positive_rate * doc.shadow_stats.runs)
            new_fps = prev_fps + (1 if (breached and is_false_positive) else 0)
            fp_rate = (new_fps / runs) if runs > 0 else 0.0

            new_stats = doc.shadow_stats.model_copy(
                update={
                    "runs": runs,
                    "breaches": breaches,
                    "false_positive_rate": round(fp_rate, 4),
                }
            )
            updated_doc = doc.model_copy(update={"shadow_stats": new_stats})
            updated_sentinel = sentinel.model_copy(
                update={
                    "last_run_at": now_iso,
                    "last_result": {"count": violations, "violations": violations},
                    "consecutive_breaches": sentinel.consecutive_breaches + 1 if breached else 0,
                }
            )
            # Shadow invariants never wake an agent (Agent Spec §2.1 & §7.3)
            return updated_doc, updated_sentinel, False

        if doc.status == "enforced" and sentinel.state == "enforced":
            consec = (sentinel.consecutive_breaches + 1) if breached else 0
            should_wake = bool(breached and consec >= sentinel.wake_condition.consecutive)
            updated_sentinel = sentinel.model_copy(
                update={
                    "last_run_at": now_iso,
                    "last_result": {"count": violations, "violations": violations},
                    "consecutive_breaches": consec,
                }
            )
            return doc, updated_sentinel, should_wake

        return doc, sentinel, False

    def promote_to_enforced(
        self,
        doc: InvariantDoc,
        sentinel: SentinelDefinition,
        *,
        shadow_elapsed_hours: float,
    ) -> tuple[InvariantDoc, SentinelDefinition]:
        """Transition `shadow -> enforced` once the 24h shadow window elapsed, fp_rate < 5%, and confidence >= 0.8 (Decision A8)."""
        if doc.status != "shadow":
            raise InvariantLifecycleError(
                f"Only 'shadow' invariants can promote to 'enforced' (got '{doc.status}')"
            )
        if shadow_elapsed_hours < self._shadow_window_hours:
            raise InvariantLifecycleError(
                f"Shadow window of {self._shadow_window_hours}h has not elapsed (got {shadow_elapsed_hours}h)"
            )
        if doc.shadow_stats.false_positive_rate >= self._max_false_positive_rate:
            raise InvariantLifecycleError(
                f"False positive rate {doc.shadow_stats.false_positive_rate:.2%} >= {self._max_false_positive_rate:.2%}"
            )
        if doc.confidence < self._min_enforced_confidence:
            raise InvariantLifecycleError(
                f"Invariant confidence {doc.confidence} < required {self._min_enforced_confidence}"
            )

        updated_doc = doc.model_copy(update={"status": "enforced"})
        updated_sentinel = sentinel.model_copy(
            update={"state": "enforced", "consecutive_breaches": 0}
        )
        return updated_doc, updated_sentinel

    def demote_on_false_positive(
        self,
        doc: InvariantDoc,
        sentinel: SentinelDefinition,
    ) -> tuple[InvariantDoc, SentinelDefinition]:
        """Demote an `enforced` invariant back to `shadow` when an operator marks a breach as a false positive."""
        if doc.status != "enforced":
            raise InvariantLifecycleError(
                f"Only 'enforced' invariants can be demoted to 'shadow' (got '{doc.status}')"
            )
        new_stats = doc.shadow_stats.model_copy(
            update={
                "runs": 1,
                "breaches": 1,
                "false_positive_rate": 1.0,
            }
        )
        updated_doc = doc.model_copy(update={"status": "shadow", "shadow_stats": new_stats})
        updated_sentinel = sentinel.model_copy(
            update={"state": "shadow", "consecutive_breaches": 0}
        )
        return updated_doc, updated_sentinel

    def retire(
        self,
        doc: InvariantDoc,
        sentinel: SentinelDefinition,
        *,
        days_without_breach: float = 0.0,
        owner_confirmed: bool = False,
        provenance_gone: bool = False,
    ) -> tuple[InvariantDoc, SentinelDefinition]:
        """Transition `shadow` or `enforced` -> `retired` (Decision A8: 30d without breach AND owner confirmation, or provenance gone)."""
        if doc.status not in ("shadow", "enforced"):
            raise InvariantLifecycleError(f"Cannot retire invariant from status '{doc.status}'")
        if not provenance_gone:
            if days_without_breach < self._auto_retirement_days:
                raise InvariantLifecycleError(
                    f"Auto-retirement requires >= {self._auto_retirement_days} days without a breach (got {days_without_breach})"
                )
            if not owner_confirmed:
                raise InvariantLifecycleError(
                    "Auto-retirement after 30 days requires explicit owner confirmation (Decision A8)"
                )

        updated_doc = doc.model_copy(update={"status": "retired"})
        updated_sentinel = sentinel.model_copy(
            update={"state": "retired", "consecutive_breaches": 0}
        )
        return updated_doc, updated_sentinel
