#!/usr/bin/env python3
# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Deploy or update God-View Court Agents on Vertex AI Agent Engine.

Spec alignment:
- plans/2026-10-17-god-view-rebuild-plan.md §3.5:
  - real agent_engines.create with extra_packages=[godview, shared]
  - requirements including google-adk
  - deployment_spec min_instances=0, max_instances=2, resource_limits={cpu: "1", memory: "2Gi"}
  - labels: godview_managed=true, godview_role=court, agent_type, subject
  - env: GODVIEW_SOVEREIGN_URL, GOOGLE_CLOUD_LOCATION=global
  - Roster dynamically fetched from Sovereign GET /api/v1/perceive spawn plan
- Supports hermetic testing against InMemoryAgentEngine.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "shared") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "shared"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger("godview.deploy.agent_engine")

DEFAULT_PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", os.environ.get("GODVIEW_PROJECT_ID", ""))
DEFAULT_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
DEFAULT_STAGING_BUCKET = os.environ.get("GODVIEW_AGENT_STAGING_BUCKET", "")
DEFAULT_SENSE_SA = os.environ.get("GODVIEW_SENSE_SA", "")
COURT_MAX_AGENTS = int(os.environ.get("GODVIEW_COURT_MAX_AGENTS", "40"))

VALID_AGENT_TYPES = ("application", "data", "business_line", "change", "platform")


@dataclass(frozen=True)
class DeploymentSpec:
    """Vertex AI Agent Engine deployment_spec per §3.5."""

    min_instances: int = 0
    max_instances: int = 2
    resource_limits: dict[str, str] = field(
        default_factory=lambda: {"cpu": "1", "memory": "2Gi"}
    )
    container_concurrency: int = 4
    env_vars: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentEnginePayload:
    """Complete deployment payload for a court agent on Vertex AI Agent Engine."""

    agent_id: str
    agent_type: str
    subject_key: str
    display_name: str
    description: str
    project_id: str
    location: str
    staging_bucket: str
    service_account: str
    deployment_spec: DeploymentSpec
    a2a_card: dict[str, Any]
    labels: dict[str, str]


class AgentEngineBackend(Protocol):
    """Protocol implemented by both the live Vertex AI SDK wrapper and the hermetic fake."""

    def list_reasoning_engines(self) -> list[dict[str, Any]]: ...

    def deploy_reasoning_engine(self, payload: AgentEnginePayload) -> dict[str, Any]: ...

    def set_min_instances(self, resource_name: str, min_instances: int) -> dict[str, Any]: ...


DEFAULT_SKILLS_BY_TYPE: dict[str, list[dict[str, Any]]] = {
    "application": [
        {"id": "explain_business_change", "name": "Explain a commit or PR in business terms", "tags": ["change"]},
        {"id": "answer_interrogation", "name": "Answer a swarm question about rules, routes, entities or boundaries", "tags": ["swarm"]},
        {"id": "author_invariant", "name": "Propose INVARIANT.md candidates from code, policy or observation", "tags": ["invariants"]},
        {"id": "run_probe_plan", "name": "Execute a probe plan under the synthetic marker and report boundaries", "tags": ["probe"]},
        {"id": "propose_remediation", "name": "Produce a trust-gated remediation plan with a reversal recipe", "tags": ["remediation"]},
    ],
    "data": [
        {"id": "check_data_integrity", "name": "Run read-only SQL integrity and orphan checks", "tags": ["integrity"]},
        {"id": "analyze_query_latency", "name": "Inspect query execution and blocking chains", "tags": ["locks"]},
        {"id": "propose_sql_remediation", "name": "Propose L3 backend termination or schema remediation", "tags": ["remediation"]},
    ],
    "business_line": [
        {"id": "mine_journey_conformance", "name": "Evaluate directly-follows conformance and funnel KPIs", "tags": ["journeys"]},
        {"id": "quantify_business_exposure", "name": "Calculate business impact across affected journeys", "tags": ["impact"]},
        {"id": "propose_step_replay", "name": "Propose L4 replay or compensation via discovered admin contracts", "tags": ["remediation"]},
    ],
    "change": [
        {"id": "review_pull_request_risk", "name": "Summarize PR diff in business terms and score risk", "tags": ["change"]},
        {"id": "correlate_deploy_incident", "name": "Correlate recent commits/deploys against sentinel breaches", "tags": ["swarm"]},
        {"id": "author_rollback_pr", "name": "Execute 5-step rollback PR recipe via capability adapter", "tags": ["remediation"]},
    ],
    "platform": [
        {"id": "evaluate_golden_signals", "name": "Check latency, error, saturation and drift diffs", "tags": ["platform"]},
        {"id": "audit_resource_cost", "name": "Identify idle Reasoning Engines and oversized resources", "tags": ["cost"]},
        {"id": "restore_drifted_attribute", "name": "Restore drifted infrastructure attribute or dispatch reconcile", "tags": ["remediation"]},
    ],
}


