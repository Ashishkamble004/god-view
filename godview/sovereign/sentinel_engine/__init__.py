"""0-Token Deterministic Sentinel Engine (`godview.sovereign.sentinel_engine`).

Implements TDD §2.4, §3.3 & §4:
- Evaluates universal sentinel check classes (lb_requests, posture, probe, pubsub_health,
  golden_signals, journey_conformance, kpi_changepoint, invariant) + legacy classes
  on a 10 s cadence (throttled to 60 s in Hibernate mode) at 0 LLM tokens.
- Per-tick evaluation budget enforcement (default 10.0 s).
- Per-sentinel error isolation (one failing check never blocks the tick).
- Excludes synthetic probe rows (`source != 'probe'`) from business SQL invariants.
- Enforces shadow vs enforced invariant semantics and suppresses wakes during planned
  operator actions (Kill Switch, Scheduled Pause, Hibernate).
- Delegates incident lifecycle and evidence assembly to `IncidentManager`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from godview_shared.contracts import (
    EvidencePack,
    SentinelDefinition,
    SentinelState,
)
from pydantic import BaseModel, Field

from godview.sovereign.court_manager.token_governor import TokenBudgetGovernor
from godview.sovereign.incidents.manager import IncidentManager
from godview.sovereign.nervous_system import NervousSystemRouter
from godview.sovereign.sentinel_engine.invoker import CapabilityInvoker
from godview.sovereign.sentinels.evaluators import (
    EvaluatorContext,
    get_evaluator,
)
from godview.sovereign.trust_ladder import TrustGate

logger = logging.getLogger(__name__)


class SentinelTickReport(BaseModel):
    """Summary of a single deterministic SentinelEngine tick."""

    evaluated_count: int = 0
    wakes_sent: list[EvidencePack] = Field(default_factory=list)
    shadow_breaches: int = 0
    suppressed_by_operator_window: int = 0
    errors: list[dict[str, str]] = Field(default_factory=list)


def _compare(observed: float, operator: str, threshold: float) -> bool:
    if operator == ">":
        return observed > threshold
    if operator == ">=":
        return observed >= threshold
    if operator == "<":
        return observed < threshold
    if operator == "<=":
        return observed <= threshold
    if operator == "==":
        return observed == threshold
    if operator == "!=":
        return observed != threshold
    raise ValueError(f"Unsupported wake operator '{operator}'")


class SentinelEngine:
    """Deterministic 0-token Sentinel Engine running inside Sovereign."""

    def __init__(
        self,
        firestore: Any | None = None,
        bigquery: Any | None = None,
        pubsub: Any | None = None,
        agent_engine: Any | None = None,
        *,
        ctx: Any | None = None,
        capability_invoker: CapabilityInvoker | None = None,
        incident_manager: IncidentManager | None = None,
        token_governor: TokenBudgetGovernor | None = None,
        trust_gate: TrustGate | None = None,
        embed_fn: Callable[[str], list[float]] | None = None,
        clock: Callable[[], datetime] | None = None,
        budget_s: float = 10.0,
    ) -> None:
        self._ctx = ctx
        self._firestore = firestore or getattr(ctx, "docs", None)
        self._bigquery = bigquery or getattr(ctx, "ledger", None)
        self._pubsub = pubsub or getattr(ctx, "bus", None)
        self._agent_engine = agent_engine or getattr(ctx, "agents", None)
        self._token_governor = token_governor
        self._trust_gate = trust_gate
        self._embed_fn = embed_fn
        self._clock = clock or getattr(ctx, "clock", None) or (lambda: datetime.now(tz=UTC))
        self.budget_s = budget_s
        self.cadence_s: int = 10
        self._planned_operator_action_active: bool = False
        self._planned_operator_action_reason: str | None = None

        if self._pubsub is not None:
            self._router = NervousSystemRouter(pubsub=self._pubsub)
        else:
            self._router = None

        adapters = getattr(ctx, "adapters", []) if ctx else []
        self._invoker = capability_invoker or CapabilityInvoker(adapters=adapters)

        self.incident_manager = incident_manager or IncidentManager(
            ctx=ctx,
            firestore=self._firestore,
            bigquery=self._bigquery,
            bus=self._pubsub,
            agents=self._agent_engine,
            hooks=getattr(ctx, "hooks", None),
            token_governor=self._token_governor,
            trust_gate=self._trust_gate,
            clock=self._clock,
            embed_fn=self._embed_fn,
        )

        self._eval_context = EvaluatorContext(
            invoker=self._invoker,
            firestore=self._firestore,
            bigquery=self._bigquery,
            clock=self._clock,
        )

    @property
    def invoker(self) -> CapabilityInvoker:
        return self._invoker

    def register_sentinel(self, sentinel: SentinelDefinition) -> SentinelDefinition:
        """Persist a compiled SentinelDefinition to Firestore `sentinels`."""
        if self._firestore is not None and hasattr(self._firestore, "set"):
            self._firestore.set(
                "sentinels",
                sentinel.sentinel_id,
                sentinel.model_dump(mode="json", by_alias=True),
            )
        return sentinel

    def list_sentinels(self) -> list[SentinelDefinition]:
        """Return all registered sentinels from Firestore."""
        if self._firestore is None or not hasattr(self._firestore, "list_docs"):
            return []
        docs = self._firestore.list_docs("sentinels")
        return [SentinelDefinition.model_validate(d) for d in docs]

    def update_sentinel_state(self, sentinel_id: str, state: SentinelState) -> SentinelDefinition:
        """Update a sentinel's lifecycle state (`shadow` | `enforced` | `paused` | `retired`)."""
        raw = self._firestore.get("sentinels", sentinel_id) if self._firestore else None
        if raw is None:
            raise KeyError(f"Unknown sentinel_id '{sentinel_id}'")
        snt = SentinelDefinition.model_copy(
            SentinelDefinition.model_validate(raw), update={"state": state}
        )
        return self.register_sentinel(snt)

    def set_planned_operator_action(self, *, active: bool, reason: str | None = None) -> None:
        """Suppress traffic-drop / expected wakes during planned operator windows (UC-18, UC-19, UC-20)."""
        self._planned_operator_action_active = active
        self._planned_operator_action_reason = reason

    def set_hibernate(self, enabled: bool) -> int:
        """Throttle sentinel cadence to 60 s in Hibernate mode; restore 10 s on wake (UC-20)."""
        self.cadence_s = 60 if enabled else 10
        return self.cadence_s

    async def evaluate_single_sentinel(self, sentinel_id: str) -> Any:
        """Evaluate a specific sentinel by ID on demand."""
        raw = self._firestore.get("sentinels", sentinel_id) if self._firestore else None
        if raw is None:
            raise KeyError(f"Unknown sentinel_id '{sentinel_id}'")
        snt = SentinelDefinition.model_validate(raw)
        evaluator = get_evaluator(snt.sentinel_class)
        return await evaluator.evaluate(snt, self._eval_context)

    async def evaluate_tick(self, budget_s: float | None = None) -> SentinelTickReport:
        """Run one deterministic 0-token evaluation pass across all active sentinels."""
        now = self._clock()
        now_iso = now.isoformat()
        report = SentinelTickReport()
        start_time = time.monotonic()
        effective_budget = budget_s or self.budget_s

        if hasattr(self._eval_context.invoker, "clear_tick_cache"):
            self._eval_context.invoker.clear_tick_cache()

        active_sentinels = [s for s in self.list_sentinels() if s.state not in ("paused", "retired")]
        # Rotate / sort to evaluate least recently run sentinels first to avoid starving tail sentinels
        active_sentinels.sort(key=lambda s: s.last_run_at or "")

        universal_classes = {
            "lb_requests",
            "posture",
            "probe",
            "pubsub_health",
            "golden_signals",
            "journey_conformance",
            "kpi_changepoint",
            "invariant",
        }

        for snt in active_sentinels:
            # Check cadence: sentinels with cadence > loop cadence only run when due
            if snt.last_run_at and snt.cadence_s > self.cadence_s:
                try:
                    last_run_dt = datetime.fromisoformat(snt.last_run_at)
                    if (now - last_run_dt).total_seconds() < snt.cadence_s:
                        continue
                except Exception:
                    pass

            # Check tick budget
            if (time.monotonic() - start_time) > effective_budget:
                logger.warning(
                    "Sentinel tick budget exceeded (%.2fs); yielding remaining checks",
                    effective_budget,
                )
                break

            report.evaluated_count += 1

            # Per-sentinel error isolation: catch and isolate evaluator exceptions
            try:
                evaluator = get_evaluator(snt.sentinel_class)
                eval_res = await evaluator.evaluate(snt, self._eval_context)
            except Exception as exc:
                logger.exception("Error evaluating sentinel '%s': %s", snt.sentinel_id, exc)
                report.errors.append({"sentinel_id": snt.sentinel_id, "error": str(exc)})
                self.register_sentinel(
                    snt.model_copy(
                        update={
                            "last_run_at": now_iso,
                            "last_result": {"error": str(exc), "breached": False},
                        }
                    )
                )
                continue

            observed = eval_res.observed
            if snt.sentinel_class in universal_classes:
                breached = eval_res.breached
            else:
                breached = eval_res.breached or _compare(
                    observed, snt.wake_condition.operator, snt.wake_condition.threshold
                )

            is_unknown = (
                eval_res.details.get("status") in ("unknown", "error")
                or bool(eval_res.details.get("error"))
            )

            if not breached:
                if snt.consecutive_breaches != 0 or snt.last_run_at != now_iso:
                    self.register_sentinel(
                        snt.model_copy(
                            update={
                                "consecutive_breaches": 0,
                                "last_run_at": now_iso,
                                "last_result": {
                                    "observed": observed,
                                    "breached": False,
                                    "details": eval_res.details,
                                },
                            }
                        )
                    )
                # Auto-close tracking via IncidentManager: ONLY if evaluation confirmed healthy (not unknown)
                if not is_unknown:
                    await self.incident_manager.record_clear(snt, now, resource_id=eval_res.resource_id)
                continue

            # Handle shadow mode
            if snt.state == "shadow":
                report.shadow_breaches += 1
                self.register_sentinel(
                    snt.model_copy(
                        update={
                            "consecutive_breaches": snt.consecutive_breaches + 1,
                            "last_run_at": now_iso,
                            "last_result": {
                                "observed": observed,
                                "breached": True,
                                "shadow": True,
                                "details": eval_res.details,
                            },
                        }
                    )
                )
                if self._router is not None:
                    await self._router.emit(
                        "godview.sentinel.breach",
                        source="//sovereign/sentinel-engine",
                        subject=snt.sentinel_id,
                        data={
                            "sentinel_id": snt.sentinel_id,
                            "shadow": True,
                            "observed": observed,
                        },
                    )
                continue

            # Handle planned operator window suppression
            if self._planned_operator_action_active:
                report.suppressed_by_operator_window += 1
                continue

            # Enforced breach tracking
            new_consecutive = snt.consecutive_breaches + 1
            updated_snt = self.register_sentinel(
                snt.model_copy(
                    update={
                        "consecutive_breaches": new_consecutive,
                        "last_run_at": now_iso,
                        "last_result": {
                            "observed": observed,
                            "breached": True,
                            "signature": eval_res.signature,
                            "details": eval_res.details,
                        },
                    }
                )
            )

            # Wake condition satisfied
            if new_consecutive >= snt.wake_condition.consecutive:
                incident, was_opened = await self.incident_manager.record_breach(
                    updated_snt, eval_res, now
                )

                evp = incident.evidence_pack
                if isinstance(evp, dict):
                    evp = EvidencePack.model_validate(evp)

                # Decorate with token governor and trust snapshot if available
                if self._token_governor is not None:
                    tokens_rem = self._token_governor.get_remaining_tokens(snt.target_agent_id)
                    can_esc = self._token_governor.can_escalate_to_argon(snt.target_agent_id)
                    evp.budget.agent_tokens_remaining = tokens_rem
                    evp.budget.escalation_allowed = can_esc

                if self._trust_gate is not None:
                    trust_snap = {
                        rec.action_type: rec.level
                        for rec in self._trust_gate.list_agent_records(snt.target_agent_id)
                    }
                    evp.trust_snapshot = trust_snap

                # Maintain backward compatibility with A2A / router / agent records
                if self._router is not None:
                    await self._router.emit(
                        "godview.sentinel.breach",
                        source="//sovereign/sentinel-engine",
                        subject=snt.sentinel_id,
                        incident_id=incident.doc_id,
                        data={
                            "sentinel_id": snt.sentinel_id,
                            "shadow": False,
                            "observed": observed,
                            "evidence_pack_id": evp.evidence_pack_id,
                        },
                    )


                if was_opened:
                    if self._firestore is not None and hasattr(self._firestore, "get"):
                        agent_raw = self._firestore.get("court_agents", snt.target_agent_id)
                        if agent_raw is not None:
                            self._firestore.set(
                                "court_agents",
                                snt.target_agent_id,
                                {
                                    "wakes_7d": int(agent_raw.get("wakes_7d", 0)) + 1,
                                    "last_wake_at": now_iso,
                                },
                                merge=True,
                            )
                    report.wakes_sent.append(evp)

        return report


