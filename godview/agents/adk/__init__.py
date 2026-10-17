# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Google ADK Multi-Agent package for God-View Court Agents."""

from __future__ import annotations

import google.adk  # noqa: F401
from google.adk.agents import BaseAgent, LlmAgent, LoopAgent, ParallelAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.events.event import Event, EventActions
from google.adk.tools import FunctionTool

from godview.agents.adk.court import (
    CourtOrchestrator,
    install_court_and_rsi,
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
    "FunctionTool",
    "InvocationContext",
    "LlmAgent",
    "LoopAgent",
    "ParallelAgent",
    "RemoteA2aAgent",
    "SpecialistAgent",
    "SpecialistTools",
    "create_specialist_agent",
    "install_court_and_rsi",
    "register_in_process_court",
]