def build_a2a_card(
    agent_type: str,
    subject_key: str,
    endpoint_url: str | None = None,
    project_id: str | None = None,
    location: str | None = None,
) -> dict[str, Any]:
    """Construct the A2A AgentCard JSON."""
    if agent_type not in VALID_AGENT_TYPES:
        raise ValueError(f"Unsupported agent_type '{agent_type}'. Must be one of {VALID_AGENT_TYPES}")
    agent_id = f"{agent_type}__{subject_key}"
    proj = project_id or DEFAULT_PROJECT_ID or "god-view"
    loc = location or DEFAULT_LOCATION or "us-central1"
    url = endpoint_url or f"https://{loc}-aiplatform.googleapis.com/v1beta1/projects/{proj}/locations/{loc}/reasoningEngines/godview-{agent_type.replace('_', '-')}-{subject_key}:query"
    return {
        "name": agent_id,
        "version": "5.0.0",
        "description": f"God-View {agent_type.replace('_', ' ').title()} Agent for {subject_key}",
        "url": url,
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
        },
        "skills": DEFAULT_SKILLS_BY_TYPE[agent_type],
    }


def build_agent_payload(
    agent_type: str,
    subject_key: str,
    sovereign_url: str,
    project_id: str | None = None,
    location: str | None = None,
    staging_bucket: str | None = None,
    service_account: str | None = None,
) -> AgentEnginePayload:
    """Build and validate a complete AgentEnginePayload."""
    if agent_type not in VALID_AGENT_TYPES:
        raise ValueError(f"Invalid agent_type '{agent_type}'. Expected one of {VALID_AGENT_TYPES}")
    if not subject_key or not subject_key.strip():
        raise ValueError("subject_key must be non-empty")
    if not sovereign_url or not sovereign_url.startswith("http"):
        raise ValueError(f"sovereign_url must be a valid HTTP(S) URL, got '{sovereign_url}'")

    proj = project_id or DEFAULT_PROJECT_ID or "hermetic"
    loc = location or DEFAULT_LOCATION or "us-central1"
    bucket = staging_bucket or DEFAULT_STAGING_BUCKET or f"gs://{proj}-god-view-agent-staging"
    sa = service_account or DEFAULT_SENSE_SA
    if not sa and proj != "hermetic":
        sa = f"godview-sense-sa@{proj}.iam.gserviceaccount.com"

    if not bucket.startswith("gs://"):
        raise ValueError(f"staging_bucket must start with gs://, got '{bucket}'")

    from godview_shared.config import MODEL_TABLE

    reasoning_cfg = MODEL_TABLE.get("reasoning", MODEL_TABLE["reasoning"])
    primary_model = reasoning_cfg.model_id
    fallback_model = (
        reasoning_cfg.fallbacks[0].model_id if reasoning_cfg.fallbacks else primary_model
    )
    embedding_cfg = MODEL_TABLE.get("embedding", MODEL_TABLE.get("embeddings"))
    embedding_model = embedding_cfg.model_id if embedding_cfg else "text-embedding-005"

    agent_id = f"{agent_type}__{subject_key}"
    env_vars = {
        "GODVIEW_AGENT_ID": agent_id,
        "GODVIEW_AGENT_TYPE": agent_type,
        "GODVIEW_SUBJECT_KEY": subject_key,
        "GOOGLE_CLOUD_LOCATION": "global",
        "GODVIEW_MODEL_LOCATION": "global",
        "GODVIEW_REASONING_MODEL": primary_model,
        "GODVIEW_FALLBACK_MODEL": fallback_model,
        "GODVIEW_ESCALATION_MODEL": primary_model,
        "GODVIEW_EMBEDDING_MODEL": embedding_model,
        "GODVIEW_EMBEDDING_LOCATION": loc,
        "GODVIEW_SOVEREIGN_URL": sovereign_url.rstrip("/"),
        "GODVIEW_CAPABILITY_INVOKE_PATH": "/v1/capabilities/{capability_id}:invoke",
    }
    spec = DeploymentSpec(
        min_instances=0,
        max_instances=2,
        resource_limits={"cpu": "1", "memory": "2Gi"},
        container_concurrency=4,
        env_vars=env_vars,
    )
    labels = {
        "godview_managed": "true",
        "godview_role": "court",
        "agent_type": agent_type.replace("_", "-"),
        "subject": subject_key[:63],
    }
    return AgentEnginePayload(
        agent_id=agent_id,
        agent_type=agent_type,
        subject_key=subject_key,
        display_name=f"godview-{agent_type.replace('_', '-')}-{subject_key}"[:63],
        description=f"God-View v5 {agent_type} specialist agent for {subject_key}",
        project_id=proj,
        location=loc,
        staging_bucket=bucket,
        service_account=sa,
        deployment_spec=spec,
        a2a_card=build_a2a_card(agent_type, subject_key, project_id=proj, location=loc),
        labels=labels,
    )


