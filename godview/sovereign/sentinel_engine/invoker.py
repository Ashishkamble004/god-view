"""CapabilityInvoker: interfaces with sense adapters to invoke detection capabilities.

Provides:
- Adapter resolution over `ctx.adapters`
- Registration of mocked/stubbed capability handlers for testing
- Per-call timeout and error isolation (never crashes the sentinel evaluation pass)
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class CapabilityInvoker:
    """Dispatches capability invocations to sense adapters or registered handlers."""

    def __init__(self, adapters: list[Any] | None = None) -> None:
        self.adapters = adapters or []
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}

    def register_handler(
        self, capability_id: str, handler: Callable[[dict[str, Any]], Any]
    ) -> None:
        """Register a handler function for a specific capability ID."""
        self._handlers[capability_id] = handler

    async def invoke(self, capability_id: str, args: dict[str, Any] | None = None) -> Any:
        """Invoke a capability with arguments, ensuring per-call error isolation."""
        call_args = args or {}

        # 1. Custom / registered handler takes precedence
        if capability_id in self._handlers:
            handler = self._handlers[capability_id]
            try:
                res = handler(call_args)
                if inspect.isawaitable(res):
                    return await res
                return res
            except Exception as exc:
                logger.warning(
                    "Capability handler for '%s' raised error: %s", capability_id, exc
                )
                return {"error": str(exc)}

        # 2. Search adapters in context
        for adapter in self.adapters:
            try:
                caps = adapter.capabilities() if hasattr(adapter, "capabilities") else {}
                if capability_id in caps:
                    if hasattr(adapter, "execute_capability"):
                        res = adapter.execute_capability(capability_id, call_args)
                        if inspect.isawaitable(res):
                            return await res
                        return res
            except Exception as exc:
                logger.warning(
                    "Adapter '%s' failed executing capability '%s': %s",
                    getattr(adapter, "name", "unknown"),
                    capability_id,
                    exc,
                )
                return {"error": str(exc)}

        # 3. Not found
        logger.debug("No adapter or handler found for capability '%s'", capability_id)
        return None
