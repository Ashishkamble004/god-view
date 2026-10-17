"""In-memory Vertex AI Agent Engine fake (ReasoningEngine, earned warmth, Memory Bank, A2A)."""

from __future__ import annotations

import copy
import inspect
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from godview_shared.contracts import A2AAgentCard, A2AMessage, parse_a2a_message

A2AHandler = Callable[[A2AMessage], dict[str, Any] | Awaitable[dict[str, Any]]]


class InMemoryAgentEngine:
    """Hermetic fake for Vertex AI Agent Engine (us-central1)."""

    def __init__(self) -> None:
        self._agents: dict[str, dict[str, Any]] = {}
        self._memory_banks: dict[str, dict[str, Any]] = {}
        self._a2a_handlers: dict[str, A2AHandler] = {}
        self._a2a_log: list[dict[str, Any]] = []

    def deploy_agent(
        self,
        agent_id: str,
        agent_type: str,
        min_instances: int = 0,
        max_instances: int = 1,
        cpu: str = "1",
        memory: str = "2Gi",
        card: A2AAgentCard | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Simulate deploying an ADK ReasoningEngine with deployment_spec.min_instances=0."""
        if min_instances not in range(0, 76):
            raise ValueError(f"min_instances must be in [0, 75] (got {min_instances})")
        resource_name = f"projects/hermetic/locations/us-central1/reasoningEngines/{agent_id}"
        card_url = f"https://agent-engine.hermetic.invalid/a2a/{agent_id}/card.json"
        record = {
            "agent_id": agent_id,
            "agent_type": agent_type,
            "reasoning_engine": resource_name,
            "a2a_card_url": card_url,
            "memory_bank_id": f"mb-{agent_id}",
            "deployment_spec": {
                "min_instances": min_instances,
                "max_instances": max_instances,
                "resource_limits": {"cpu": cpu, "memory": memory},
                "container_concurrency": 4,
            },
            "card": (
                card.model_dump(mode="json")
                if isinstance(card, A2AAgentCard)
                else copy.deepcopy(card)
            ),
            "deployed_at": datetime.now(tz=UTC).isoformat(),
            "state": "ACTIVE",
        }
        self._agents[agent_id] = record
        self._memory_banks.setdefault(agent_id, {})
        return copy.deepcopy(record)

    def set_min_instances(self, agent_id: str, min_instances: int) -> dict[str, Any]:
        """Simulate Sovereign PATCH deploymentSpec.minInstances (0 <-> 1 earned warmth)."""
        if agent_id not in self._agents:
            raise KeyError(f"Unknown ReasoningEngine agent_id '{agent_id}'")
        if min_instances not in (0, 1):
            raise ValueError(f"Earned warmth min_instances must be 0 or 1 (got {min_instances})")
        self._agents[agent_id]["deployment_spec"]["min_instances"] = min_instances
        return copy.deepcopy(self._agents[agent_id])

    def retire_agent(self, agent_id: str) -> dict[str, Any]:
        """Mark a ReasoningEngine retired and cool min_instances to 0."""
        if agent_id not in self._agents:
            raise KeyError(f"Unknown ReasoningEngine agent_id '{agent_id}'")
        self._agents[agent_id]["deployment_spec"]["min_instances"] = 0
        self._agents[agent_id]["state"] = "RETIRED"
        return copy.deepcopy(self._agents[agent_id])

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        """Return deployed ReasoningEngine metadata."""
        if agent_id not in self._agents:
            raise KeyError(f"Unknown ReasoningEngine agent_id '{agent_id}'")
        return copy.deepcopy(self._agents[agent_id])

    def list_agents(self) -> list[dict[str, Any]]:
        """List all deployed ReasoningEngines."""
        return [copy.deepcopy(a) for a in self._agents.values()]

    def write_memory_bank(self, agent_id: str, key: str, value: Any) -> None:
        """Write to an agent's private Memory Bank."""
        if agent_id not in self._memory_banks:
            self._memory_banks[agent_id] = {}
        self._memory_banks[agent_id][key] = copy.deepcopy(value)

    def read_memory_bank(self, agent_id: str) -> dict[str, Any]:
        """Read an agent's private Memory Bank."""
        return copy.deepcopy(self._memory_banks.get(agent_id, {}))

    def register_a2a_handler(self, agent_id: str, handler: A2AHandler) -> None:
        """Register a handler for A2A messages sent to agent_id."""
        self._a2a_handlers[agent_id] = handler

    async def send_a2a(self, message: A2AMessage | dict[str, Any]) -> dict[str, Any]:
        """Validate and dispatch an A2A message to the target agent."""
        parsed = message if not isinstance(message, dict) else parse_a2a_message(message)
        self._a2a_log.append(parsed.model_dump(mode="json", by_alias=True))
        handler = self._a2a_handlers.get(parsed.to_agent_id)
        if handler is not None:
            res = handler(parsed)
            if inspect.isawaitable(res):
                return await res
            return res
        return {
            "status": "delivered",
            "to_agent_id": parsed.to_agent_id,
            "type": parsed.type,
            "round": parsed.round,
        }

    def get_a2a_log(self) -> list[dict[str, Any]]:
        """Return all dispatched A2A messages."""
        return copy.deepcopy(self._a2a_log)