class HermeticAgentEngineBackend:
    """Adapter wrapping shared.godview_shared.fakes.InMemoryAgentEngine for tests."""

    def __init__(self, shared_fake: Any | None = None) -> None:
        if shared_fake is None:
            from godview_shared.fakes import InMemoryAgentEngine

            shared_fake = InMemoryAgentEngine()
        self.fake_engine = shared_fake
        self._labels_by_agent: dict[str, dict[str, str]] = {}

    def list_reasoning_engines(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for item in self.fake_engine.list_agents():
            agent_id = item["agent_id"]
            enriched = dict(item)
            enriched["name"] = item.get("reasoning_engine", agent_id)
            enriched["labels"] = dict(
                self._labels_by_agent.get(agent_id, item.get("labels", {"godview_managed": "true"}))
            )
            results.append(enriched)
        return results

    def deploy_reasoning_engine(self, payload: AgentEnginePayload) -> dict[str, Any]:
        from godview_shared.contracts import A2AAgentCard

        validated_card = A2AAgentCard.model_validate(payload.a2a_card)
        record = self.fake_engine.deploy_agent(
            agent_id=payload.agent_id,
            agent_type=payload.agent_type,
            min_instances=payload.deployment_spec.min_instances,
            max_instances=payload.deployment_spec.max_instances,
            cpu=payload.deployment_spec.resource_limits["cpu"],
            memory=payload.deployment_spec.resource_limits["memory"],
            card=validated_card,
            subject=payload.subject_key,
            env_vars=dict(payload.deployment_spec.env_vars),
        )
        self._labels_by_agent[payload.agent_id] = dict(payload.labels)
        a2a_url = f"https://{payload.location}-aiplatform.googleapis.com/v1beta1/projects/{payload.project_id}/locations/{payload.location}/reasoningEngines/{payload.agent_id}:query"
        record["name"] = record.get("reasoning_engine", payload.agent_id)
        record["display_name"] = payload.display_name
        record["subject_key"] = payload.subject_key
        record["labels"] = dict(payload.labels)
        record["a2a_card_url"] = a2a_url
        card = dict(payload.a2a_card)
        card["url"] = a2a_url
        record["a2a_card"] = card
        if "deployment_spec" in record:
            record["deployment_spec"]["env_vars"] = dict(payload.deployment_spec.env_vars)
        return record

    def set_min_instances(self, resource_name_or_agent_id: str, min_instances: int) -> dict[str, Any]:
        agent_id = resource_name_or_agent_id.split("/")[-1]
        labels = self._labels_by_agent.get(agent_id, {})
        if labels.get("godview_managed") != "true":
            if agent_id not in self.fake_engine._agents:
                raise KeyError(f"ReasoningEngine '{resource_name_or_agent_id}' not found")
            raise PermissionError(
                f"Refusing to mutate ReasoningEngine '{resource_name_or_agent_id}' without label godview_managed=true"
            )
        if min_instances not in (0, 1):
            raise ValueError(f"Earned warmth min_instances must be 0 or 1, got {min_instances}")
        updated = self.fake_engine.set_min_instances(agent_id, min_instances)
        updated["name"] = updated.get("reasoning_engine", agent_id)
        updated["labels"] = dict(labels)
        return updated


class VertexAgentEngineBackend:
    """Live Vertex AI Agent Engine backend using google-cloud-aiplatform[agent_engines,adk]."""

    def __init__(self, project_id: str, location: str, staging_bucket: str) -> None:
        self.project_id = project_id
        self.location = location
        self.staging_bucket = staging_bucket

    def _init_sdk(self) -> tuple[Any, Any]:
        import vertexai  # type: ignore[import-not-found]
        from google.cloud import aiplatform_v1beta1  # type: ignore[import-not-found]

        vertexai.init(
            project=self.project_id,
            location=self.location,
            staging_bucket=self.staging_bucket,
        )
        client_options = {"api_endpoint": f"{self.location}-aiplatform.googleapis.com"}
        client = aiplatform_v1beta1.ReasoningEngineServiceClient(client_options=client_options)
        return vertexai, client

    def list_reasoning_engines(self) -> list[dict[str, Any]]:
        _, client = self._init_sdk()
        parent = f"projects/{self.project_id}/locations/{self.location}"
        results: list[dict[str, Any]] = []
        for engine in client.list_reasoning_engines(parent=parent):
            labels = dict(getattr(engine, "labels", {}) or {})
            results.append(
                {
                    "name": engine.name,
                    "display_name": engine.display_name,
                    "labels": labels,
                }
            )
        return results

    def deploy_reasoning_engine(self, payload: AgentEnginePayload) -> dict[str, Any]:
        vertexai, _ = self._init_sdk()
        from vertexai import agent_engines  # type: ignore[import-not-found]

        sovereign_url = payload.deployment_spec.env_vars["GODVIEW_SOVEREIGN_URL"]
        agent_id = payload.agent_id
        agent_type = payload.agent_type
        subject_key = payload.subject_key

        class CourtAgentWrapper:
            """Cloudpickle-safe Vertex AI Agent Engine wrapper for God-View Court Specialist."""

            def __init__(
                self,
                sov_url: str,
                aid: str,
                atype: str,
                skey: str,
                card_data: dict[str, Any] | None = None,
            ) -> None:
                self.sovereign_url = sov_url
                self.agent_id = aid
                self.agent_type = atype
                self.subject_key = skey
                self.card_data = card_data or {}
                self._agent: Any = None
                self._llm: Any = None

            def set_up(self) -> None:
                auth = None
                try:
                    from godview_shared.auth import TokenProvider
                    from godview_shared.config import GodViewSettings
                    from godview_shared.llm import LlmClient

                    settings = GodViewSettings(
                        env_mode="gcp",
                        sovereign_url=self.sovereign_url,
                    )
                    auth = TokenProvider(settings=settings)
                    self._llm = LlmClient(settings=settings)
                except Exception as exc:
                    logger.warning("Could not initialize LlmClient in set_up: %s", exc)
                    self._llm = None

                try:
                    from types import SimpleNamespace

                    from godview.agents.adk.specialist import create_specialist_agent

                    ctx = SimpleNamespace(
                        llm=self._llm,
                        sovereign_url=self.sovereign_url,
                        auth=auth,
                        docs=None,
                        ledger=None,
                    )
                    self._agent = create_specialist_agent(
                        archetype=self.agent_type,
                        subject_ref=self.subject_key,
                        ctx=ctx,
                        sovereign_url=self.sovereign_url,
                    )
                except Exception as exc:
                    logger.warning("Could not initialize specialist agent in set_up: %s", exc)
                    self._agent = None

            def agent_card(self) -> dict[str, Any]:
                """Return the A2A AgentCard for this deployed specialist."""
                if self.card_data:
                    return dict(self.card_data)
                return build_a2a_card(self.agent_type, self.subject_key)

            def query(self, **kwargs: Any) -> dict[str, Any]:
                input_data = kwargs.get("input")
                merged = dict(kwargs)
                if isinstance(input_data, dict):
                    for k, v in input_data.items():
                        merged.setdefault(k, v)

                if (
                    merged.get("action") in ("card", "get_card")
                    or merged.get("get_card")
                ):
                    return self.agent_card()

                incident_id = str(merged.get("incident_id") or "adhoc")
                evidence = merged.get("evidence_pack") or merged.get("evidence") or {}
                challenges = merged.get("challenges")
                if self._agent is not None and hasattr(self._agent, "diagnose"):
                    import asyncio

                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        loop = None

                    if loop is not None and loop.is_running():
                        import concurrent.futures

                        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                            diag = pool.submit(
                                lambda: asyncio.run(
                                    self._agent.diagnose(incident_id, evidence, challenges=challenges)
                                )
                            ).result(timeout=60)
                    else:
                        diag = asyncio.run(
                            self._agent.diagnose(incident_id, evidence, challenges=challenges)
                        )

                    return {
                        "agent_id": self.agent_id,
                        "agent_type": self.agent_type,
                        "subject_key": self.subject_key,
                        "status": "ok",
                        "diagnosis": diag.model_dump(mode="json") if hasattr(diag, "model_dump") else diag,
                    }
                return {
                    "agent_id": self.agent_id,
                    "agent_type": self.agent_type,
                    "subject_key": self.subject_key,
                    "sovereign_url": self.sovereign_url,
                    "status": "error",
                    "error": "Agent not initialized",
                }

        app_instance = CourtAgentWrapper(
            sov_url=sovereign_url,
            aid=agent_id,
            atype=agent_type,
            skey=subject_key,
            card_data=payload.a2a_card,
        )

        extra_packages = [
            str(REPO_ROOT / "godview"),
            str(REPO_ROOT / "shared"),
        ]

        remote_agent = agent_engines.create(
            app_instance,
            display_name=payload.display_name,
            description=payload.description,
            extra_packages=extra_packages,
            requirements=[
                "google-cloud-aiplatform[agent_engines,adk]>=1.88.0",
                "google-adk>=0.1.0",
                "pydantic>=2.7.0",
                "httpx>=0.27.0",
            ],
            env_vars=payload.deployment_spec.env_vars,
            min_instances=payload.deployment_spec.min_instances,
            max_instances=payload.deployment_spec.max_instances,
            resource_limits=payload.deployment_spec.resource_limits,
            container_concurrency=payload.deployment_spec.container_concurrency,
            service_account=payload.service_account if payload.service_account else None,
        )
        resource_name = remote_agent.resource_name

        from google.cloud import aiplatform_v1beta1
        from google.protobuf import field_mask_pb2

        _, client = self._init_sdk()
        updated_engine = aiplatform_v1beta1.ReasoningEngine(
            name=resource_name,
            labels=payload.labels,
        )
        update_mask = field_mask_pb2.FieldMask(paths=["labels"])
        client.update_reasoning_engine(
            reasoning_engine=updated_engine,
            update_mask=update_mask,
        ).result()

        a2a_url = f"https://{payload.location}-aiplatform.googleapis.com/v1beta1/{resource_name}:query"
        card = dict(payload.a2a_card)
        card["url"] = a2a_url
        return {
            "name": resource_name,
            "display_name": payload.display_name,
            "agent_id": payload.agent_id,
            "agent_type": payload.agent_type,
            "subject_key": payload.subject_key,
            "deployment_spec": asdict(payload.deployment_spec),
            "labels": dict(payload.labels),
            "a2a_card_url": a2a_url,
            "a2a_card": card,
            "state": "ACTIVE",
        }

    def set_min_instances(self, resource_name: str, min_instances: int) -> dict[str, Any]:
        if min_instances not in (0, 1):
            raise ValueError(f"Earned warmth min_instances must be 0 or 1, got {min_instances}")
        _, client = self._init_sdk()
        from google.cloud import aiplatform_v1beta1  # type: ignore[import-not-found]
        from google.protobuf import field_mask_pb2  # type: ignore[import-not-found]

        existing = client.get_reasoning_engine(name=resource_name)
        labels = dict(getattr(existing, "labels", {}) or {})
        if labels.get("godview_managed") != "true":
            raise PermissionError(
                f"Refusing to mutate ReasoningEngine '{resource_name}' without label godview_managed=true"
            )

        updated_engine = aiplatform_v1beta1.ReasoningEngine(
            name=resource_name,
            deployment_spec=aiplatform_v1beta1.ReasoningEngineSpec.DeploymentSpec(
                min_instances=min_instances,
            ),
        )
        update_mask = field_mask_pb2.FieldMask(paths=["deployment_spec.min_instances"])
        operation = client.update_reasoning_engine(
            reasoning_engine=updated_engine,
            update_mask=update_mask,
        )
        result = operation.result()
        return {
            "name": result.name,
            "min_instances": min_instances,
            "labels": labels,
        }


def deploy_court_agent(
    backend: AgentEngineBackend,
    payload: AgentEnginePayload,
    max_court_agents: int = COURT_MAX_AGENTS,
) -> dict[str, Any]:
    """Validate court quota cap and deploy a single court agent via the chosen backend."""
    existing = backend.list_reasoning_engines()
    managed_engines = [
        e for e in existing if e.get("labels", {}).get("godview_managed") == "true"
    ]
    already_exists = any(e.get("agent_id") == payload.agent_id for e in managed_engines)
    if not already_exists and len(managed_engines) >= max_court_agents:
        raise RuntimeError(
            f"Court Manager cap reached ({len(managed_engines)}/{max_court_agents} godview_managed agents). "
            "Refusing to spawn new ReasoningEngine."
        )
    if payload.labels.get("godview_managed") != "true":
        raise PermissionError("Agent payload must carry label godview_managed='true'")

    return backend.deploy_reasoning_engine(payload)


def fetch_spawn_plan(sovereign_url: str, token: str | None = None) -> list[tuple[str, str]]:
    """Fetch dynamic spawn plan from Sovereign GET /api/v1/perceive."""
    import httpx

    headers = {}
    auth_token = token
    if not auth_token:
        try:
            import google.auth.transport.requests
            import google.oauth2.id_token

            auth_req = google.auth.transport.requests.Request()
            auth_token = google.oauth2.id_token.fetch_id_token(auth_req, sovereign_url)
        except Exception as exc:
            logger.debug("Could not mint ID token for sovereign perceive at %s: %s", sovereign_url, exc)

    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    url = f"{sovereign_url.rstrip('/')}/api/v1/perceive"
    try:
        resp = httpx.get(url, headers=headers, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        spawn_plan = data.get("spawn_plan") or data.get("court_roster") or []
        roster: list[tuple[str, str]] = []
        for item in spawn_plan:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                roster.append((str(item[0]), str(item[1])))
            elif isinstance(item, dict):
                atype = item.get("agent_type") or item.get("archetype")
                skey = item.get("subject_ref") or item.get("subject_key") or item.get("subject")
                if atype and skey:
                    roster.append((str(atype), str(skey)))
        return roster
    except Exception as exc:
        logger.warning("Failed to fetch perceive spawn plan from %s: %s", url, exc)
        return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deploy God-View court agents to Vertex AI Agent Engine")
    parser.add_argument("--agent-type", choices=VALID_AGENT_TYPES, help="Single agent type to deploy")
    parser.add_argument("--subject-key", help="Subject key for single agent deployment")
    parser.add_argument("--from-perceive", action="store_true", help="Fetch court roster from Sovereign /api/v1/perceive")
    parser.add_argument("--targets", help="Comma-separated archetype:subject pairs, e.g. application:orders-svc,data:orders-db")
    parser.add_argument(
        "--sovereign-url",
        default=os.environ.get("GODVIEW_SOVEREIGN_URL", "https://god-view-sovereign-placeholder.a.run.app"),
        help="Sovereign Cloud Run URL for capability invocation and perceive",
    )
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--location", default=DEFAULT_LOCATION)
    parser.add_argument("--staging-bucket", default=DEFAULT_STAGING_BUCKET)
    parser.add_argument("--service-account", default=DEFAULT_SENSE_SA)
    parser.add_argument(
        "--hermetic",
        action="store_true",
        default=os.environ.get("GODVIEW_ENV") == "hermetic",
        help="Use hermetic in-memory/shared fake backend instead of live Vertex AI",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print payload JSON without deploying")
    args = parser.parse_args(argv)

    targets: list[tuple[str, str]] = []
    if args.from_perceive:
        targets = fetch_spawn_plan(args.sovereign_url)
        if not targets:
            print("Perceive spawn plan returned 0 targets.", file=sys.stderr)
            return 1
    elif args.targets:
        for pair in args.targets.split(","):
            pair = pair.strip()
            if ":" in pair:
                atype, skey = pair.split(":", 1)
                targets.append((atype.strip(), skey.strip()))
    elif args.agent_type and args.subject_key:
        targets.append((args.agent_type, args.subject_key))
    else:
        parser.error("Provide --from-perceive, --targets, or both --agent-type and --subject-key")

    payloads = [
        build_agent_payload(
            agent_type=atype,
            subject_key=skey,
            sovereign_url=args.sovereign_url,
            project_id=args.project_id or None,
            location=args.location or None,
            staging_bucket=args.staging_bucket or None,
            service_account=args.service_account or None,
        )
        for atype, skey in targets
    ]

    if args.dry_run:
        print(json.dumps([asdict(p) for p in payloads], indent=2))
        return 0

    backend: AgentEngineBackend
    if args.hermetic:
        backend = HermeticAgentEngineBackend()
    else:
        if not args.sovereign_url or "placeholder" in args.sovereign_url:
            parser.error("--sovereign-url is required for live Vertex AI deployment")
        if not args.project_id:
            parser.error("--project-id is required for live Vertex AI deployment")
        if not args.staging_bucket:
            parser.error("--staging-bucket is required for live Vertex AI deployment")
        if not args.service_account:
            parser.error("--service-account is required for live Vertex AI deployment (e.g. godview-sense-sa@<project>.iam.gserviceaccount.com)")
        backend = VertexAgentEngineBackend(
            project_id=args.project_id,
            location=args.location,
            staging_bucket=args.staging_bucket,
        )

    deployed = [deploy_court_agent(backend, p) for p in payloads]
    print(json.dumps(deployed, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
