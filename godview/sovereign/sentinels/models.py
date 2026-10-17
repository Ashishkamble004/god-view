"""Data models for sentinel evaluation and telemetry results."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SentinelEvaluation(BaseModel):
    """Result of evaluating a single sentinel check."""

    observed: float = 0.0
    breached: bool = False
    evidence_samples: list[dict[str, Any]] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    signature: str | None = None
    resource_id: str | None = None
    severity_override: str | None = None
    error: str | None = None