def install(ctx: Any) -> SentinelEngine:
    """Wire up SentinelEngine and IncidentManager into SovereignContext lifecycle.

    - Creates SentinelEngine and IncidentManager bound to ctx if not already present.
    - Sets ctx.sentinel_engine and ctx.incident_manager.
    - Registers idempotent perception hook: auto-compiles and registers universal sentinels
      from discovered_services and discovered_edges in ctx.docs.
    - Registers sentinel_tick hook (ctx.hooks.sentinel_tick.append(engine.evaluate_tick)).
    - Registers on_change_event hook (ctx.hooks.on_change_event.append(engine.incident_manager.on_change_event)).
    """
    engine = getattr(ctx, "sentinel_engine", None)
    if engine is None:
        engine = SentinelEngine(
            ctx=ctx,
            firestore=getattr(ctx, "docs", None),
            bigquery=getattr(ctx, "ledger", None),
            pubsub=getattr(ctx, "bus", None),
            agent_engine=getattr(ctx, "agents", None),
            clock=getattr(ctx, "clock", None),
        )
        ctx.sentinel_engine = engine
        ctx.incident_manager = engine.incident_manager

    async def perception_sentinel_hook() -> None:
        docs = getattr(ctx, "docs", None)
        if docs is None or not hasattr(docs, "list_docs"):
            return
        try:
            services = docs.list_docs("discovered_services")
            edges = docs.list_docs("discovered_edges")
        except Exception:
            return

        from godview.sovereign.sentinels.pack import compile_universal_sentinels

        new_sentinels = compile_universal_sentinels(services=services, edges=edges)
        for snt in new_sentinels:
            existing = docs.get("sentinels", snt.sentinel_id) if hasattr(docs, "get") else None
            if existing is None:
                engine.register_sentinel(snt)

    hooks = getattr(ctx, "hooks", None)
    if hooks is not None:
        if hasattr(hooks, "perception_tick") and perception_sentinel_hook not in hooks.perception_tick:
            hooks.perception_tick.append(perception_sentinel_hook)
        if hasattr(hooks, "sentinel_tick") and engine.evaluate_tick not in hooks.sentinel_tick:
            hooks.sentinel_tick.append(engine.evaluate_tick)
        if hasattr(hooks, "on_change_event"):
            on_chg = engine.incident_manager.on_change_event
            if on_chg not in hooks.on_change_event:
                hooks.on_change_event.append(on_chg)

    return engine


__all__ = ["CapabilityInvoker", "SentinelEngine", "SentinelTickReport", "install"]
