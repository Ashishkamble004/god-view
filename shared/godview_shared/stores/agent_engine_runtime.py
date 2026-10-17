"""Production Vertex AI Agent Engine runtime implementation for God-View Sovereign."""

from __future__ import annotations

import copy
import logging
import os
from datetime import UTC, datetime
from typing import Any

import httpx

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

    def _env_mode(self) -> str:
        return os.environ.get("GODVIEW_ENV", "gcp").lower()

    def _get_access_token(self) -> str:
        if self.auth is not None and hasattr(self.auth, "access_token"):
            return self.auth.access_token()
        try:
            import google.auth
            from google.auth.transport.requests import Request as GoogleAuthRequest

            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            if not creds.valid:
                creds.refresh(GoogleAuthRequest())
            return str(creds.token)
        except Exception as exc:
            raise RuntimeError(f"Failed to obtain OAuth2 access token for Vertex AI: {exc}") from exc

    def _base_url(self) -> str:
        return f"https://{self.location}-aiplatform.googleapis.com/v1"

    def _parent_name(self) -> str:
        return f"projects/{self.project}/locations/{self.location}"

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
            f"{self._parent_name()}/reasoningEngines/{agent_id}"
            if self.project
            else f"reasoningEngines/{agent_id}"
        )
        card_url = f"{self._base_url()}/{resource_name}:query"

        card_dict = (
            card
            if isinstance(card, dict)
            else (card.model_dump(mode="json") if hasattr(card, "model_dump") else {})
        )

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
            "card": card_dict,
            "env_vars": copy.deepcopy(env_vars) if env_vars else {},
            "deployed_at": datetime.now(tz=UTC).isoformat(),
            "state": "ACTIVE",
        }

        mode = self._env_mode()
        if mode == "gcp" and self.project:
            token = self._get_access_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            url = f"{self._base_url()}/{self._parent_name()}/reasoningEngines"
            payload = {
                "displayName": f"godview-{agent_type}-{agent_id}",
                "description": f"God-View Court Agent {agent_id} ({agent_type})",
                "spec": {
                    "deploymentSpec": {
                        "minInstances": min_instances,
                        "maxInstances": max_instances,
                        "containerConcurrency": 4,
                    }
                },
                "labels": {
                    "godview_managed": "true",
                    "godview_role": "court",
                    "agent_type": agent_type,
                    "agent_id": agent_id,
                },
            }
            try:
                with httpx.Client(timeout=15.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code in (200, 201):
                        resp_data = resp.json()
                        if "name" in resp_data:
                            record["reasoning_engine"] = resp_data["name"]
                            record["a2a_card_url"] = f"{self._base_url()}/{resp_data['name']}:query"
                    elif resp.status_code != 409:  # 409 Conflict is acceptable if already deployed
                        raise RuntimeError(
                            f"Vertex deploy reasoningEngine failed with HTTP {resp.status_code}: {resp.text}"
                        )
            except Exception as exc:
                if mode == "gcp":
                    raise RuntimeError(f"Vertex ReasoningEngine deployment failed for {agent_id}: {exc}") from exc

        self._cached_agents[agent_id] = copy.deepcopy(record)
        return copy.deepcopy(record)

    def set_min_instances(self, agent_id: str, min_instances: int) -> dict[str, Any]:
        """Update deploymentSpec.minInstances on the Vertex ReasoningEngine."""
        mode = self._env_mode()
        if mode == "gcp" and self.project:
            token = self._get_access_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            resource_name = (
                self._cached_agents.get(agent_id, {}).get("reasoning_engine")
                or f"{self._parent_name()}/reasoningEngines/{agent_id}"
            )
            url = f"{self._base_url()}/{resource_name}?updateMask=spec.deploymentSpec.minInstances"
            patch_body = {
                "spec": {
                    "deploymentSpec": {
                        "minInstances": min_instances,
                    }
                }
            }
            try:
                with httpx.Client(timeout=10.0) as client:
                    resp = client.patch(url, headers=headers, json=patch_body)
                    if resp.status_code not in (200, 201, 204):
                        raise RuntimeError(
                            f"Vertex patch minInstances failed with HTTP {resp.status_code}: {resp.text}"
                        )
            except Exception as exc:
                if mode == "gcp":
                    raise RuntimeError(f"Failed to set min_instances on {agent_id}: {exc}") from exc

        if agent_id in self._cached_agents:
            self._cached_agents[agent_id]["deployment_spec"]["min_instances"] = min_instances
            return copy.deepcopy(self._cached_agents[agent_id])
        return {"agent_id": agent_id, "deployment_spec": {"min_instances": min_instances}}

    def retire_agent(self, agent_id: str) -> dict[str, Any]:
        """Retire a ReasoningEngine and cool its instances to 0."""
        self.set_min_instances(agent_id, 0)
        if agent_id in self._cached_agents:
            self._cached_agents[agent_id]["state"] = "RETIRED"
            return copy.deepcopy(self._cached_agents[agent_id])
        return {"agent_id": agent_id, "state": "RETIRED"}

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        """Get ReasoningEngine metadata."""
        if agent_id in self._cached_agents:
            return copy.deepcopy(self._cached_agents[agent_id])

        mode = self._env_mode()
        if mode == "gcp" and self.project:
            token = self._get_access_token()
            headers = {"Authorization": f"Bearer {token}"}
            resource_name = (
                agent_id
                if agent_id.startswith("projects/")
                else f"{self._parent_name()}/reasoningEngines/{agent_id}"
            )
            url = f"{self._base_url()}/{resource_name}"
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code == 404:
                    raise KeyError(f"Unknown ReasoningEngine agent_id '{agent_id}'")
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"Vertex get reasoningEngine failed with HTTP {resp.status_code}: {resp.text}"
                    )
                data = resp.json()
                rec = {
                    "agent_id": agent_id,
                    "reasoning_engine": data.get("name", resource_name),
                    "display_name": data.get("displayName", ""),
                    "state": data.get("state", "ACTIVE"),
                    "raw": data,
                }
                self._cached_agents[agent_id] = copy.deepcopy(rec)
                return rec

        raise KeyError(f"Unknown ReasoningEngine agent_id '{agent_id}'")

    def list_agents(self) -> list[dict[str, Any]]:
        """List deployed ReasoningEngines with pagination."""
        mode = self._env_mode()
        if mode == "gcp" and self.project:
            token = self._get_access_token()
            headers = {"Authorization": f"Bearer {token}"}
            url = f"{self._base_url()}/{self._parent_name()}/reasoningEngines"
            page_token = None
            remote_agents: list[dict[str, Any]] = []

            with httpx.Client(timeout=15.0) as client:
                while True:
                    params: dict[str, Any] = {"pageSize": 100}
                    if page_token:
                        params["pageToken"] = page_token

                    resp = client.get(url, headers=headers, params=params)
                    if resp.status_code != 200:
                        raise RuntimeError(
                            f"Vertex list reasoningEngines failed with HTTP {resp.status_code}: {resp.text}"
                        )
                    body = resp.json()
                    for item in body.get("reasoningEngines", []):
                        name = item.get("name", "")
                        labels = item.get("labels", {})
                        aid = labels.get("agent_id") or name.split("/")[-1]
                        rec = {
                            "agent_id": aid,
                            "reasoning_engine": name,
                            "display_name": item.get("displayName", ""),
                            "labels": labels,
                            "state": item.get("state", "ACTIVE"),
                            "a2a_card_url": f"{self._base_url()}/{name}:query",
                        }
                        self._cached_agents[aid] = copy.deepcopy(rec)
                        remote_agents.append(rec)

                    page_token = body.get("nextPageToken")
                    if not page_token:
                        break

            return remote_agents

        return [copy.deepcopy(a) for a in self._cached_agents.values()]

    async def send_a2a(self, message: Any) -> dict[str, Any]:
        """Dispatch an A2A message to target agent via HTTP POST with ADC OAuth access token or ID token."""
        parsed: A2AMessage = (
            message if isinstance(message, A2AMessage) else parse_a2a_message(message)
        )
        target_id = parsed.to_agent_id
        agent = self._cached_agents.get(target_id)
        endpoint = agent.get("a2a_card_url") if agent else None
        if not endpoint and self.project:
            endpoint = f"{self._base_url()}/{self._parent_name()}/reasoningEngines/{target_id}:query"

        mode = self._env_mode()
        if endpoint and self.auth is not None:
            # If endpoint is a Cloud Run service (.run.app), use OIDC ID token
            # If endpoint is aiplatform.googleapis.com, use OAuth2 access token
            if ".run.app" in endpoint:
                token = self.auth.id_token_for(endpoint)
            else:
                token = self._get_access_token()

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    endpoint,
                    json=parsed.model_dump(mode="json", by_alias=True),
                    headers=headers,
                )
                if resp.status_code == 200:
                    return resp.json()
                if mode == "gcp":
                    raise RuntimeError(
                        f"A2A dispatch to {target_id} at {endpoint} failed with HTTP {resp.status_code}: {resp.text}"
                    )

        if mode == "gcp":
            raise RuntimeError(f"Cannot dispatch A2A message to {target_id}: no endpoint available in GCP mode")

        return {
            "status": "delivered",
            "to_agent_id": target_id,
            "type": parsed.type,
            "round": parsed.round,
        }
