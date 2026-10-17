# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Google ADK agent abstractions and compatibility layer for God-View Court Agents."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncGenerator, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("godview.agents.adk")


@dataclass
class EventActions:
    """Action flags carried on an ADK event."""

    escalate: bool = False
    transfer_to_agent: str | None = None
    finish_task: bool = False


@dataclass
class Event:
    """ADK event emitted during agent runs."""

    author: str
    content: str | dict[str, Any] = ""
    actions: EventActions = field(default_factory=EventActions)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())


@dataclass
class InvocationContext:
    """Session and invocation context for ADK agent runs."""

    session_id: str = "default_session"
    branch: str = "main"
    state: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    llm_runner: Any = None

    def model_copy(self) -> InvocationContext:
        return InvocationContext(
            session_id=self.session_id,
            branch=self.branch,
            state=dict(self.state),
            metadata=dict(self.metadata),
            llm_runner=self.llm_runner,
        )


class BaseAgent:
    """Base agent interface matching Google ADK BaseAgent."""

    def __init__(
        self,
        name: str,
        *,
        description: str = "",
        sub_agents: Sequence[BaseAgent] | None = None,
        **kwargs: Any,
    ) -> None:
        self.name = name
        self.description = description
        self.sub_agents: list[BaseAgent] = list(sub_agents or [])
        self.extra_kwargs = kwargs

    async def run_async(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event]:
        """Execute agent asynchronously and yield events."""
        async for event in self._run_async_impl(ctx):
            yield event

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event]:
        yield Event(author=self.name, content=f"Agent {self.name} completed.")

    async def invoke(
        self, input_text: str | dict[str, Any], ctx: InvocationContext | None = None
    ) -> Event:
        """Run agent to completion and return the final event."""
        context = ctx or InvocationContext()
        context.metadata["input"] = input_text
        last_event = Event(author=self.name, content="")
        async for ev in self.run_async(context):
            last_event = ev
        return last_event


class LlmAgent(BaseAgent):
    """LLM-backed Agent matching Google ADK LlmAgent."""

    def __init__(
        self,
        name: str,
        *,
        model: str = "gemini-4-argon",
        instruction: str = "",
        tools: Sequence[Callable[..., Any]] | None = None,
        sub_agents: Sequence[BaseAgent] | None = None,
        fallback_model: str = "gemini-3.8-flash",
        location: str = "global",
        description: str = "",
        llm_runner: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, description=description, sub_agents=sub_agents, **kwargs)
        self.model = model
        self.fallback_model = fallback_model
        self.location = location
        self.instruction = instruction
        self.tools: list[Callable[..., Any]] = list(tools or [])
        self.llm_runner = llm_runner
        self._tool_map: dict[str, Callable[..., Any]] = {}
        for tool in self.tools:
            tool_name = getattr(tool, "__name__", str(tool))
            self._tool_map[tool_name] = tool

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event]:
        runner = ctx.llm_runner or self.llm_runner
        input_data = ctx.metadata.get("input", "")

        # Execute using runner if provided
        if runner is not None:
            if inspect.iscoroutinefunction(runner):
                res = await runner(self, input_data, ctx)
            elif callable(runner):
                res = runner(self, input_data, ctx)
                if inspect.isawaitable(res):
                    res = await res
            else:
                res = str(runner)
            yield Event(author=self.name, content=res)
            return

        # Default execution: summarize task with tool availability
        content = {
            "agent": self.name,
            "model": self.model,
            "tools_count": len(self.tools),
            "input": input_data,
            "status": "ready",
        }
        yield Event(author=self.name, content=content)


class ParallelAgent(BaseAgent):
    """Parallel agent executing sub-agents concurrently, matching Google ADK ParallelAgent."""

    def __init__(
        self,
        name: str,
        *,
        sub_agents: Sequence[BaseAgent] | None = None,
        description: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, description=description, sub_agents=sub_agents, **kwargs)

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event]:
        if not self.sub_agents:
            return

        queue: asyncio.Queue[Event] = asyncio.Queue()

        async def _run_child(child: BaseAgent) -> None:
            child_ctx = ctx.model_copy()
            child_ctx.branch = f"{self.name}.{child.name}"
            try:
                async for event in child.run_async(child_ctx):
                    await queue.put(event)
            except Exception as exc:
                logger.exception("Error in ParallelAgent branch %s", child.name)
                await queue.put(
                    Event(
                        author=child.name,
                        content={"error": str(exc)},
                        actions=EventActions(escalate=True),
                    )
                )

        tasks = [asyncio.create_task(_run_child(child)) for child in self.sub_agents]

        async def _wait_tasks() -> None:
            await asyncio.gather(*tasks, return_exceptions=True)

        waiter = asyncio.create_task(_wait_tasks())
        while not waiter.done() or not queue.empty():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.1)
                yield event
            except TimeoutError:
                continue


class LoopAgent(BaseAgent):
    """Loop agent running critic/refinement rounds, matching Google ADK LoopAgent."""

    def __init__(
        self,
        name: str,
        *,
        sub_agents: Sequence[BaseAgent] | None = None,
        max_iterations: int = 4,
        description: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, description=description, sub_agents=sub_agents, **kwargs)
        self.max_iterations = max_iterations

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event]:
        if not self.sub_agents:
            return

        iteration = 0
        while iteration < self.max_iterations:
            iteration += 1
            for child in self.sub_agents:
                async for event in child.run_async(ctx):
                    yield event
                    if event.actions.escalate or event.actions.finish_task:
                        return


class RemoteA2aAgent(BaseAgent):
    """Remote A2A Agent communicating via A2A protocol, matching Google ADK RemoteA2aAgent."""

    def __init__(
        self,
        name: str,
        agent_card: str | dict[str, Any],
        *,
        description: str = "",
        timeout: float = 30.0,
        client: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, description=description, **kwargs)
        self.agent_card = agent_card
        self.timeout = timeout
        self.client = client
        self.card_url = (
            agent_card
            if isinstance(agent_card, str)
            else agent_card.get("url", "")
        )

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event]:
        payload = {
            "to_agent": self.name,
            "input": ctx.metadata.get("input"),
            "session_id": ctx.session_id,
        }
        if self.client is not None:
            if hasattr(self.client, "send_a2a"):
                res = await self.client.send_a2a(
                    {
                        "to_agent_id": self.name,
                        "type": "INTERROGATION",
                        "payload": payload,
                    }
                )
                yield Event(author=self.name, content=res)
                return
            if hasattr(self.client, "execute_task"):
                from godview.agents.server import A2ATaskRequest

                inp = ctx.metadata.get("input") or {}
                req = A2ATaskRequest(
                    task_id=ctx.session_id,
                    agent_id=self.name,
                    incident_id=inp.get("incident_id"),
                    telemetry=inp.get("evidence", inp),
                )
                resp = self.client.execute_task(req)
                yield Event(author=self.name, content=resp.model_dump(mode="json"))
                return

        yield Event(
            author=self.name,
            content={"status": "delivered", "to": self.name, "url": self.card_url},
        )
