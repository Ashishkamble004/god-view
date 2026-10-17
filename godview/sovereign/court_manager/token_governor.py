"""Token-Budget Governor (`godview.sovereign.court_manager.token_governor`).

Enforces TDD I-08, I-09, UC-22, and Coordinator Decision A9:
- Application / Business-Line / Change agents: $0.75/day each
- Data / Platform agents: $0.50/day each
- Sovereign: $1.50/day
- Court daily ceiling: $6.00/day, gemini-4-argon escalation reserve 20% ($1.20)
- Refuses to start without valid positive budget numbers
- Exhaustion degrades that agent to L1 until 00:00 IST
- Court ceiling exhaustion degrades all agents to L1 and emits a notice
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from godview_shared.config import (
    DEFAULT_AGENT_DAILY_BUDGETS_USD,
    DEFAULT_AGENT_FALLBACK_MODEL_ID,
    DEFAULT_AGENT_MODEL_ID,
    DEFAULT_AGENT_MODEL_LOCATION,
    DEFAULT_COURT_DAILY_CEILING_USD,
    DEFAULT_ESCALATION_RESERVE_FRACTION,
    validate_model_location,
)
from godview_shared.fakes import InMemoryFirestore, InMemoryPubSub

from godview.sovereign.nervous_system import NervousSystemRouter

REQUIRED_BUDGET_TYPES: tuple[str, ...] = (
    "application",
    "business_line",
    "change",
    "data",
    "platform",
    "sovereign",
)


class TokenBudgetGovernor:
    """Per-agent daily token/USD budget governor and Argon escalation reserve guard."""

    def __init__(
        self,
        firestore: InMemoryFirestore,
        pubsub: InMemoryPubSub,
        agent_daily_budgets_usd: Mapping[str, Decimal] | None = None,
        court_daily_ceiling_usd: Decimal | None = None,
        escalation_reserve_fraction: Decimal = DEFAULT_ESCALATION_RESERVE_FRACTION,
    ) -> None:
        budgets = (
            dict(agent_daily_budgets_usd)
            if agent_daily_budgets_usd is not None
            else dict(DEFAULT_AGENT_DAILY_BUDGETS_USD)
        )
        ceiling = (
            court_daily_ceiling_usd
            if court_daily_ceiling_usd is not None
            else DEFAULT_COURT_DAILY_CEILING_USD
        )

        if not budgets or any(k not in budgets for k in REQUIRED_BUDGET_TYPES):
            raise ValueError(
                f"TokenBudgetGovernor refuses to start without valid daily budget numbers for {REQUIRED_BUDGET_TYPES} (Decision A9)"
            )
        if any(Decimal(str(v)) <= Decimal("0") for v in budgets.values()):
            raise ValueError("All agent daily token budgets must be > 0 (Decision A9)")
        if Decimal(str(ceiling)) <= Decimal("0"):
            raise ValueError("court_daily_ceiling_usd must be > 0 (Decision A9)")

        self._firestore = firestore
        self._pubsub = pubsub
        self._router = NervousSystemRouter(pubsub=pubsub)
        self._agent_budgets_usd: dict[str, Decimal] = {
            k: Decimal(str(v)) for k, v in budgets.items()
        }
        self.court_daily_ceiling_usd: Decimal = Decimal(str(ceiling))
        self.escalation_reserve_fraction: Decimal = Decimal(str(escalation_reserve_fraction))
        self.escalation_reserve_usd: Decimal = (
            self.court_daily_ceiling_usd * self.escalation_reserve_fraction
        ).quantize(Decimal("0.01"))

        self._usd_spent_by_agent: dict[str, Decimal] = {}
        self._tokens_spent_by_agent: dict[str, int] = {}
        self._degraded_agents: set[str] = set()
        self.court_exhausted: bool = False

    def _agent_type_from_id(self, agent_id: str) -> str:
        if agent_id == "sovereign":
            return "sovereign"
        if "__" in agent_id:
            prefix = agent_id.split("__", 1)[0]
            if prefix in self._agent_budgets_usd:
                return prefix
        return "application"

    def get_daily_budget_usd(self, agent_id: str) -> Decimal:
        """Return the daily USD budget for agent_id based on its specialist type."""
        agent_type = self._agent_type_from_id(agent_id)
        return self._agent_budgets_usd[agent_type]

    def get_usd_spent(self, agent_id: str) -> Decimal:
        """Return the USD spent today by agent_id."""
        return self._usd_spent_by_agent.get(agent_id, Decimal("0.00"))

    def get_tokens_spent(self, agent_id: str) -> int:
        """Return the tokens spent today by agent_id."""
        return self._tokens_spent_by_agent.get(agent_id, 0)

    def get_remaining_tokens(self, agent_id: str, default_daily_tokens: int = 50000) -> int:
        """Estimate remaining tokens today for agent_id from its USD and token spend."""
        if self.is_agent_degraded_to_l1(agent_id):
            return 0
        budget = self.get_daily_budget_usd(agent_id)
        spent_usd = self.get_usd_spent(agent_id)
        if budget <= Decimal("0") or spent_usd >= budget:
            return 0
        ratio = float((budget - spent_usd) / budget)
        by_usd = int(round(default_daily_tokens * ratio))
        by_tokens = max(0, default_daily_tokens - self.get_tokens_spent(agent_id))
        return max(0, min(by_usd, by_tokens))

    @property
    def total_court_usd_spent(self) -> Decimal:
        return sum(self._usd_spent_by_agent.values(), start=Decimal("0.00"))

    def is_agent_degraded_to_l1(self, agent_id: str) -> bool:
        """Return True if agent_id or the entire court has exhausted its daily budget."""
        return self.court_exhausted or (agent_id in self._degraded_agents)

    def effective_trust_level(self, agent_id: str, nominal_level: int) -> int:
        """Clamp an agent's trust level to at most L1 if its budget is exhausted (Decision A9)."""
        if self.is_agent_degraded_to_l1(agent_id):
            return min(nominal_level, 1)
        return nominal_level

    def can_escalate_to_argon(self, agent_id: str) -> bool:
        """Return True only if agent is not degraded and remaining court ceiling >= 20% reserve ($1.20)."""
        if self.is_agent_degraded_to_l1(agent_id):
            return False
        remaining_court = self.court_daily_ceiling_usd - self.total_court_usd_spent
        return remaining_court >= self.escalation_reserve_usd

    def resolve_agent_model(
        self, agent_id: str, *, quota_or_429_fallback: bool = False
    ) -> tuple[str, str]:
        """Return (model_id, location) for a God-View agent call.

        Defaults to Argon ('gemini-4-argon' @ 'global'), falling back to
        'gemini-3.8-flash' @ 'global' only on HTTP 429 / quota / budget reserve exhaustion.
        """
        if quota_or_429_fallback or not self.can_escalate_to_argon(agent_id):
            return DEFAULT_AGENT_FALLBACK_MODEL_ID, DEFAULT_AGENT_MODEL_LOCATION
        return DEFAULT_AGENT_MODEL_ID, DEFAULT_AGENT_MODEL_LOCATION

    async def record_usage(
        self,
        *,
        agent_id: str,
        model_id: str,
        location: str,
        tokens: int,
        usd_cost: Decimal,
    ) -> dict[str, str | int | bool]:
        """Meter a model call, enforcing location rules (I-09) and daily budgets (I-08, A9)."""
        validate_model_location(model_id, location)
        if tokens < 0 or usd_cost < Decimal("0"):
            raise ValueError("tokens and usd_cost must be non-negative")

        prev_usd = self._usd_spent_by_agent.get(agent_id, Decimal("0.00"))
        new_usd = prev_usd + Decimal(str(usd_cost))
        self._usd_spent_by_agent[agent_id] = new_usd
        self._tokens_spent_by_agent[agent_id] = (
            self._tokens_spent_by_agent.get(agent_id, 0) + tokens
        )

        budget = self.get_daily_budget_usd(agent_id)
        if new_usd >= budget and agent_id not in self._degraded_agents:
            self._degraded_agents.add(agent_id)
            await self._router.emit(
                "godview.operator.note",
                source="//sovereign/token-governor",
                subject=agent_id,
                data={
                    "kind": "agent_budget_exhausted",
                    "agent_id": agent_id,
                    "usd_spent_today": str(new_usd),
                    "daily_budget_usd": str(budget),
                    "degraded_to_level": 1,
                },
            )

        if self.total_court_usd_spent >= self.court_daily_ceiling_usd and not self.court_exhausted:
            self.court_exhausted = True
            await self._router.emit(
                "godview.operator.note",
                source="//sovereign/token-governor",
                subject="court",
                data={
                    "kind": "court_daily_ceiling_exhausted",
                    "total_usd_spent_today": str(self.total_court_usd_spent),
                    "court_daily_ceiling_usd": str(self.court_daily_ceiling_usd),
                    "degraded_all_to_level": 1,
                },
            )

        return {
            "agent_id": agent_id,
            "tokens_spent_today": self._tokens_spent_by_agent[agent_id],
            "usd_spent_today": str(new_usd),
            "degraded_to_l1": self.is_agent_degraded_to_l1(agent_id),
        }

    def reset_daily_counters_ist(self) -> None:
        """Reset per-agent and court daily counters at 00:00 IST."""
        self._usd_spent_by_agent.clear()
        self._tokens_spent_by_agent.clear()
        self._degraded_agents.clear()
        self.court_exhausted = False
