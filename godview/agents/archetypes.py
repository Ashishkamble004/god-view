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
"""Court Specialist Agent archetypes and seed HarnessTree loaders."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

from godview_shared.config import (
    DEFAULT_AGENT_DAILY_BUDGETS_USD,
    DEFAULT_AGENT_FALLBACK_MODEL_ID,
    DEFAULT_AGENT_MODEL_ID,
    DEFAULT_AGENT_MODEL_LOCATION,
)

from godview.harness.core.tree import HarnessTree
from godview.harness.substrate import GODVIEW_SURFACE

CourtDomain = Literal[
    "application",
    "data",
    "business_line",
    "change",
    "platform",
]

COURT_DOMAINS: tuple[CourtDomain, ...] = (
    "application",
    "data",
    "business_line",
    "change",
    "platform",
)

_BASE_TREES_ROOT = Path(__file__).resolve().parent / "base_trees"


@dataclass(frozen=True)
class ArchetypeSpec:
    """Specification for one of the 5 God-View Court Specialist Agent archetypes."""

    domain: CourtDomain
    display_name: str
    description: str
    default_model_id: str = DEFAULT_AGENT_MODEL_ID
    fallback_model_id: str = DEFAULT_AGENT_FALLBACK_MODEL_ID
    location: str = DEFAULT_AGENT_MODEL_LOCATION
    daily_budget_usd: Decimal = Decimal("0.75")
    primary_capabilities: tuple[str, ...] = ()
    base_tree_dir: Path = _BASE_TREES_ROOT / "application"


ARCHETYPES: dict[str, ArchetypeSpec] = {
    "application": ArchetypeSpec(
        domain="application",
        display_name="Application Court Specialist",
        description=(
            "Diagnoses service RPC/HTTP regressions, fixed-width/JSON payload offset shifts "
            "(cibil_score / cibil_stub_900), and model routing / prompt anomalies."
        ),
        daily_budget_usd=DEFAULT_AGENT_DAILY_BUDGETS_USD["application"],
        primary_capabilities=(
            "obs.query_logs_scoped",
            "obs.list_timeseries",
            "obs.get_trace",
            "git.list_commits",
        ),
        base_tree_dir=_BASE_TREES_ROOT / "application",
    ),
    "data": ArchetypeSpec(
        domain="data",
        display_name="Data Court Specialist",
        description=(
            "Diagnoses Cloud SQL and BigQuery data distribution anomalies, two-sided CUSUM drift, "
            "and relational ordering invariants (step_order_insurance_before_kyc)."
        ),
        daily_budget_usd=DEFAULT_AGENT_DAILY_BUDGETS_USD["data"],
        primary_capabilities=(
            "sql.list_tables",
            "sql.list_active_queries",
            "obs.query_logs_scoped",
            "obs.list_timeseries",
        ),
        base_tree_dir=_BASE_TREES_ROOT / "data",
    ),
    "business_line": ArchetypeSpec(
        domain="business_line",
        display_name="Business-Line Court Specialist",
        description=(
            "Monitors end-to-end vehicle_loan_origination saga conformance, Pub/Sub DLQ stalls "
            "(insurance_no_policy_write), and rupee-at-risk KPI impact."
        ),
        daily_budget_usd=DEFAULT_AGENT_DAILY_BUDGETS_USD["business_line"],
        primary_capabilities=(
            "obs.list_timeseries",
            "obs.query_logs_scoped",
            "obs.get_trace",
            "sql.list_tables",
        ),
        base_tree_dir=_BASE_TREES_ROOT / "business_line",
    ),
    "change": ArchetypeSpec(
        domain="change",
        display_name="Change Court Specialist",
        description=(
            "Correlates production incidents with recent Git commits, PR diffs, container "
            "image rollouts, and configuration drift."
        ),
        daily_budget_usd=DEFAULT_AGENT_DAILY_BUDGETS_USD["change"],
        primary_capabilities=(
            "git.list_commits",
            "git.get_commit",
            "git.get_file_contents",
            "obs.query_logs_scoped",
        ),
        base_tree_dir=_BASE_TREES_ROOT / "change",
    ),
    "platform": ArchetypeSpec(
        domain="platform",
        display_name="Platform Court Specialist",
        description=(
            "Diagnoses GKE Autopilot OOMKilled (exit 137) pod crashes, Cloud Run scaling "
            "limits, VPC/firewall posture, and Model Armor security templates."
        ),
        daily_budget_usd=DEFAULT_AGENT_DAILY_BUDGETS_USD["platform"],
        primary_capabilities=(
            "k8s.get_resource",
            "k8s.query_logs",
            "k8s.list_recommendations",
            "obs.list_timeseries",
        ),
        base_tree_dir=_BASE_TREES_ROOT / "platform",
    ),
}


def resolve_domain(domain_or_agent_id: str) -> CourtDomain:
    """Resolve an `agent_id` (e.g. `application__svc` or `court-data`) to a `CourtDomain`."""
    cleaned = domain_or_agent_id.strip().lower()
    if cleaned in ARCHETYPES:
        return cleaned  # type: ignore[return-value]
    if "__" in cleaned:
        prefix = cleaned.split("__", 1)[0]
        if prefix in ARCHETYPES:
            return prefix  # type: ignore[return-value]
    for dom in COURT_DOMAINS:
        if dom in cleaned:
            return dom
    return "application"


def get_archetype(domain_or_agent_id: str) -> ArchetypeSpec:
    """Return the `ArchetypeSpec` for `domain_or_agent_id`."""
    dom = resolve_domain(domain_or_agent_id)
    return ARCHETYPES[dom]


def list_archetypes() -> list[ArchetypeSpec]:
    """Return all 5 Court Specialist Agent archetypes."""
    return [ARCHETYPES[d] for d in COURT_DOMAINS]


def load_seed_harness_tree(domain_or_agent_id: str) -> HarnessTree:
    """Load the seed `HarnessTree` (`h0`) for `domain_or_agent_id`."""
    spec = get_archetype(domain_or_agent_id)
    return HarnessTree(spec.base_tree_dir, GODVIEW_SURFACE)
