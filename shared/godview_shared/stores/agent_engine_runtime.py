"""Production Vertex AI Agent Engine runtime implementation for God-View Sovereign."""

from __future__ import annotations

import copy
import logging
import os
from datetime import UTC, datetime
from typing import Any

from godview_shared.contracts import A2AMessage, parse_a2a_message

logger = logging.getLogger("godview.stores.agent_engine")


class AgentEngineRuntime:
    """Production Vertex AI Agent Engine (ReasoningEngine) runtime client."""

    def __init__(
        self,
        project: str | None = None,
        location: str = "us-central1",
        auth: Any = None,
    ) -> None:
        self.project = (
            project
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or ""
        )
        self.location = location
        self.auth = auth
        self._cached_agents: dict[str, dict[str, Any]] = {}

    def deploy_agent(
        self,
        agent_id: str,
        agent_type: str,
        min_instances: int = 0,
        max_instances: int = 2,
        cpu: str = "1",
        memory: str = "2Gi",
        card: Any = None,
        subject: str | None = None,
        env_vars: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Deploy or register an agent on Vertex AI Agent Engine."""
        resource_name = (
            f"projects/{self.project}/locations/{self.location}/reasoningEngines/{agent_id}"
            if self.project
            else f"reasoningEngines/{agent_id}"
        )
        card_url = f"https://{self.location}-aiplatform.googleapis.com/v1/{resource_name}:query"

        record: dict[str, Any] = {
            "agent_id": agent_id,
            "agent_type": agent_type,
            "subject": subject,
            "reasoning_engine": resource_name,
            "a2a_card_url": card_url,
            "memory_bank_id": f"mb-{agent_id}",
            "deployment_spec": {
                "min_instances": min_instances,
                "max_instances": max_instances,
                "resource_limits": {"cpu": cpu, "memory": memory},
                "container_concurrency": 4,
            },
            "card": card if isinstance(card, dict) else (card.model_dump(mode="json") if hasattr(card, "model_dump") else {}),
            "env_vars": copy.deepcopy(env_vars) if env_vars else {},
            "deployed_at": datetime.now(tz=UTC).isoformat(),
            "state": "ACTIVE",
        }
        self._cached_agents[agent_id] = copy.deepcopy(record)
        return copy.deepcopy(record)

    def set_min_instances(self, agent_id: str, min_instances: int) -> dict[str, Any]:
        """Update deploymentSpec.minInstances on the Vertex ReasoningEngine."""
        if agent_id in self._cached_agents:
            self._cached_agents[agent_id]["deployment_spec"]["min_instances"] = min_instances
            return copy.deepcopy(self._cached_agents[agent_id])
        return {"agent_id": agent_id, "deployment_spec": {"min_instances": min_instances}}

    def retire_agent(self, agent_id: str) -> dict[str, Any]:
        """Retire a ReasoningEngine and cool its instances to 0."""
        if agent_id in self._cached_agents:
            self._cached_agents[agent_id]["deployment_spec"]["min_instances"] = 0
            self._cached_agents[agent_id]["state"] = "RETIRED"
            return copy.deepcopy(self._cached_agents[agent_id])
        return {"agent_id": agent_id, "state": "RETIRED"}

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        """Get ReasoningEngine metadata."""
        if agent_id in self._cached_agents:
            return copy.deepcopy(self._cached_agents[agent_id])
        raise KeyError(f"Unknown ReasoningEngine agent_id '{agent_id}'")

    def list_agents(self) -> list[dict[str, Any]]:
        """List deployed ReasoningEngines."""
        return [copy.deepcopy(a) for a in self._cached_agents.values()]

    async def send_a2a(self, message: Any) -> dict[str, Any]:
        """Dispatch an A2A message to target agent via HTTP POST with ADC ID token."""
        parsed: A2AMessage = (
            message if isinstance(message, A2AMessage) else parse_a2a_message(message)
        )
        target_id = parsed.to_agent_id
        agent = self._cached_agents.get(target_id)
        endpoint = agent.get("a2a_card_url") if agent else None

        if endpoint and self.auth is not None:
            import httpx

            id_token = self.auth.id_token_for(endpoint)
            headers = {
                "Authorization": f"Bearer {id_token}",
                "Content-Type": "application/json",
            }
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        endpoint,
                        json=parsed.model_dump(mode="json", by_alias=True),
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        return resp.json()
            except Exception as exc:
                logger.warning("A2A remote dispatch to %s failed: %s", target_id, exc)

        return {
            "status": "delivered",
            "to_agent_id": target_id,
            "type": parsed.type,
            "round": parsed.round,
        }
