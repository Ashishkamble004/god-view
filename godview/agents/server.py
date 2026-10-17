# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""A2A protocol AgentCard, AgentExecutor, and FastAPI router for God-View Court Agents."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, FastAPI
from godview_shared.config import (
    DEFAULT_AGENT_FALLBACK_MODEL_ID,
    DEFAULT_AGENT_MODEL_ID,
    DEFAULT_AGENT_MODEL_LOCATION,
)
from pydantic import BaseModel, Field

from godview.agents.archetypes import (
    COURT_DOMAINS,
    get_archetype,
    resolve_domain,
)
from godview.agents.court_agent import (
    CourtAgent,
    CourtHypothesis,
    CourtSwarmSession,
)
from godview.harness.core import autotrigger


class AgentSkillCard(BaseModel):
    """A2A skill descriptor advertised in an AgentCard."""

    id: str
    name: str
    description: str
    autotrigger: str
    tags: list[str] = Field(default_factory=list)


class AgentCapabilitiesCard(BaseModel):
    """A2A capability flags advertised in an AgentCard."""

    streaming: bool = False
    pushNotifications: bool = False
    stateTransitionHistory: bool = True
    harness2RecursiveEvolution: bool = True


class AgentCard(BaseModel):
    """A2A AgentCard descriptor for a God-View Court Specialist Agent."""

    name: str
    agent_id: str
    domain: str
    description: str
    url: str
    version: str = "4.0.0"
    default_model_id: str = DEFAULT_AGENT_MODEL_ID
    fallback_model_id: str = DEFAULT_AGENT_FALLBACK_MODEL_ID
    location: str = DEFAULT_AGENT_MODEL_LOCATION
    capabilities: AgentCapabilitiesCard = Field(default_factory=AgentCapabilitiesCard)
    skills: list[AgentSkillCard] = Field(default_factory=list)
    allowed_mcp_capabilities: list[str] = Field(default_factory=list)


class A2ATaskRequest(BaseModel):
    """Incoming A2A task execution request."""

    task_id: str
    agent_id: str = "application__specialist"
    incident_id: str | None = None
    telemetry: dict[str, Any] = Field(default_factory=dict)


class A2ATaskResponse(BaseModel):
    """Completed A2A task execution response."""

    task_id: str
    agent_id: str
    domain: str
    status: str = "completed"
    model_used: str = DEFAULT_AGENT_MODEL_ID
    hypothesis: dict[str, Any]


class AgentExecutor:
    """Executes A2A tasks and builds A2A AgentCards for the 5 Court Specialist Archetypes."""

    def __init__(self, base_url: str = "https://god-view-sovereign.run.app") -> None:
        self.base_url = base_url.rstrip("/")
        self.agents: dict[str, CourtAgent] = {
            dom: CourtAgent(f"{dom}__specialist", domain=dom) for dom in COURT_DOMAINS
        }
        self.swarm = CourtSwarmSession(list(self.agents.values()))

    def get_agent(self, domain_or_agent_id: str) -> CourtAgent:
        dom = resolve_domain(domain_or_agent_id)
        return self.agents[dom]

    def build_agent_card(self, domain_or_agent_id: str) -> AgentCard:
        agent = self.get_agent(domain_or_agent_id)
        spec = get_archetype(agent.domain)
        skills: list[AgentSkillCard] = []
        for sp in agent.tree.skills():
            raw = sp.read_text(encoding="utf-8")
            trig = autotrigger(raw) or ""
            skills.append(
                AgentSkillCard(
                    id=sp.parent.name,
                    name=sp.parent.name,
                    description=f"Harness² c6 skill for {agent.domain} ({sp.parent.name})",
                    autotrigger=trig,
                    tags=[agent.domain, "harness2"],
                )
            )
        return AgentCard(
            name=spec.display_name,
            agent_id=agent.agent_id,
            domain=agent.domain,
            description=spec.description,
            url=f"{self.base_url}/a2a/agents/{agent.domain}",
            default_model_id=agent.default_model_id,
            fallback_model_id=agent.fallback_model_id,
            location=agent.location,
            skills=skills,
            allowed_mcp_capabilities=list(spec.primary_capabilities),
        )

    def execute_task(self, request: A2ATaskRequest) -> A2ATaskResponse:
        agent = self.get_agent(request.agent_id)
        inc_id = request.incident_id or request.task_id
        hyp: CourtHypothesis = agent.diagnose_incident(inc_id, telemetry=request.telemetry)
        return A2ATaskResponse(
            task_id=request.task_id,
            agent_id=agent.agent_id,
            domain=agent.domain,
            status="completed",
            model_used=hyp.model_used,
            hypothesis=asdict(hyp),
        )


def create_a2a_router(executor: AgentExecutor | None = None) -> APIRouter:
    """Create the FastAPI router exposing A2A AgentCard and task execution endpoints."""
    exec_instance = executor or AgentExecutor()
    router = APIRouter(tags=["a2a"])

    @router.get("/.well-known/agent.json", response_model=AgentCard)
    def get_default_agent_card() -> AgentCard:
        return exec_instance.build_agent_card("application")

    @router.get("/a2a/agents/{agent_id}/card", response_model=AgentCard)
    def get_specialist_agent_card(agent_id: str) -> AgentCard:
        return exec_instance.build_agent_card(agent_id)

    @router.post("/a2a/tasks/send", response_model=A2ATaskResponse)
    def send_a2a_task(req: A2ATaskRequest) -> A2ATaskResponse:
        return exec_instance.execute_task(req)

    @router.post("/a2a/swarm/convene")
    def convene_court_swarm(payload: dict[str, Any]) -> dict[str, Any]:
        inc_id = str(payload.get("incident_id", "inc-20261017-0001"))
        telemetry = payload.get("telemetry") or {}
        verdict = exec_instance.swarm.convene(inc_id, telemetry=telemetry)
        return CourtSwarmSession.serialize_verdict(verdict)

    return router


def create_agent_app(executor: AgentExecutor | None = None) -> FastAPI:
    """Create a standalone FastAPI application for the Court Agent A2A service."""
    app = FastAPI(title="God-View v4.0 Court Agents (Harness² A2A)", version="4.0.0")
    app.include_router(create_a2a_router(executor))
    return app
