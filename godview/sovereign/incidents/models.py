"""Incident models and data contracts (`godview.sovereign.incidents.models`)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from godview_shared.contracts import (
    Diagnosis,
    EpisodicIncidentDoc,
)
from pydantic import ConfigDict, Field

IncidentOutcome = Literal["open", "mitigated", "resolved", "false_positive"]


class IncidentDoc(EpisodicIncidentDoc):
    """Extended incident record tracking dedupe keys, consecutive breaches, and auto-close status."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    dedupe_key: str = ""
    target_resource: str | None = None
    resource_id: str | None = None
    last_seen_at: datetime | str | None = None
    consecutive_breaches: int = 1
    clear_ticks: int = 0
    diagnoses: list[Diagnosis] = Field(default_factory=list)
    raw_evidence: dict[str, Any] = Field(default_factory=dict)
    root_cause_category: str | None = None
    remediation_capability: str | None = None
    resolution_summary: str | None = None
