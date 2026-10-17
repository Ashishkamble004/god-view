# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Google ADK Multi-Agent package for God-View Court Agents."""

from __future__ import annotations

from godview.agents.adk.compat import (
    BaseAgent,
    Event,
    EventActions,
    InvocationContext,
    LlmAgent,
    LoopAgent,
    ParallelAgent,
    RemoteA2aAgent,
)
from godview.agents.adk.court import (
    CourtOrchestrator,
    register_in_process_court,
)
from godview.agents.adk.specialist import (
    ARCHETYPE_GENERIC_INSTRUCTIONS,
    SpecialistAgent,
    SpecialistTools,
    create_specialist_agent,
)

__all__ = [
    "ARCHETYPE_GENERIC_INSTRUCTIONS",
    "BaseAgent",
    "CourtOrchestrator",
    "Event",
    "EventActions",
    "InvocationContext",
    "LlmAgent",
    "LoopAgent",
    "ParallelAgent",
    "RemoteA2aAgent",
    "SpecialistAgent",
    "SpecialistTools",
    "create_specialist_agent",
    "register_in_process_court",
]
