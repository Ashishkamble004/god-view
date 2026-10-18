"""Court Manager (`godview.sovereign.court_manager`).

Manages the specialist agent court lifecycle (spawn, brief, wake, retire, archive),
enforces Coordinator Decision A1 (`GODVIEW_COURT_MAX_AGENTS=40` hard cap),
evaluates earned warmth (`min_instances=1` inside `09:00–17:00 IST` for top 2–3
hottest agents, cooled to `0` outside), and enforces swarm convergence exit
conditions (consensus >= 0.85, <= 4 rounds, <= 90 s).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from godview_shared.config import DEFAULT_COURT_MAX_AGENTS
from godview_shared.contracts import CourtAgentDoc, CourtAgentType
from godview_shared.fakes import InMemoryAgentEngine, InMemoryFirestore, InMemoryPubSub

from godview.sovereign.court_manager.token_governor import TokenBudgetGovernor
from godview.sovereign.nervous_system import NervousSystemRouter

IST = ZoneInfo("Asia/Kolkata")


class CourtCapacityError(RuntimeError):
    """Raised when spawning an agent would exceed GODVIEW_COURT_MAX_AGENTS (Decision A1)."""


class CourtManager:
    """Lifecycle, warmth, and swarm convergence manager for the specialist agent court."""

    def __init__(
        self,
        firestore: InMemoryFirestore,
        pubsub: InMemoryPubSub,
        agent_engine: InMemoryAgentEngine,
        *,
        max_agents: int = DEFAULT_COURT_MAX_AGENTS,
        max_warm_agents: int = 2,
        token_governor: TokenBudgetGovernor | None = None,
    ) -> None:
        if max_agents < 1 or max_agents > DEFAULT_COURT_MAX_AGENTS:
            raise ValueError(
                f"max_agents={max_agents} violates hard cap {DEFAULT_COURT_MAX_AGENTS} (Decision A1)"
            )
        self._firestore = firestore
        self._pubsub = pubsub
        self._agent_engine = agent_engine
        self.max_agents = max_agents
        self.max_warm_agents = max(1, min(max_warm_agents, 3))
        self.token_governor = token_governor or TokenBudgetGovernor(
            firestore=firestore, pubsub=pubsub
        )
        self._router = NervousSystemRouter(pubsub=pubsub)

    def list_active_agents(self) -> list[CourtAgentDoc]:
        """Return all non-archived court agents from Firestore."""
        docs = self._firestore.list_docs(
            "court_agents",
            predicate=lambda d: (
                d.get("state") not in ("archived", "retiring")
                and "agent_type" in d
                and "subject_ref" in d
            ),
        )
        return [CourtAgentDoc.model_validate(d) for d in docs]

    def get_agent(self, agent_id: str) -> CourtAgentDoc | None:
        """Fetch a single court agent document by ID."""
        raw = self._firestore.get("court_agents", agent_id)
        if raw is None or "agent_type" not in raw or "subject_ref" not in raw:
            return None
        return CourtAgentDoc.model_validate(raw)

    async def spawn_agent(
        self,
        agent_type: CourtAgentType,
        subject_ref: str,
    ) -> CourtAgentDoc:
        """Spawn or update a specialist court agent, enforcing the Decision A1 cap."""
        agent_id = f"{agent_type}__{subject_ref}"
        existing = self.get_agent(agent_id)
        if existing is not None and existing.state not in ("archived", "retiring"):
            return existing

        active = self.list_active_agents()
        if len(active) >= self.max_agents:
            raise CourtCapacityError(
                f"Cannot spawn '{agent_id}': court at capacity (max_agents={self.max_agents}, Decision A1)"
            )

        now_iso = datetime.now(tz=UTC).isoformat()
        deployed = await asyncio.to_thread(
            self._agent_engine.deploy_agent,
            agent_id=agent_id,
            agent_type=agent_type,
            min_instances=0,
        )
        daily_budget = self.token_governor.get_daily_budget_usd(agent_id)
        doc = CourtAgentDoc(
            doc_id=agent_id,
            agent_type=agent_type,
            subject_ref=subject_ref,
            reasoning_engine=deployed["reasoning_engine"],
            a2a_card_url=deployed["a2a_card_url"],
            state="sleeping",
            warmth_min_instances=0,
            memory_bank_id=f"mem-{agent_id}",
            namespace=agent_id,
            token_budget_daily=daily_budget,
            tokens_spent_today=0,
            wakes_7d=0,
            spawned_at=now_iso,
        )
        self._firestore.set("court_agents", agent_id, doc.model_dump(mode="json"))
        await self._router.emit(
            "godview.court.spawn",
            source="//sovereign/court-manager",
            subject=agent_id,
            data={"agent_id": agent_id, "agent_type": agent_type, "subject_ref": subject_ref},
        )
        return doc

    async def retire_agent(self, agent_id: str) -> CourtAgentDoc:
        """Consolidate private working memory to Firestore, cool to 0, and archive agent."""
        existing = self.get_agent(agent_id)
        if existing is None:
            raise KeyError(f"Unknown court agent '{agent_id}'")

        # Consolidate private Memory Bank into Firestore living_model before retiring
        private_mem = self._agent_engine.read_memory_bank(agent_id)
        if private_mem:
            self._firestore.set_subdoc(
                "court_agents",
                agent_id,
                "living_model",
                "rules",
                {
                    "section": "rules",
                    "items": [{"key": k, "value": v} for k, v in private_mem.items()],
                    "updated_at": datetime.now(tz=UTC).isoformat(),
                },
            )

        await asyncio.to_thread(self._agent_engine.retire_agent, agent_id)
        now_iso = datetime.now(tz=UTC).isoformat()
        updated = existing.model_copy(
            update={
                "state": "archived",
                "warmth_min_instances": 0,
                "retired_at": now_iso,
            }
        )
        self._firestore.set("court_agents", agent_id, updated.model_dump(mode="json"))
        await self._router.emit(
            "godview.court.retire",
            source="//sovereign/court-manager",
            subject=agent_id,
            data={"agent_id": agent_id, "retired_at": now_iso},
        )
        return updated

    async def set_warmth(self, agent_id: str, min_instances: Literal[0, 1]) -> CourtAgentDoc:
        """Set Agent Engine deploymentSpec.minInstances (0 <-> 1) and persist warmth."""
        existing = self.get_agent(agent_id)
        if existing is None:
            raise KeyError(f"Unknown court agent '{agent_id}'")
        await asyncio.to_thread(self._agent_engine.set_min_instances, agent_id, min_instances)
        updated = existing.model_copy(update={"warmth_min_instances": min_instances})
        self._firestore.set("court_agents", agent_id, updated.model_dump(mode="json"))
        await self._router.emit(
            "godview.court.warm",
            source="//sovereign/court-manager",
            subject=agent_id,
            data={"agent_id": agent_id, "warmth_min_instances": min_instances},
        )
        return updated

    async def cool_all_agents(self) -> int:
        """Cool every active court agent to min_instances=0 (Scheduled Pause / Hibernate)."""
        for doc in self.list_active_agents():
            aid = doc.doc_id or doc.namespace
            if doc.warmth_min_instances != 0:
                await self.set_warmth(aid, 0)
            else:
                await asyncio.to_thread(self._agent_engine.set_min_instances, aid, 0)
        return 0

    async def evaluate_earned_warmth(self, now_ist: datetime | None = None) -> list[str]:
        """Warm top max_warm_agents by wakes_7d inside 09:00-17:00 IST; cool all outside (UC-19, UC-21)."""
        current_ist = (now_ist or datetime.now(tz=IST)).astimezone(IST)
        in_window = 9 <= current_ist.hour < 17

        active = self.list_active_agents()
        if not in_window:
            for doc in active:
                aid = doc.doc_id or doc.namespace
                await self.set_warmth(aid, 0)
            return []

        ranked = sorted(
            active,
            key=lambda d: (d.wakes_7d, d.doc_id or d.namespace),
            reverse=True,
        )
        warm_targets = {
            (d.doc_id or d.namespace) for d in ranked[: self.max_warm_agents] if d.wakes_7d > 0
        }
        warmed_ids: list[str] = []
        for doc in active:
            aid = doc.doc_id or doc.namespace
            target_warmth: Literal[0, 1] = 1 if aid in warm_targets else 0
            await self.set_warmth(aid, target_warmth)
            if target_warmth == 1:
                warmed_ids.append(aid)
        return warmed_ids

    @staticmethod
    def check_swarm_exit(
        *, consensus: float, rounds: int, elapsed_seconds: float
    ) -> tuple[bool, str]:
        """Evaluate swarm convergence exit conditions (TDD §3.5: >=0.85, <=4 rounds, <=90 s)."""
        if consensus >= 0.85:
            return True, "consensus_reached"
        if rounds >= 4:
            return True, "max_rounds_reached"
        if elapsed_seconds >= 90.0:
            return True, "timeout_reached"
        return False, "continue"


__all__ = ["CourtCapacityError", "CourtManager", "TokenBudgetGovernor"]
