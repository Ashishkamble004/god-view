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
"""God-View v4.0 Court Specialist Agents package (`godview.agents`)."""

from __future__ import annotations

from godview.agents.archetypes import (
    ARCHETYPES,
    COURT_DOMAINS,
    ArchetypeSpec,
    CourtDomain,
    get_archetype,
    list_archetypes,
    load_seed_harness_tree,
    resolve_domain,
)
from godview.agents.court_agent import (
    CONSENSUS_THRESHOLD,
    MAX_SWARM_DURATION_S,
    MAX_SWARM_ROUNDS,
    CourtAgent,
    CourtHypothesis,
    CourtSwarmSession,
    SwarmConsensusVerdict,
)
from godview.agents.inspection_routes import create_harness_inspection_router
from godview.agents.server import (
    A2ATaskRequest,
    A2ATaskResponse,
    AgentCapabilitiesCard,
    AgentCard,
    AgentExecutor,
    AgentSkillCard,
    create_a2a_router,
    create_agent_app,
)

__all__ = [
    "A2ATaskRequest",
    "A2ATaskResponse",
    "ARCHETYPES",
    "AgentCapabilitiesCard",
    "AgentCard",
    "AgentExecutor",
    "AgentSkillCard",
    "ArchetypeSpec",
    "CONSENSUS_THRESHOLD",
    "COURT_DOMAINS",
    "CourtAgent",
    "CourtDomain",
    "CourtHypothesis",
    "CourtSwarmSession",
    "MAX_SWARM_DURATION_S",
    "MAX_SWARM_ROUNDS",
    "SwarmConsensusVerdict",
    "create_a2a_router",
    "create_agent_app",
    "create_harness_inspection_router",
    "get_archetype",
    "list_archetypes",
    "load_seed_harness_tree",
    "resolve_domain",
]
