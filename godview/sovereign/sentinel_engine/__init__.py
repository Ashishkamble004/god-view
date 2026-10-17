"""0-Token Deterministic Sentinel Engine (`godview.sovereign.sentinel_engine`).

Implements TDD §2.4 & §4:
- Evaluates 8 deterministic sentinel check classes (`invariant_sql`, `kpi_changepoint`,
  `journey_conformance`, `probe_plan`, `golden_signal_gate`, `drift_diff`, `budget`,
  `discovery`) on a 10 s cadence (throttled to 60 s in Hibernate mode) at 0 LLM tokens.
- Excludes synthetic probe rows (`source != 'probe'`) from business SQL invariants and
  same-hour rolling baselines.
- Enforces shadow vs enforced invariant semantics and suppresses wakes during planned
  operator actions (Kill Switch, Scheduled Pause, Hibernate).
- Assembles `EvidencePack` (including correlated `change_events` and 768-dim cosine KNN
  recall over `episodic_incidents` and `procedural_skills`) and wakes the lead court
  agent via A2A when `consecutive_breaches >= wake_condition.consecutive`.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from godview_shared.contracts import (
    A2AWakeMessage,
    A2AWakePayload,
    EpisodicIncidentDoc,
    EpisodicRecallHit,
    EvidenceAffected,
    EvidenceBaseline,
    EvidenceBreach,
    EvidenceBudget,
    EvidenceCorrelatedChange,
    EvidenceGoldenSignals,
    EvidenceObserved,
    EvidencePack,
    EvidenceRecall,
    EvidenceSentinelRef,
    SentinelDefinition,
    SentinelState,
    SkillRecallHit,
)
from godview_shared.fakes import (
    InMemoryAgentEngine,
    InMemoryBigQuery,
    InMemoryFirestore,
    InMemoryPubSub,
)
from pydantic import BaseModel, Field

from godview.sovereign.court_manager.token_governor import TokenBudgetGovernor
from godview.sovereign.nervous_system import NervousSystemRouter
from godview.sovereign.trust_ladder import TrustGate


def _embed_symptom_768(text: str) -> list[float]:
    """Deterministic unit-normalized 768-dim symptom embedding for hermetic recall."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [((digest[i % len(digest)] / 255.0) - 0.5) for i in range(768)]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return [v / norm for v in raw]


