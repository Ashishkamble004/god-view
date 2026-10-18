"""Trust Ladder & TrustGate (`godview.sovereign.trust_ladder`).

Enforces TDD I-06 (TrustGate before every mutating call, blast radius = 1 resource,
`godview-act-sa` impersonation, L2 `approval_id`, money/customer-comms caps) and
Coordinator Decision A10:
- Promotion thresholds: N1=3 -> L2, N2=5 -> L3, N3=10 + human whitelist -> L4
- Demotion: a single bad outcome resets that action type to L1 (not level - 1) and increments regressions
- L4 whitelisting: only a human owner from the Cockpit (authenticated via IAP) may set whitelisted=true
- Global kill switch: in-flight L3/L4 remediation finishes its current single-resource step,
  all queued steps abort, and any new L2 approval is auto-expired.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from godview_shared.capabilities import get_capability
from godview_shared.contracts import (
    ApprovalCard,
    AuditApprover,
    AuditRecord,
    TrustDecision,
    TrustHistoryEntry,
    TrustRecord,
    TrustViolation,
)

from godview.sovereign.court_manager.token_governor import TokenBudgetGovernor
from godview.sovereign.nervous_system import NervousSystemRouter

PLATFORM_ACTION_PREFIXES: tuple[str, ...] = (
    "armor.",
    "run.",
    "gke.",
    "pubsub.",
    "k8s.",
)


def _is_platform_action(action_type: str) -> bool:
    return any(action_type.startswith(p) for p in PLATFORM_ACTION_PREFIXES)

N1_RECOMMENDATIONS_FOR_L2: int = 3
N2_APPROVED_EXECUTIONS_FOR_L3: int = 5
N3_BUSINESS_STREAK_FOR_L4: int = 10


class TrustGate:
    """Deterministic Trust Ladder (L0-L4) state machine and mutation authorizer."""

    def __init__(
        self,
        firestore: Any = None,
        pubsub: Any = None,
        token_governor: TokenBudgetGovernor | None = None,
        *,
        docs: Any = None,
        bus: Any = None,
        clock: Any = None,
    ) -> None:
        self._firestore = docs if docs is not None else firestore
        self._pubsub = bus if bus is not None else pubsub
        self._token_governor = token_governor
        self._clock = clock
        self._lock = threading.Lock()
        self._router = NervousSystemRouter(pubsub=self._pubsub) if self._pubsub is not None else None
        self.global_kill_switch: bool = False
        self._inflight_steps: list[str] = []
        self._queued_steps: list[str] = []
        self._approval_seq: int = 0
        self._audit_seq: int = 0
        self._audit_records: dict[str, AuditRecord] = {}

    @staticmethod
    def _record_id(agent_id: str, action_type: str) -> str:
        return f"{agent_id}__{action_type}"

    def get_or_create_record(
        self,
        agent_id: str,
        action_type: str,
        *,
        pre_promote_platform: bool = True,
    ) -> TrustRecord:
        """Fetch or initialize the TrustRecord for (agent_id, action_type).

        Pre-promotes platform actions to L2 on the proving ground (Plan §3.4 item 5).
        """
        doc_id = self._record_id(agent_id, action_type)
        if self._firestore is not None:
            raw = self._firestore.get("trust_records", doc_id)
            if raw is not None:
                return TrustRecord.model_validate(raw)

        initial_level = 2 if (pre_promote_platform and _is_platform_action(action_type)) else 0
        now_iso = datetime.now(tz=UTC).isoformat()
        history = []
        if initial_level > 0:
            history.append(
                TrustHistoryEntry.model_validate(
                    {
                        "from": 0,
                        "to": initial_level,
                        "at": now_iso,
                        "reason": "Proving ground pre-promoted to L2 for platform actions",
                    }
                )
            )

        rec = TrustRecord(
            agent_id=agent_id,
            action_type=action_type,
            level=initial_level,
            history=history,
            last_promoted_at=now_iso if initial_level > 0 else None,
        )
        return self._save_record(rec)

    def _save_record(self, record: TrustRecord) -> TrustRecord:
        doc_id = self._record_id(record.agent_id, record.action_type)
        self._firestore.set(
            "trust_records",
            doc_id,
            record.model_dump(mode="json", by_alias=True),
        )
        return record

    def _transition(
        self,
        record: TrustRecord,
        new_level: int,
        reason: str,
        **extra_updates: Any,
    ) -> TrustRecord:
        now_iso = datetime.now(tz=UTC).isoformat()
        history = list(record.history)
        if record.level != new_level:
            history.append(
                TrustHistoryEntry.model_validate(
                    {
                        "from": record.level,
                        "to": new_level,
                        "at": now_iso,
                        "reason": reason,
                    }
                )
            )
            if new_level > record.level:
                extra_updates.setdefault("last_promoted_at", now_iso)
            else:
                extra_updates.setdefault("last_demoted_at", now_iso)
                extra_updates.setdefault("demotion_reason", reason)
        updates: dict[str, Any] = {"level": new_level, "history": history, **extra_updates}
        return self._save_record(record.model_copy(update=updates))

    def promote_to_l1_after_self_check(self, agent_id: str, action_type: str) -> TrustRecord:
        """Promote L0 -> L1 after passing self-consistency check (Agent Spec §8.1)."""
        rec = self.get_or_create_record(agent_id, action_type)
        if rec.level < 1:
            return self._transition(rec, 1, "Passed self-consistency check")
        return rec

    def record_recommendation_correct(self, agent_id: str, action_type: str) -> TrustRecord:
        """Record a verified recommendation; promote L1 -> L2 at N1=3 (Decision A10)."""
        rec = self.get_or_create_record(agent_id, action_type)
        new_count = rec.counters.recommendations_correct + 1
        counters = rec.counters.model_copy(update={"recommendations_correct": new_count})
        if rec.level == 1 and new_count >= N1_RECOMMENDATIONS_FOR_L2:
            return self._transition(
                rec,
                2,
                f"N1={N1_RECOMMENDATIONS_FOR_L2} correct recommendations verified",
                counters=counters,
            )
        return self._save_record(rec.model_copy(update={"counters": counters}))

    def record_approved_execution_verified(self, agent_id: str, action_type: str) -> TrustRecord:
        """Record an approved execution with verified outcome; promote L2 -> L3 at N2=5 & 0 regressions."""
        spec = get_capability(action_type)
        rec = self.get_or_create_record(agent_id, action_type)
        new_exec = rec.counters.approved_executions + 1
        new_ver = rec.counters.verified_outcomes + 1
        counters = rec.counters.model_copy(
            update={"approved_executions": new_exec, "verified_outcomes": new_ver}
        )
        if (
            rec.level == 2
            and new_exec >= N2_APPROVED_EXECUTIONS_FOR_L3
            and rec.counters.regressions == 0
            and not spec.customer_comms
        ):
            return self._transition(
                rec,
                3,
                f"N2={N2_APPROVED_EXECUTIONS_FOR_L3} approved executions verified with 0 regressions",
                counters=counters,
            )
        return self._save_record(rec.model_copy(update={"counters": counters}))

    def record_business_success(self, agent_id: str, action_type: str) -> TrustRecord:
        """Record a business-KPI-validated autonomous success; promote L3 -> L4 if N3=10 and whitelisted."""
        rec = self.get_or_create_record(agent_id, action_type)
        new_streak = rec.counters.business_successes + 1
        counters = rec.counters.model_copy(update={"business_successes": new_streak})
        if rec.level == 3 and new_streak >= N3_BUSINESS_STREAK_FOR_L4 and rec.whitelisted:
            return self._transition(
                rec,
                4,
                f"N3={N3_BUSINESS_STREAK_FOR_L4} business-KPI-validated successes + human whitelist",
                counters=counters,
            )
        return self._save_record(rec.model_copy(update={"counters": counters}))

    def list_agent_records(self, agent_id: str) -> list[TrustRecord]:
        """Return all persisted TrustRecord documents for agent_id."""
        docs = self._firestore.list_docs("trust_records", where={"agent_id": agent_id})
        return [TrustRecord.model_validate(d) for d in docs]

    def whitelist_action(
        self,
        agent_id: str,
        action_type: str,
        iap_principal: str,
        *,
        whitelisted: bool = True,
    ) -> TrustRecord:
        """Whitelist or un-whitelist an action for L4 by an authenticated human Cockpit owner (Decision A10)."""
        spec = get_capability(action_type)
        if whitelisted and spec.customer_comms:
            raise TrustViolation(
                f"Capability '{action_type}' is customer communication and can never be whitelisted in v1"
            )
        cleaned_principal = iap_principal.strip()
        if ":" in cleaned_principal:
            cleaned_principal = cleaned_principal.split(":", 1)[-1].strip()
        if not cleaned_principal or "@" not in cleaned_principal:
            raise TrustViolation(
                "L4 whitelisting requires an authenticated human IAP principal (Decision A10)"
            )

        rec = self.get_or_create_record(agent_id, action_type)
        if not whitelisted:
            target_level = min(rec.level, 3)
            if rec.level != target_level:
                return self._transition(
                    rec,
                    target_level,
                    f"L4 whitelist revoked by human owner {cleaned_principal}",
                    whitelisted=False,
                    whitelisted_by=None,
                )
            now_iso = datetime.now(tz=UTC).isoformat()
            history = list(rec.history)
            history.append(
                TrustHistoryEntry.model_validate(
                    {
                        "from": rec.level,
                        "to": rec.level,
                        "at": now_iso,
                        "reason": f"L4 whitelist disabled by human owner {cleaned_principal}",
                    }
                )
            )
            return self._save_record(
                rec.model_copy(
                    update={"whitelisted": False, "whitelisted_by": None, "history": history}
                )
            )

        if rec.level == 3 and rec.counters.business_successes >= N3_BUSINESS_STREAK_FOR_L4:
            return self._transition(
                rec,
                4,
                f"Whitelisted for L4 by human owner {cleaned_principal} with N3>={N3_BUSINESS_STREAK_FOR_L4}",
                whitelisted=True,
                whitelisted_by=cleaned_principal,
            )
        return self._save_record(
            rec.model_copy(update={"whitelisted": True, "whitelisted_by": cleaned_principal})
        )

    def record_bad_outcome(
        self,
        agent_id: str,
        action_type: str | None = None,
        reason: str | None = None,
        *,
        capability_id: str | None = None,
        failure_reason: str | None = None,
    ) -> TrustRecord:
        """Reset action type to L1 (not level - 1) and increment regressions on bad outcome (Decision A10)."""
        effective_action = action_type or capability_id or "unknown"
        effective_reason = reason or failure_reason or "Remediation failed verification"
        rec = self.get_or_create_record(agent_id, effective_action)
        counters = rec.counters.model_copy(
            update={
                "regressions": rec.counters.regressions + 1,
                "business_successes": 0,
            }
        )
        return self._transition(
            rec,
            1,
            effective_reason,
            counters=counters,
            whitelisted=False,
        )

    def authorize(
        self,
        *,
        agent_id: str,
        capability_id: str,
        target_resource: str,
        blast_radius_resources: int = 1,
        approval_id: str | None = None,
        novel_skill_first_step: bool = False,
    ) -> TrustDecision:
        """Evaluate TrustGate before a capability invocation (TDD I-06, Decision A3 & A10)."""
        spec = get_capability(capability_id)
        now_iso = datetime.now(tz=UTC).isoformat()

        if spec.sovereign_only and agent_id != "sovereign":
            raise TrustViolation(
                f"Capability '{capability_id}' is sovereign-only; '{agent_id}' is forbidden (TDD §3.7)"
            )

        if not spec.mutating:
            return TrustDecision(
                authorized=True,
                agent_id=agent_id,
                action_type=capability_id,
                capability_id=capability_id,
                effective_level=0,
                required_level=0,
                approval_id=approval_id,
                sa_to_impersonate="godview-sense-sa",
                blast_radius_resources=blast_radius_resources,
                reason="Read-only capability authorized at L0",
                decided_at=now_iso,
            )

        if blast_radius_resources != 1:
            raise TrustViolation(
                f"Blast radius must be 1 resource per step; got {blast_radius_resources} for '{target_resource}' (TDD I-06)"
            )

        if self.global_kill_switch:
            return TrustDecision(
                authorized=False,
                agent_id=agent_id,
                action_type=capability_id,
                capability_id=capability_id,
                effective_level=1,
                required_level=spec.min_level,
                sa_to_impersonate="godview-sense-sa",
                blast_radius_resources=1,
                reason="Global kill switch is active; mutating actions halted (Decision A10)",
                decided_at=now_iso,
            )

        rec = self.get_or_create_record(agent_id, capability_id)
        effective_level: int = rec.level
        if self._token_governor is not None:
            effective_level = self._token_governor.effective_trust_level(agent_id, effective_level)

        if spec.customer_comms:
            effective_level = min(effective_level, 2)
        if spec.money_movement and not (rec.whitelisted and rec.level == 4):
            effective_level = min(effective_level, 2)

        clamped_level: int = max(0, min(4, effective_level))

        if approval_id is not None:
            if self._firestore is None:
                raise TrustViolation(f"Approval '{approval_id}' cannot be validated: no datastore configured")
            with self._lock:
                apr_raw = self._firestore.get("approvals", approval_id)
                if apr_raw is None or apr_raw.get("decision") != "approved":
                    raise TrustViolation(
                        f"Approval '{approval_id}' is missing or not in 'approved' state"
                    )
                if apr_raw.get("consumed"):
                    raise TrustViolation(
                        f"Approval '{approval_id}' has already been consumed and cannot be reused"
                    )
                apr_cap = apr_raw.get("capability") or apr_raw.get("action_type")
                if apr_cap != capability_id:
                    raise TrustViolation(
                        f"Approval '{approval_id}' is bound to capability '{apr_cap}', not '{capability_id}'"
                    )
                apr_agent = apr_raw.get("agent_id")
                if apr_agent != agent_id:
                    raise TrustViolation(
                        f"Approval '{approval_id}' is bound to agent '{apr_agent}', not '{agent_id}'"
                    )
                expected_target = target_resource or capability_id
                apr_target = apr_raw.get("target_resource")
                if apr_target != expected_target:
                    raise TrustViolation(
                        f"Approval '{approval_id}' is bound to target '{apr_target}', not '{expected_target}'"
                    )
                raw_expires = apr_raw.get("expires_at")
                if isinstance(raw_expires, str) and raw_expires:
                    exp_dt = datetime.fromisoformat(raw_expires.replace("Z", "+00:00"))
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=UTC)
                    if exp_dt <= datetime.now(tz=UTC):
                        raise TrustViolation(f"Approval '{approval_id}' expired at {raw_expires}")
                self._firestore.set(
                    "approvals",
                    approval_id,
                    {"consumed": True, "consumed_at": now_iso},
                    merge=True,
                )
            exec_level = max(2, clamped_level)
            return TrustDecision(
                authorized=True,
                agent_id=agent_id,
                action_type=capability_id,
                capability_id=capability_id,
                effective_level=exec_level,
                required_level=spec.min_level,
                approval_id=approval_id,
                sa_to_impersonate="godview-act-sa",
                blast_radius_resources=1,
                reason=f"Authorized via verified human approval '{approval_id}'",
                decided_at=now_iso,
            )

        if novel_skill_first_step:
            return TrustDecision(
                authorized=False,
                agent_id=agent_id,
                action_type=capability_id,
                capability_id=capability_id,
                effective_level=min(clamped_level, 2),
                required_level=spec.min_level,
                sa_to_impersonate="godview-sense-sa",
                blast_radius_resources=1,
                reason="Novel skill first mutating step always requires 1-click human approval (Agent Spec §8.1 #3)",
                decided_at=now_iso,
            )

        if spec.customer_comms:
            return TrustDecision(
                authorized=False,
                agent_id=agent_id,
                action_type=capability_id,
                capability_id=capability_id,
                effective_level=clamped_level,
                required_level=spec.min_level,
                sa_to_impersonate="godview-sense-sa",
                blast_radius_resources=1,
                reason="Customer communication is capped at L2 and always requires human approval",
                decided_at=now_iso,
            )

        if spec.money_movement and clamped_level < 4:
            return TrustDecision(
                authorized=False,
                agent_id=agent_id,
                action_type=capability_id,
                capability_id=capability_id,
                effective_level=clamped_level,
                required_level=spec.min_level,
                sa_to_impersonate="godview-sense-sa",
                blast_radius_resources=1,
                reason="Money-movement capability is capped at L2 until whitelisted for L4",
                decided_at=now_iso,
            )

        if clamped_level < 3 or clamped_level < spec.min_level:
            return TrustDecision(
                authorized=False,
                agent_id=agent_id,
                action_type=capability_id,
                capability_id=capability_id,
                effective_level=clamped_level,
                required_level=spec.min_level,
                sa_to_impersonate="godview-sense-sa",
                blast_radius_resources=1,
                reason=f"Effective level L{clamped_level} requires human approval for L{spec.min_level} capability",
                decided_at=now_iso,
            )

        return TrustDecision(
            authorized=True,
            agent_id=agent_id,
            action_type=capability_id,
            capability_id=capability_id,
            effective_level=clamped_level,
            required_level=spec.min_level,
            sa_to_impersonate="godview-act-sa",
            blast_radius_resources=1,
            reason=f"Autonomous execution authorized at L{clamped_level}",
            decided_at=now_iso,
        )

    def register_inflight_step(self, step_id: str) -> None:
        """Track a currently executing single-resource remediation step."""
        if step_id not in self._inflight_steps:
            self._inflight_steps.append(step_id)

    def enqueue_step(self, step_id: str) -> None:
        """Track a queued remediation step."""
        self._queued_steps.append(step_id)

    def set_global_kill_switch(self, *, enabled: bool, actor: str) -> dict[str, Any]:
        """Engage or disengage the global kill switch (Decision A10)."""
        self.global_kill_switch = enabled
        if enabled:
            inflight_allowed = self._inflight_steps[:1]
            queued_aborted = self._inflight_steps[1:] + list(self._queued_steps)
            self._inflight_steps = inflight_allowed
            self._queued_steps.clear()
            return {
                "enabled": True,
                "actor": actor,
                "inflight_allowed_to_finish": inflight_allowed,
                "queued_aborted": queued_aborted,
            }
        return {
            "enabled": False,
            "actor": actor,
            "inflight_allowed_to_finish": list(self._inflight_steps),
            "queued_aborted": [],
        }

    def create_approval_request(
        self,
        *,
        incident_id: str,
        agent_id: str,
        capability_id: str,
        target_resource: str,
        trust_level_required: int,
        narrative: str,
        evidence_pack_id: str = "evp-remediation",
        financial_impact: float = 0.0,
        reversal_recipe: dict[str, Any] | None = None,
        args: dict[str, Any] | None = None,
        verification_plan: list[dict[str, Any]] | list[str] | None = None,
        **kwargs: Any,
    ) -> ApprovalCard:
        """Create a persisted L2 approval card; auto-expire if global kill switch is active (Decision A10)."""
        now = datetime.now(tz=UTC)
        date_str = now.strftime("%Y%m%d")
        approval_id = f"apr-{date_str}-{uuid.uuid4().hex[:8]}"

        decision: Literal["pending", "approved", "rejected", "snoozed", "expired"] = (
            "expired" if self.global_kill_switch else "pending"
        )
        req_level = max(1, min(4, trust_level_required))
        effective_financial_impact = financial_impact or 0.0
        if not effective_financial_impact:
            for k, v in kwargs.items():
                if "impact" in k and isinstance(v, (int, float)):
                    effective_financial_impact = float(v)
                    break
        extra_fields = {
            "args": args or {},
            "verification_plan": verification_plan or [],
            "consumed": False,
        }
        card = ApprovalCard(
            approval_id=approval_id,
            incident_id=incident_id,
            agent_id=agent_id,
            action_type=capability_id,
            capability=capability_id,
            target_resource=target_resource,
            trust_level_required=req_level,
            narrative=narrative,
            evidence_pack_id=evidence_pack_id,
            financial_impact=effective_financial_impact,
            reversal_recipe=reversal_recipe or {"capability": capability_id},
            requested_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=30)).isoformat(),
            decision=decision,
            decided_by="system:kill-switch" if self.global_kill_switch else None,
            decided_at=now.isoformat() if self.global_kill_switch else None,
            **extra_fields,
        )
        if self._firestore is not None:
            self._firestore.set("approvals", approval_id, card.model_dump(mode="json"))
        return card

    def decide_approval(
        self,
        approval_id: str,
        *,
        decision: Literal["approved", "rejected", "snoozed"],
        decided_by: str,
    ) -> tuple[ApprovalCard, AuditRecord]:
        """Record a human IAP decision on an approval card and write an immutable AuditRecord."""
        raw = self._firestore.get("approvals", approval_id)
        if raw is None:
            raise KeyError(f"Unknown approval_id '{approval_id}'")
        card = ApprovalCard.model_validate(raw)
        if card.decision != "pending":
            raise TrustViolation(
                f"Approval '{approval_id}' is already in terminal state '{card.decision}'"
            )

        cleaned_actor = decided_by.strip()
        if ":" in cleaned_actor:
            cleaned_actor = cleaned_actor.split(":", 1)[-1].strip()
        if not cleaned_actor:
            raise TrustViolation("IAP principal header is required to decide an approval")

        now_iso = datetime.now(tz=UTC).isoformat()
        audit = self.record_audit(
            actor=cleaned_actor,
            action=card.capability,
            target=card.target_resource,
            trust_level=2,
            approval_id=approval_id,
            incident_id=card.incident_id,
            evidence_pack_id=card.evidence_pack_id,
            reversal_recipe=card.reversal_recipe,
            outcome="success" if decision == "approved" else "reverted",
        )
        updated_card = card.model_copy(
            update={
                "decision": decision,
                "decided_by": cleaned_actor,
                "decided_at": now_iso,
                "audit_id": audit.audit_id,
            }
        )
        self._firestore.set("approvals", approval_id, updated_card.model_dump(mode="json"))
        return updated_card, audit

    def record_audit(
        self,
        *,
        actor: str,
        action: str,
        target: str,
        trust_level: int,
        approval_id: str | None = None,
        incident_id: str | None = None,
        evidence_pack_id: str = "evp-20261016-0001",
        reversal_recipe: dict[str, Any] | None = None,
        outcome: Literal["success", "failed", "reverted"] = "success",
    ) -> AuditRecord:
        """Create and store an immutable AuditRecord."""
        now = datetime.now(tz=UTC)
        now_iso = now.isoformat()
        audit_id = f"aud-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
        approver = (
            AuditApprover(principal=actor, channel="cockpit", at=now_iso)
            if approval_id is not None
            else None
        )
        doc = AuditRecord(
            audit_id=audit_id,
            incident_id=incident_id or "inc-20261016-0001",
            agent_id=actor,
            trust_level=max(0, min(4, trust_level)),
            action_type=action,
            capability=action,
            target_resource=target,
            approver=approver,
            evidence_pack_id=evidence_pack_id,
            sa_used="godview-act-sa",
            executed_at=now_iso,
            result=outcome,
            reversal_recipe=reversal_recipe or {"capability": action},
        )
        self._audit_records[audit_id] = doc
        if self._firestore is not None:
            try:
                self._firestore.set("audit_records", audit_id, doc.model_dump(mode="json"))
            except Exception:
                pass
        return doc


__all__ = ["TrustGate"]
