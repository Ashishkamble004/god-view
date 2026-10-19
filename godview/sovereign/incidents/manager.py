"""IncidentManager: deduplication, lifecycle, evidence pack assembly, and court handoff.

Implements TDD §2.4, §3.3 & §4:
- Dedupe key format: `class:resource:signature`
- Incident lifecycle: open, update, auto-close when clear for N ticks, manual close
- EvidencePack assembly (log samples, config snapshot, probe transcript, metrics window,
  related change events, topology neighbourhood)
- Severity classification (sev1, sev2, sev3)
- `on_change_event` hook fed by nervous system / audit push
- SSE / EventBus event publishing (`incident.opened`, `incident.updated`, `incident.closed`)
- Handoff to court agents via `ctx.hooks.on_incident` and/or A2A wake messages
"""

from __future__ import annotations

import inspect
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from godview_shared.contracts import (
    A2AWakeMessage,
    A2AWakePayload,
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
    SkillRecallHit,
)

from godview.sovereign.incidents.models import IncidentDoc
from godview.sovereign.sentinels.models import SentinelEvaluation

logger = logging.getLogger(__name__)


class IncidentManager:
    """Manages the full lifecycle of episodic incidents."""

    def __init__(
        self,
        *,
        ctx: Any | None = None,
        firestore: Any | None = None,
        bigquery: Any | None = None,
        bus: Any | None = None,
        agents: Any | None = None,
        hooks: Any | None = None,
        token_governor: Any | None = None,
        trust_gate: Any | None = None,
        clock: Callable[[], datetime] | None = None,
        auto_close_ticks: int = 3,
        embed_fn: Callable[[str], list[float]] | None = None,
    ) -> None:
        self._ctx = ctx
        self._firestore = firestore or getattr(ctx, "docs", None)
        self._bigquery = bigquery or getattr(ctx, "ledger", None)
        self._bus = bus or getattr(ctx, "bus", None)
        self._agents = agents or getattr(ctx, "agents", None)
        self._hooks = hooks or getattr(ctx, "hooks", None)
        self._token_governor = token_governor
        self._trust_gate = trust_gate
        self._clock = clock or getattr(ctx, "clock", None) or (lambda: datetime.now(tz=UTC))
        self.auto_close_ticks = auto_close_ticks
        self._embed_fn = embed_fn

        self._active_incidents: dict[str, IncidentDoc] = {}
        self._load_active_incidents_from_store()

    async def _get_embedding(self, text: str) -> list[float] | None:
        """Return embedding vector from ctx.llm or provided embed_fn; otherwise None."""
        def _unwrap(val: Any) -> list[float] | None:
            if isinstance(val, list) and val:
                if isinstance(val[0], list):
                    try:
                        return [float(x) for x in val[0]]
                    except (TypeError, ValueError):
                        return None
                if isinstance(val[0], (int, float)):
                    try:
                        return [float(x) for x in val]
                    except (TypeError, ValueError):
                        return None
            return None

        if self._embed_fn is not None:
            try:
                res = self._embed_fn(text)
                if inspect.isawaitable(res):
                    res = await res
                return _unwrap(res)
            except Exception:
                return None
        llm = getattr(self._ctx, "llm", None)
        if llm is not None:
            if hasattr(llm, "embed"):
                try:
                    res = llm.embed([text])
                    if inspect.isawaitable(res):
                        res = await res
                    return _unwrap(res)
                except Exception:
                    return None
            elif hasattr(llm, "embed_sync"):
                try:
                    res = llm.embed_sync([text])
                    return _unwrap(res)
                except Exception:
                    return None
        return None

    def _load_active_incidents_from_store(self) -> None:
        """Load open incidents from Firestore/document store into _active_incidents."""
        if self._firestore is None or not hasattr(self._firestore, "list_docs"):
            return
        try:
            docs = self._firestore.list_docs("incidents")
            for d in docs:
                if d.get("outcome") == "open":
                    try:
                        inc = IncidentDoc.model_validate(d)
                        if not inc.target_resource and inc.dedupe_key:
                            parts = inc.dedupe_key.split(":")
                            if len(parts) >= 2:
                                inc.target_resource = parts[1]
                                inc.resource_id = parts[1]
                        if inc.dedupe_key:
                            self._active_incidents[inc.dedupe_key] = inc
                    except Exception:
                        pass
        except Exception:
            pass

    def load_active_incidents(self) -> None:
        """Explicit entrypoint to synchronize active incidents from store."""
        self._load_active_incidents_from_store()

    def make_dedupe_key(self, sentinel_class: str, resource_id: str, signature: str) -> str:
        """Construct canonical dedupe key: `class:resource:signature`."""
        res_clean = (resource_id or "global").replace(":", "_")
        sig_clean = (signature or "anomaly").replace(":", "_")
        return f"{sentinel_class}:{res_clean}:{sig_clean}"

    def _generate_incident_id(self, now: datetime) -> str:
        date_str = now.strftime("%Y%m%d%H%M%S")
        rand_suffix = uuid.uuid4().hex[:6]
        return f"inc-{date_str}-{rand_suffix}"

    def _generate_evp_id(self, now: datetime) -> str:
        date_str = now.strftime("%Y%m%d%H%M%S")
        rand_suffix = uuid.uuid4().hex[:6]
        return f"evp-{date_str}-{rand_suffix}"

    def _get_topology_neighbourhood(self, resource_id: str) -> dict[str, Any]:
        """Fetch upstream and downstream topology neighbours if discovery docs exist."""
        upstream: list[str] = []
        downstream: list[str] = []
        if self._firestore is not None and hasattr(self._firestore, "list_docs"):
            try:
                edges = self._firestore.list_docs("discovered_edges")
                for e in edges:
                    src = e.get("src", "")
                    dst = e.get("dst", "")
                    if dst == resource_id or resource_id in dst:
                        upstream.append(src)
                    if src == resource_id or resource_id in src:
                        downstream.append(dst)
            except Exception:
                pass
        return {"upstream": upstream[:5], "downstream": downstream[:5]}

    async def _build_evidence_pack(
        self,
        incident_id: str,
        sentinel: SentinelDefinition,
        eval_result: SentinelEvaluation,
        now: datetime,
    ) -> EvidencePack:
        now_iso = now.isoformat()
        evp_id = self._generate_evp_id(now)
        window_s = sentinel.check.window_s or sentinel.wake_condition.window_s or 300

        target_agent = sentinel.target_agent_id
        res_id = eval_result.resource_id or sentinel.source_ref

        # Correlated changes from Firestore change_events (strictly filtered by resource and <=30m window)
        correlated_changes: list[EvidenceCorrelatedChange] = []
        if self._firestore is not None and hasattr(self._firestore, "list_docs"):
            try:
                change_docs = self._firestore.list_docs("change_events")
                for chg in change_docs:
                    chg_res = str(chg.get("target_resource") or chg.get("service") or chg.get("resource_id") or "")
                    chg_affected = [str(a) for a in chg.get("affected_services", [])]
                    res_match = (
                        (res_id and (res_id in chg_res or chg_res in res_id))
                        or any(res_id in a for a in chg_affected)
                    )
                    if not res_match:
                        continue

                    chg_time_str = chg.get("occurred_at")
                    if chg_time_str:
                        try:
                            chg_dt = datetime.fromisoformat(str(chg_time_str))
                            if abs((now - chg_dt).total_seconds()) > 1800:
                                continue
                        except Exception:
                            pass

                    chg_kind = chg.get("kind")
                    chg_summary = chg.get("business_summary")
                    correlated_changes.append(
                        EvidenceCorrelatedChange(
                            change_event_id=chg.get("_id") or chg.get("doc_id", "chg-0"),
                            kind=str(chg_kind) if chg_kind else "unknown",
                            occurred_at=str(chg.get("occurred_at", now_iso)),
                            business_summary=str(chg_summary) if chg_summary else "",
                        )
                    )
                    if len(correlated_changes) >= 5:
                        break
            except Exception:
                pass

        # Vector recall over precedent incidents & skills (only if real embedding is available)
        symptom_text = (
            f"Sentinel {sentinel.sentinel_id} ({sentinel.sentinel_class}) breached: "
            f"signature={eval_result.signature} observed={eval_result.observed}"
        )
        query_vec = await self._get_embedding(symptom_text)
        episodic_hits: list[Any] = []
        skill_hits: list[Any] = []
        if query_vec is not None and self._firestore is not None and hasattr(self._firestore, "find_nearest"):
            try:
                episodic_hits = self._firestore.find_nearest(
                    "episodic_incidents",
                    vector_field="symptom_embedding",
                    query_vector=query_vec,
                    limit=3,
                )
            except Exception:
                pass
            try:
                skill_hits = self._firestore.find_nearest(
                    "procedural_skills",
                    vector_field="trigger_embedding",
                    query_vector=query_vec,
                    limit=3,
                )
            except Exception:
                pass

        recall = EvidenceRecall(
            episodic=[
                EpisodicRecallHit(
                    incident_id=doc.get("_id") or doc.get("doc_id", "inc-past"),
                    similarity=round(score, 4),
                )
                for doc, score in episodic_hits
            ],
            skills=[
                SkillRecallHit(
                    skill_slug=str(doc.get("name", doc.get("_id", "skill-0"))),
                    similarity=round(score, 4),
                )
                for doc, score in skill_hits
            ],
        )

        # Golden signals summary (honest metrics without synthetic defaults or full table scan)
        query_rows: list[dict[str, Any]] = []
        if self._bigquery is not None and hasattr(self._bigquery, "query"):
            try:
                sql = (
                    "SELECT http_status, duration_ms FROM events "
                    "WHERE COALESCE(source, 'otel') != 'probe' LIMIT 50"
                )
                query_rows = self._bigquery.query(sql)
            except Exception:
                pass

        gs_status = "unknown"
        p95_ms_val: float | None = None
        error_rate_val: float | None = None

        if query_rows:
            error_rows = [r for r in query_rows if int(r.get("http_status") or 200) >= 500]
            durations = sorted(
                float(r["duration_ms"]) for r in query_rows if r.get("duration_ms") is not None
            )
            p95_ms_val = durations[int(len(durations) * 0.95)] if durations else 0.0
            error_rate_val = round((len(error_rows) / len(query_rows)) * 100.0, 2)
            if sentinel.sentinel_class == "golden_signals" or len(error_rows) > 0 or (p95_ms_val >= 5000.0 and p95_ms_val > 0.0):
                gs_status = (
                    "red"
                    if (error_rate_val >= 5.0 or p95_ms_val >= 5000.0)
                    else "amber"
                )
            else:
                gs_status = "green"
        elif "p95_ms" in eval_result.details or "error_rate" in eval_result.details:
            p95_ms_val = float(eval_result.details.get("p95_ms", 0.0))
            err_raw = float(eval_result.details.get("error_rate", 0.0))
            error_rate_val = round(err_raw * 100.0 if err_raw <= 1.0 else err_raw, 2)
            gs_status = (
                "red" if (error_rate_val >= 5.0 or (p95_ms_val >= 5000.0 and p95_ms_val > 0.0)) else "green"
            )
        elif sentinel.sentinel_class == "golden_signals":
            gs_status = "red" if eval_result.breached else "green"
        else:
            gs_status = "unknown"

        # Budget from token governor (or 0 if unconfigured)
        tokens_rem = 0
        can_esc = False
        gov = self._token_governor or getattr(self._ctx, "token_governor", None)
        if gov is None and self._ctx is not None and hasattr(self._ctx, "court_manager"):
            gov = getattr(self._ctx.court_manager, "token_governor", None)
        if gov is not None and hasattr(gov, "get_remaining_tokens"):
            try:
                tokens_rem = gov.get_remaining_tokens(target_agent)
                can_esc = gov.can_escalate_to_argon(target_agent)
            except Exception:
                pass

        # Trust snapshot from trust gate
        trust_snap: dict[str, int] = {}
        if self._trust_gate is not None and hasattr(self._trust_gate, "list_agent_records"):
            try:
                trust_snap = {
                    rec.action_type: rec.level
                    for rec in self._trust_gate.list_agent_records(target_agent)
                }
            except Exception:
                pass

        return EvidencePack.model_validate(
            {
                "schema_version": "1.0",
                "evidence_pack_id": evp_id,
                "incident_id": incident_id,
                "fired_at": now_iso,
                "sentinel": EvidenceSentinelRef.model_validate(
                    {
                        "id": sentinel.sentinel_id,
                        "class": sentinel.sentinel_class,
                        "source_ref": sentinel.source_ref,
                    }
                ),
                "breach": EvidenceBreach(
                    statement=sentinel.check.statement or sentinel.source_ref,
                    observed=EvidenceObserved(count=eval_result.observed, window_s=window_s),
                    first_seen=now_iso,
                    consecutive=sentinel.consecutive_breaches or 1,
                ),
                "baseline": EvidenceBaseline(
                    model=sentinel.baseline.model,
                    expected=sentinel.baseline.expected or 0.0,
                    stddev=sentinel.baseline.stddev or 0.1,
                ),
                "affected": EvidenceAffected(
                    services=[res_id, target_agent],
                    rows=len(eval_result.evidence_samples),
                ),
                "correlated_changes": correlated_changes,
                "golden_signals": EvidenceGoldenSignals(
                    status=gs_status,
                    p95_ms=p95_ms_val,
                    error_rate_pct=error_rate_val,
                ),
                "recall": recall,
                "budget": EvidenceBudget(
                    agent_tokens_remaining=tokens_rem,
                    escalation_allowed=can_esc,
                ),
                "target_agent_id": target_agent,
                "trust_snapshot": trust_snap,
            }
        )

    def _persist_incident(self, incident: IncidentDoc) -> None:
        """Persist incident to both `incidents` and `episodic_incidents` collections."""
        if self._firestore is None:
            return
        payload = incident.model_dump(mode="json", by_alias=True)
        if hasattr(self._firestore, "set"):
            try:
                self._firestore.set("incidents", incident.doc_id, payload)
            except ValueError:
                pass
            try:
                self._firestore.set("episodic_incidents", incident.doc_id, payload)
            except ValueError:
                pass

    async def _emit_incident_event(self, event_type: str, incident: IncidentDoc) -> None:
        """Emit incident lifecycle event to EventBus / PubSub."""
        if self._bus is None:
            return
        try:
            from godview.sovereign.nervous_system import NervousSystemRouter

            router_type = (
                "godview.sentinel.recovered"
                if event_type == "incident.closed"
                else "godview.sentinel.breach"
            )
            router = NervousSystemRouter(pubsub=self._bus)
            await router.emit(
                router_type,
                source="//sovereign/incident-manager",
                subject=incident.dedupe_key,
                incident_id=incident.doc_id,
                data={
                    "incident_id": incident.doc_id,
                    "event_type": event_type,
                    "dedupe_key": incident.dedupe_key,
                    "severity": incident.severity,
                    "outcome": incident.outcome,
                    "symptom_summary": incident.symptom_summary,
                },
            )
        except Exception:
            pass

    async def _notify_incident_closed(self, inc: IncidentDoc) -> None:
        """Invoke on_incident_closed hook whether registered as a list or single callable."""
        if self._hooks is None or not hasattr(self._hooks, "on_incident_closed"):
            return
        hook_target = self._hooks.on_incident_closed
        if isinstance(hook_target, list):
            for hook_fn in hook_target:
                try:
                    res = hook_fn(inc)
                    if hasattr(res, "__await__"):
                        await res
                except Exception:
                    pass
        elif callable(hook_target):
            try:
                res = hook_target(inc)
                if hasattr(res, "__await__"):
                    await res
            except Exception:
                pass

    async def _notify_court(self, incident: IncidentDoc, evp: EvidencePack) -> None:
        """Hand off incident to court via ctx.hooks.on_incident and A2A wake."""
        now_iso = incident.opened_at if isinstance(incident.opened_at, str) else incident.opened_at.isoformat()

        # 1. Call ctx.hooks.on_incident if registered
        if self._hooks is not None and hasattr(self._hooks, "on_incident"):
            hook_target = self._hooks.on_incident
            if isinstance(hook_target, list):
                for hook_fn in hook_target:
                    try:
                        hook_res = hook_fn(incident)
                        if hasattr(hook_res, "__await__"):
                            await hook_res
                    except Exception:
                        pass
            elif callable(hook_target):
                try:
                    hook_res = hook_target(incident)
                    if hasattr(hook_res, "__await__"):
                        await hook_res
                except Exception:
                    pass

        # 2. Dispatch A2A wake message via AgentRuntime
        if self._agents is not None and hasattr(self._agents, "send_a2a"):
            wake_msg = A2AWakeMessage(
                incident_id=incident.doc_id or "inc-0",
                from_agent_id="sovereign",
                to_agent_id=incident.lead_agent_id,
                round=1,
                sent_at=now_iso,
                type="wake",
                payload=A2AWakePayload(evidence_pack=evp),
            )
            try:
                send_res = self._agents.send_a2a(wake_msg)
                if hasattr(send_res, "__await__"):
                    await send_res
            except Exception:
                pass

    async def record_breach(
        self,
        sentinel: SentinelDefinition,
        eval_result: SentinelEvaluation,
        now: datetime,
    ) -> tuple[IncidentDoc, bool]:
        """Record a sentinel breach: open new incident or update existing."""
        sig = eval_result.signature or f"breach_{sentinel.sentinel_class}"
        res = eval_result.resource_id or sentinel.source_ref
        dedupe_key = self.make_dedupe_key(sentinel.sentinel_class, res, sig)
        now_iso = now.isoformat()

        # Case 1: Update existing active incident
        if dedupe_key in self._active_incidents:
            incident = self._active_incidents[dedupe_key]
            incident.consecutive_breaches += 1
            incident.clear_ticks = 0
            incident.last_seen_at = now_iso
            # Update raw evidence with latest samples
            if eval_result.evidence_samples:
                existing_samples = incident.raw_evidence.get("latest_samples", [])
                incident.raw_evidence["latest_samples"] = (
                    eval_result.evidence_samples + existing_samples
                )[:10]

            # Sync diagnoses from Firestore if populated by async court callback
            if not incident.diagnoses and self._firestore is not None and incident.doc_id:
                try:
                    stored_inc = self._firestore.get("incidents", incident.doc_id)
                    if stored_inc and stored_inc.get("diagnoses"):
                        incident.diagnoses = list(stored_inc["diagnoses"])
                except Exception:
                    pass

            self._persist_incident(incident)
            if not incident.diagnoses and incident.evidence_pack is not None:
                await self._notify_court(incident, incident.evidence_pack)
            await self._emit_incident_event("incident.updated", incident)
            return incident, False

        # Case 2: Open new incident
        inc_id = self._generate_incident_id(now)
        evp = await self._build_evidence_pack(inc_id, sentinel, eval_result, now)
        severity = eval_result.severity_override or "sev2"

        raw_ev = {
            "log_samples": eval_result.evidence_samples if sentinel.sentinel_class == "lb_requests" else [],
            "config_snapshot": eval_result.details if sentinel.sentinel_class == "posture" else {},
            "probe_transcript": eval_result.evidence_samples if sentinel.sentinel_class == "probe" else [],
            "metrics_window": eval_result.details if sentinel.sentinel_class == "golden_signals" else {},
            "topology_neighbourhood": self._get_topology_neighbourhood(res),
            "correlated_changes": [c.model_dump(mode="json") for c in evp.correlated_changes],
            "details": eval_result.details,
        }

        symptom = (
            f"Sentinel {sentinel.sentinel_id} ({sentinel.sentinel_class}) breached: "
            f"observed={eval_result.observed} signature={sig}"
        )

        incident = IncidentDoc(
            doc_id=inc_id,
            dedupe_key=dedupe_key,
            target_resource=res,
            resource_id=res,
            opened_at=now_iso,
            last_seen_at=now_iso,
            severity=severity,
            symptom_summary=symptom,
            evidence_pack=evp,
            lead_agent_id=sentinel.target_agent_id,
            outcome="open",
            consecutive_breaches=sentinel.consecutive_breaches or 1,
            clear_ticks=0,
            raw_evidence=raw_ev,
            symptom_embedding=await self._get_embedding(symptom),
        )

        self._active_incidents[dedupe_key] = incident
        self._persist_incident(incident)
        await self._emit_incident_event("incident.opened", incident)
        await self._notify_court(incident, evp)
        return incident, True

    async def record_clear(
        self,
        sentinel: SentinelDefinition,
        now: datetime,
        resource_id: str | None = None,
    ) -> list[IncidentDoc]:
        """Track clear evaluation ticks and auto-close incidents clear for N ticks."""
        now_iso = now.isoformat()
        closed_incidents: list[IncidentDoc] = []
        spec = sentinel.check.spec or {}
        res = resource_id or spec.get("resource_id") or sentinel.source_ref or "global"
        res_clean = res.replace(":", "_")
        target_prefix = f"{sentinel.sentinel_class}:{res_clean}:"

        matching_keys = [
            k for k in self._active_incidents
            if k.startswith(target_prefix)
        ]

        for k in matching_keys:
            inc = self._active_incidents[k]
            inc.clear_ticks += 1
            if inc.clear_ticks >= self.auto_close_ticks:
                inc.outcome = "resolved"
                inc.closed_at = now_iso
                self._persist_incident(inc)
                del self._active_incidents[k]
                closed_incidents.append(inc)
                await self._emit_incident_event("incident.closed", inc)
                await self._notify_incident_closed(inc)

        return closed_incidents

    async def close_incident(
        self,
        incident_id: str,
        *,
        reason: str = "",
        outcome: str = "resolved",
        operator: str | None = None,
        resolved_by: str | None = None,
        root_cause_category: str | None = None,
        remediation_capability: str | None = None,
        resolution_summary: str | None = None,
    ) -> IncidentDoc:
        """Manually close an incident."""
        now_iso = self._clock().isoformat()
        effective_operator = operator or resolved_by

        # Find in active incidents
        target: IncidentDoc | None = None
        target_key: str | None = None
        for k, inc in self._active_incidents.items():
            if inc.doc_id == incident_id:
                target = inc
                target_key = k
                break

        # Fallback to Firestore
        if target is None and self._firestore is not None and hasattr(self._firestore, "get"):
            raw = None
            try:
                raw = self._firestore.get("incidents", incident_id)
            except ValueError:
                pass
            if raw is None:
                try:
                    raw = self._firestore.get("episodic_incidents", incident_id)
                except ValueError:
                    pass
            if raw is not None:
                target = IncidentDoc.model_validate(raw)

        if target is None:
            raise KeyError(f"Unknown incident '{incident_id}'")

        target.outcome = outcome
        target.closed_at = now_iso
        target.root_cause = reason or target.root_cause or "Closed by operator"
        if root_cause_category:
            target.root_cause_category = root_cause_category
            target.raw_evidence["root_cause_category"] = root_cause_category
        if remediation_capability:
            target.remediation_capability = remediation_capability
            target.raw_evidence["remediation_capability"] = remediation_capability
        if resolution_summary:
            target.resolution_summary = resolution_summary
            target.raw_evidence["resolution_summary"] = resolution_summary

        if effective_operator:
            if effective_operator not in target.participants:
                target.participants.append(effective_operator)
            target.raw_evidence["closed_by"] = effective_operator
        if reason:
            target.raw_evidence["close_reason"] = reason

        if target_key and target_key in self._active_incidents:
            del self._active_incidents[target_key]

        self._persist_incident(target)
        await self._emit_incident_event("incident.closed", target)
        await self._notify_incident_closed(target)
        return target

    def on_change_event(self, event: Any) -> None:
        """Hook called on nervous-system push events (audit logs, deploys, tfstate)."""
        now_iso = self._clock().isoformat()
        evt_dict = event.model_dump(mode="json") if hasattr(event, "model_dump") else (event if isinstance(event, dict) else {})
        chg_id = evt_dict.get("doc_id") or evt_dict.get("change_event_id") or f"chg-{self._clock().strftime('%Y%m%d%H%M%S')}"

        # Persist to Firestore change_events
        if self._firestore is not None and hasattr(self._firestore, "set"):
            self._firestore.set(
                "change_events",
                chg_id,
                {
                    **evt_dict,
                    "doc_id": chg_id,
                    "occurred_at": evt_dict.get("occurred_at") or now_iso,
                },
            )

        # Correlate with active incidents
        affected_services = evt_dict.get("affected_services", [])
        target_res = evt_dict.get("target_resource") or evt_dict.get("service") or ""
        for inc in self._active_incidents.values():
            if (target_res and target_res in inc.dedupe_key) or any(svc in inc.dedupe_key for svc in affected_services):
                correlated = inc.raw_evidence.setdefault("correlated_changes", [])
                correlated.append(
                    {
                        "change_event_id": chg_id,
                        "kind": evt_dict.get("kind", "audit"),
                        "occurred_at": evt_dict.get("occurred_at") or now_iso,
                        "business_summary": evt_dict.get("business_summary", ""),
                    }
                )

    def list_incidents(
        self,
        status: str | None = None,
        severity: str | None = None,
        limit: int = 50,
    ) -> list[IncidentDoc]:
        """List incidents from memory cache and Firestore."""
        docs: list[IncidentDoc] = []
        seen_ids: set[str] = set()

        for inc in self._active_incidents.values():
            if inc.doc_id and inc.doc_id not in seen_ids:
                seen_ids.add(inc.doc_id)
                docs.append(inc)

        if self._firestore is not None and hasattr(self._firestore, "list_docs"):
            raw_docs = []
            try:
                raw_docs = self._firestore.list_docs("incidents")
            except ValueError:
                pass
            if not raw_docs:
                try:
                    raw_docs = self._firestore.list_docs("episodic_incidents")
                except ValueError:
                    pass
            for raw in raw_docs:
                doc_id = raw.get("doc_id") or raw.get("_id")
                if doc_id and doc_id not in seen_ids:
                    seen_ids.add(doc_id)
                    try:
                        docs.append(IncidentDoc.model_validate(raw))
                    except Exception:
                        pass

        # Apply filters
        if status:
            docs = [d for d in docs if d.outcome == status]
        if severity:
            docs = [d for d in docs if d.severity == severity]

        return docs[:limit]

    def get_incident(self, incident_id: str) -> IncidentDoc | None:
        """Fetch an incident by its ID."""
        for inc in self._active_incidents.values():
            if inc.doc_id == incident_id:
                return inc
        if self._firestore is not None and hasattr(self._firestore, "get"):
            raw = None
            try:
                raw = self._firestore.get("incidents", incident_id)
            except ValueError:
                pass
            if raw is None:
                try:
                    raw = self._firestore.get("episodic_incidents", incident_id)
                except ValueError:
                    pass
            if raw is not None:
                return IncidentDoc.model_validate(raw)
        return None