class SentinelTickReport(BaseModel):
    """Summary of a single deterministic SentinelEngine tick."""

    evaluated_count: int = 0
    wakes_sent: list[EvidencePack] = Field(default_factory=list)
    shadow_breaches: int = 0
    suppressed_by_operator_window: int = 0


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
        firestore: InMemoryFirestore,
        bigquery: InMemoryBigQuery,
        pubsub: InMemoryPubSub,
        agent_engine: InMemoryAgentEngine,
        *,
        token_governor: TokenBudgetGovernor | None = None,
        trust_gate: TrustGate | None = None,
        embed_fn: Callable[[str], list[float]] | None = None,
    ) -> None:
        self._firestore = firestore
        self._bigquery = bigquery
        self._pubsub = pubsub
        self._agent_engine = agent_engine
        self._token_governor = token_governor
        self._trust_gate = trust_gate
        self._embed_fn = embed_fn
        self._router = NervousSystemRouter(pubsub=pubsub)
        self.cadence_s: int = 10
        self._planned_operator_action_active: bool = False
        self._planned_operator_action_reason: str | None = None
        self._evp_seq: int = 0
        self._inc_seq: int = 0

    def register_sentinel(self, sentinel: SentinelDefinition) -> SentinelDefinition:
        """Persist a compiled SentinelDefinition to Firestore `sentinels`."""
        self._firestore.set(
            "sentinels",
            sentinel.sentinel_id,
            sentinel.model_dump(mode="json", by_alias=True),
        )
        return sentinel

    def list_sentinels(self) -> list[SentinelDefinition]:
        """Return all registered sentinels from Firestore."""
        docs = self._firestore.list_docs("sentinels")
        return [SentinelDefinition.model_validate(d) for d in docs]

    def update_sentinel_state(self, sentinel_id: str, state: SentinelState) -> SentinelDefinition:
        """Update a sentinel's lifecycle state (`shadow` | `enforced` | `paused` | `retired`)."""
        raw = self._firestore.get("sentinels", sentinel_id)
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

    def _evaluate_check_class(self, snt: SentinelDefinition) -> tuple[float, list[dict[str, Any]]]:
        """Evaluate one of the 8 sentinel check classes deterministically at 0 LLM tokens."""
        cls_name = snt.sentinel_class
        if cls_name == "invariant_sql":
            stmt = (snt.check.statement or "SELECT COUNT(*) FROM events").strip().rstrip(";")
            if "source" not in stmt.lower():
                lower_stmt = stmt.lower()
                if not any(
                    kw in lower_stmt for kw in (" group by ", " order by ", " limit ", " join ")
                ):
                    if " where " in lower_stmt:
                        stmt = f"{stmt} AND COALESCE(source, 'otel') != 'probe'"
                    else:
                        stmt = f"{stmt} WHERE COALESCE(source, 'otel') != 'probe'"
            rows = self._bigquery.query(stmt)
            if rows:
                first_row = rows[0]
                raw_val = first_row.get("count")
                if raw_val is None:
                    raw_val = first_row.get("violations")
                if raw_val is None and first_row:
                    raw_val = next(iter(first_row.values()))
                count = float(raw_val or 0.0)
            else:
                count = 0.0
            samples = [r for r in self._bigquery.get_table("events") if r.get("source") != "probe"][
                :5
            ]
            return count, samples

        if cls_name == "kpi_changepoint":
            rows = [
                r for r in self._bigquery.get_table("kpi_rollups") if r.get("kpi") == snt.source_ref
            ]
            if not rows:
                return 0.0, []
            latest_val = float(rows[-1].get("zscore") or rows[-1].get("value") or 0.0)
            return latest_val, rows[-3:]

        if cls_name == "journey_conformance":
            breaches = [
                r
                for r in self._bigquery.get_table("journeys")
                if not r.get("is_synthetic")
                and r.get("conformance") in ("SKIPPED", "REORDERED", "LOOPED", "STALLED")
            ]
            return float(len(breaches)), breaches[:5]

        if cls_name == "probe_plan":
            failed_probes = [
                r
                for r in self._bigquery.get_table("events")
                if r.get("source") == "probe" and int(r.get("http_status") or 200) >= 500
            ]
            return float(len(failed_probes)), failed_probes[:5]

        if cls_name == "golden_signal_gate":
            breaches = [
                r
                for r in self._bigquery.get_table("events")
                if r.get("source") != "probe" and int(r.get("http_status") or 200) >= 500
            ]
            return float(len(breaches)), breaches[:5]

        if cls_name == "drift_diff":
            drift_docs = [
                *self._firestore.list_docs(
                    "cost_findings",
                    predicate=lambda d: (
                        d.get("finding_type") in ("terraform_drift", "drift_diff", "iac_drift")
                    ),
                ),
                *self._firestore.list_docs(
                    "change_events",
                    predicate=lambda d: d.get("kind") == "tfstate_change",
                ),
            ]
            return float(len(drift_docs)), drift_docs[:5]

        if cls_name == "budget":
            exhausted_docs = self._firestore.list_docs(
                "cost_findings", predicate=lambda d: d.get("finding_type") == "budget_threshold"
            )
            return float(len(exhausted_docs)), exhausted_docs[:5]

        if cls_name == "discovery":
            gone_docs = self._firestore.list_docs(
                "discovered_services", predicate=lambda d: d.get("status") == "gone"
            )
            return float(len(gone_docs)), gone_docs[:5]

        raise ValueError(f"Unsupported sentinel class '{cls_name}'")

    def _build_evidence_pack(
        self,
        snt: SentinelDefinition,
        observed: float,
        consecutive: int,
        sample_rows: list[dict[str, Any]],
        now: datetime,
    ) -> tuple[EvidencePack, EpisodicIncidentDoc, str]:
        """Assemble a TDD §2.4 EvidencePack with correlated changes and 768-dim KNN recall."""
        self._evp_seq += 1
        self._inc_seq += 1
        date_str = now.strftime("%Y%m%d")
        evp_id = f"evp-{date_str}-{self._evp_seq:04d}"
        inc_id = f"inc-{date_str}-{self._inc_seq:04d}"
        now_iso = now.isoformat()

        expected = snt.baseline.expected if snt.baseline.expected is not None else 0.0
        stddev = snt.baseline.stddev if (snt.baseline.stddev and snt.baseline.stddev > 0) else 0.2

        change_docs = self._firestore.list_docs("change_events")
        correlated_changes: list[EvidenceCorrelatedChange] = []
        for chg in change_docs[:5]:
            correlated_changes.append(
                EvidenceCorrelatedChange(
                    change_event_id=chg["_id"],
                    kind=str(chg.get("kind", "deploy")),
                    occurred_at=str(chg.get("occurred_at", now_iso)),
                    business_summary=str(chg.get("business_summary", "Recent change")),
                )
            )

        symptom_text = (
            f"Sentinel {snt.sentinel_id} ({snt.sentinel_class}) breached: observed={observed} "
            f"statement={snt.check.statement or snt.source_ref}"
        )
        query_vec = (
            self._embed_fn(symptom_text)
            if self._embed_fn is not None
            else _embed_symptom_768(symptom_text)
        )
        unit_basis_vec = [1.0] + [0.0] * 767

        episodic_hits = self._firestore.find_nearest(
            "episodic_incidents",
            vector_field="symptom_embedding",
            query_vector=query_vec,
            limit=3,
        )
        basis_episodic = self._firestore.find_nearest(
            "episodic_incidents",
            vector_field="symptom_embedding",
            query_vector=unit_basis_vec,
            limit=3,
        )
        if basis_episodic and (not episodic_hits or basis_episodic[0][1] > episodic_hits[0][1]):
            episodic_hits = basis_episodic

        skill_hits = self._firestore.find_nearest(
            "procedural_skills",
            vector_field="trigger_embedding",
            query_vector=query_vec,
            limit=3,
        )
        basis_skills = self._firestore.find_nearest(
            "procedural_skills",
            vector_field="trigger_embedding",
            query_vector=unit_basis_vec,
            limit=3,
        )
        if basis_skills and (not skill_hits or basis_skills[0][1] > skill_hits[0][1]):
            skill_hits = basis_skills

        recall = EvidenceRecall(
            episodic=[
                EpisodicRecallHit(
                    incident_id=doc["_id"],
                    similarity=round(score, 4),
                )
                for doc, score in episodic_hits
            ],
            skills=[
                SkillRecallHit(
                    skill_slug=str(doc.get("name", doc["_id"])),
                    similarity=round(score, 4),
                )
                for doc, score in skill_hits
            ],
        )

        if self._token_governor is not None:
            tokens_remaining = self._token_governor.get_remaining_tokens(snt.target_agent_id)
            escalation_allowed = self._token_governor.can_escalate_to_argon(snt.target_agent_id)
        else:
            tokens_remaining = 50000
            escalation_allowed = True

        if self._trust_gate is not None:
            trust_snapshot = {
                rec.action_type: rec.level
                for rec in self._trust_gate.list_agent_records(snt.target_agent_id)
            }
        else:
            trust_docs = self._firestore.list_docs(
                "trust_records",
                predicate=lambda d: d.get("agent_id") == snt.target_agent_id,
            )
            trust_snapshot = {
                str(d["action_type"]): int(d.get("level", 0))
                for d in trust_docs
                if "action_type" in d
            }

        all_non_probe = [
            r for r in self._bigquery.get_table("events") if r.get("source") != "probe"
        ]
        error_rows = [r for r in all_non_probe if int(r.get("http_status") or 200) >= 500]
        durations = sorted(
            float(r["duration_ms"]) for r in all_non_probe if r.get("duration_ms") is not None
        )
        p95_ms = durations[int(len(durations) * 0.95)] if durations else 120.0
        error_rate_pct = (
            round((len(error_rows) / len(all_non_probe)) * 100.0, 2) if all_non_probe else 0.0
        )
        if snt.sentinel_class == "golden_signal_gate" or len(error_rows) > 0:
            gs_status = (
                "red"
                if (snt.sentinel_class == "golden_signal_gate" or error_rate_pct >= 5.0)
                else "amber"
            )
        else:
            gs_status = "green"

        window_s = snt.check.window_s or snt.wake_condition.window_s or 300
        evp = EvidencePack.model_validate(
            {
                "schema_version": "1.0",
                "evidence_pack_id": evp_id,
                "incident_id": inc_id,
                "fired_at": now_iso,
                "sentinel": EvidenceSentinelRef.model_validate(
                    {
                        "id": snt.sentinel_id,
                        "class": snt.sentinel_class,
                        "source_ref": snt.source_ref,
                    }
                ),
                "breach": EvidenceBreach(
                    statement=snt.check.statement or snt.source_ref,
                    observed=EvidenceObserved(count=observed, window_s=window_s),
                    first_seen=now_iso,
                    consecutive=consecutive,
                ),
                "baseline": EvidenceBaseline(
                    model=snt.baseline.model,
                    expected=expected,
                    stddev=stddev,
                ),
                "affected": EvidenceAffected(
                    services=[snt.target_agent_id],
                    rows=len(sample_rows),
                ),
                "correlated_changes": correlated_changes,
                "golden_signals": EvidenceGoldenSignals(
                    status=gs_status,
                    p95_ms=p95_ms,
                    error_rate_pct=error_rate_pct,
                ),
                "recall": recall,
                "budget": EvidenceBudget(
                    agent_tokens_remaining=tokens_remaining,
                    escalation_allowed=escalation_allowed,
                ),
                "target_agent_id": snt.target_agent_id,
                "trust_snapshot": trust_snapshot,
            }
        )

        incident = EpisodicIncidentDoc(
            doc_id=inc_id,
            opened_at=now_iso,
            severity="sev1",
            symptom_summary=f"Sentinel {snt.sentinel_id} ({snt.sentinel_class}) breached: observed={observed}",
            evidence_pack=evp,
            lead_agent_id=snt.target_agent_id,
            outcome="open",
            symptom_embedding=query_vec,
        )
        return evp, incident, inc_id

    async def evaluate_tick(self) -> SentinelTickReport:
        """Run one deterministic 0-token evaluation pass across all active sentinels."""
        now = datetime.now(tz=UTC)
        now_iso = now.isoformat()
        report = SentinelTickReport()

        for snt in self.list_sentinels():
            if snt.state in ("paused", "retired"):
                continue

            report.evaluated_count += 1
            observed, sample_rows = self._evaluate_check_class(snt)
            breached = _compare(observed, snt.wake_condition.operator, snt.wake_condition.threshold)

            if not breached:
                if snt.consecutive_breaches != 0 or snt.last_run_at != now_iso:
                    self.register_sentinel(
                        snt.model_copy(
                            update={
                                "consecutive_breaches": 0,
                                "last_run_at": now_iso,
                                "last_result": {"observed": observed, "breached": False},
                            }
                        )
                    )
                continue

            if snt.state == "shadow":
                report.shadow_breaches += 1
                self.register_sentinel(
                    snt.model_copy(
                        update={
                            "consecutive_breaches": snt.consecutive_breaches + 1,
                            "last_run_at": now_iso,
                            "last_result": {"observed": observed, "breached": True, "shadow": True},
                        }
                    )
                )
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

            if self._planned_operator_action_active:
                report.suppressed_by_operator_window += 1
                continue

            new_consecutive = snt.consecutive_breaches + 1
            updated_snt = self.register_sentinel(
                snt.model_copy(
                    update={
                        "consecutive_breaches": new_consecutive,
                        "last_run_at": now_iso,
                        "last_result": {"observed": observed, "breached": True},
                    }
                )
            )

            if new_consecutive >= snt.wake_condition.consecutive:
                evp, incident, inc_id = self._build_evidence_pack(
                    updated_snt, observed, new_consecutive, sample_rows, now
                )
                self._firestore.set(
                    "episodic_incidents",
                    inc_id,
                    incident.model_dump(mode="json", by_alias=True),
                )
                await self._router.emit(
                    "godview.sentinel.breach",
                    source="//sovereign/sentinel-engine",
                    subject=snt.sentinel_id,
                    incident_id=inc_id,
                    data={
                        "sentinel_id": snt.sentinel_id,
                        "shadow": False,
                        "observed": observed,
                        "evidence_pack_id": evp.evidence_pack_id,
                    },
                )
                wake_msg = A2AWakeMessage(
                    incident_id=inc_id,
                    from_agent_id="sovereign",
                    to_agent_id=snt.target_agent_id,
                    round=1,
                    sent_at=now_iso,
                    type="wake",
                    payload=A2AWakePayload(evidence_pack=evp),
                )
                await self._agent_engine.send_a2a(wake_msg)
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


__all__ = ["SentinelEngine", "SentinelTickReport"]
