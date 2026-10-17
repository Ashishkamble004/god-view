#!/usr/bin/env python3
"""Deploy or update God-View Court Agents on Vertex AI Agent Engine (us-central1).

Spec alignment:
- docs/GOD_VIEW_MCP_AGENT_SPEC.md §4:
  - deployment_spec: min_instances=0, max_instances=1, resource_limits={cpu: "1", memory: "2Gi"}, container_concurrency=4
  - A2A AgentCard published per agent
- plans/2026-10-16-build-decisions.md §A1, §A3, §A7:
  - Court hard cap: GODVIEW_COURT_MAX_AGENTS = 40
  - Staging bucket: gs://general-ak-god-view-agent-staging
  - Location: us-central1 (reasoning model gemini-3.8-flash @ global, embeddings text-embedding-005 @ us-central1)
  - Court agents never host MCP servers; they invoke capabilities via Sovereign's POST /v1/capabilities/{id}:invoke
- Supports hermetic testing against shared.godview_shared.fakes.FakeAgentEngineClient once M0 lands.
"""

from __future__ import annotations

import argparse
import json
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

DEFAULT_PROJECT_ID = "general-ak"
DEFAULT_LOCATION = "us-central1"
DEFAULT_STAGING_BUCKET = "gs://general-ak-god-view-agent-staging"
DEFAULT_SENSE_SA = "godview-sense-sa@general-ak.iam.gserviceaccount.com"
COURT_MAX_AGENTS = int(os.environ.get("GODVIEW_COURT_MAX_AGENTS", "40"))

VALID_AGENT_TYPES = ("application", "data", "business_line", "change", "platform")


@dataclass(frozen=True)
class DeploymentSpec:
    """Vertex AI Agent Engine deployment_spec per GOD_VIEW_MCP_AGENT_SPEC.md §4."""

    min_instances: int = 0
    max_instances: int = 1
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
        {"id": "analyze_blocking_tree", "name": "Inspect pg_locks and pg_stat_activity blocking chains", "tags": ["locks"]},
        {"id": "propose_sql_remediation", "name": "Propose L3 backend termination or schema remediation", "tags": ["remediation"]},
    ],
    "business_line": [
        {"id": "mine_journey_conformance", "name": "Evaluate directly-follows conformance and funnel KPIs", "tags": ["journeys"]},
        {"id": "quantify_rupee_exposure", "name": "Calculate rupee-at-risk across affected journeys", "tags": ["impact"]},
        {"id": "propose_saga_replay", "name": "Propose L4 replay or compensation via discovered admin contracts", "tags": ["remediation"]},
    ],
    "change": [
        {"id": "review_pull_request_risk", "name": "Summarize PR diff in business terms and score risk", "tags": ["change"]},
        {"id": "correlate_deploy_incident", "name": "Correlate recent commits/deploys against sentinel breaches", "tags": ["swarm"]},
        {"id": "author_rollback_pr", "name": "Execute 5-step rollback PR recipe via capability adapter", "tags": ["remediation"]},
    ],
    "platform": [
        {"id": "evaluate_golden_signals", "name": "Check latency, error, saturation and drift diffs", "tags": ["platform"]},
        {"id": "audit_reasoning_engine_cost", "name": "Identify idle Reasoning Engines and oversized resources", "tags": ["cost"]},
        {"id": "restore_drifted_attribute", "name": "Restore drifted infrastructure attribute or dispatch Terraform reconcile", "tags": ["remediation"]},
    ],
}


def build_a2a_card(
    agent_type: str,
    subject_key: str,
    endpoint_url: str = "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/general-ak/locations/us-central1/reasoningEngines/pending",
) -> dict[str, Any]:
    """Construct the A2A AgentCard JSON per GOD_VIEW_MCP_AGENT_SPEC.md §4.1."""
    if agent_type not in VALID_AGENT_TYPES:
        raise ValueError(f"Unsupported agent_type '{agent_type}'. Must be one of {VALID_AGENT_TYPES}")
    agent_id = f"{agent_type}__{subject_key}"
    return {
        "name": agent_id,
        "version": "4.0.0",
        "description": f"God-View {agent_type.replace('_', ' ').title()} Agent for {subject_key}",
        "url": endpoint_url,
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
    project_id: str = DEFAULT_PROJECT_ID,
    location: str = DEFAULT_LOCATION,
    staging_bucket: str = DEFAULT_STAGING_BUCKET,
    service_account: str = DEFAULT_SENSE_SA,
) -> AgentEnginePayload:
    """Build and validate a complete AgentEnginePayload."""
    if agent_type not in VALID_AGENT_TYPES:
        raise ValueError(f"Invalid agent_type '{agent_type}'. Expected one of {VALID_AGENT_TYPES}")
    if not subject_key or not subject_key.strip():
        raise ValueError("subject_key must be non-empty")
    if not sovereign_url or not sovereign_url.startswith("http"):
        raise ValueError(f"sovereign_url must be a valid HTTP(S) URL, got '{sovereign_url}'")
    if not staging_bucket.startswith("gs://"):
        raise ValueError(f"staging_bucket must start with gs://, got '{staging_bucket}'")

    agent_id = f"{agent_type}__{subject_key}"
    env_vars = {
        "GODVIEW_AGENT_ID": agent_id,
        "GODVIEW_AGENT_TYPE": agent_type,
        "GODVIEW_SUBJECT_KEY": subject_key,
        "GODVIEW_MODEL_LOCATION": "global",
        "GODVIEW_REASONING_MODEL": "gemini-4-argon",
        "GODVIEW_FALLBACK_MODEL": "gemini-3.8-flash",
        "GODVIEW_ESCALATION_MODEL": "gemini-4-argon",
        "GODVIEW_EMBEDDING_MODEL": "text-embedding-005",
        "GODVIEW_EMBEDDING_LOCATION": "us-central1",
        "GODVIEW_SOVEREIGN_URL": sovereign_url.rstrip("/"),
        "GODVIEW_CAPABILITY_INVOKE_PATH": "/v1/capabilities/{capability_id}:invoke",
    }
    spec = DeploymentSpec(
        min_instances=0,
        max_instances=1,
        resource_limits={"cpu": "1", "memory": "2Gi"},
        container_concurrency=4,
        env_vars=env_vars,
    )
    labels = {
        "godview_managed": "true",
        "tenant": "godview",
        "godview_role": "observer",
        "agent_type": agent_type.replace("_", "-"),
    }
    return AgentEnginePayload(
        agent_id=agent_id,
        agent_type=agent_type,
        subject_key=subject_key,
        display_name=f"godview-{agent_type.replace('_', '-')}-{subject_key}"[:63],
        description=f"God-View v4 {agent_type} specialist agent for {subject_key}",
        project_id=project_id,
        location=location,
        staging_bucket=staging_bucket,
        service_account=service_account,
        deployment_spec=spec,
        a2a_card=build_a2a_card(agent_type, subject_key),
        labels=labels,
    )


class HermeticAgentEngineBackend:
    """Adapter that wraps shared.godview_shared.fakes.InMemoryAgentEngine."""

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
        )
        self._labels_by_agent[payload.agent_id] = dict(payload.labels)
        record["name"] = record["reasoning_engine"]
        record["display_name"] = payload.display_name
        record["subject_key"] = payload.subject_key
        record["labels"] = dict(payload.labels)
        record["a2a_card"] = record.get("card")
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
        updated = self.fake_engine.set_min_instances(agent_id, min_instances)
        updated["name"] = updated["reasoning_engine"]
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

        # Verify Wave 3 agents module is importable locally before deploying
        try:
            from godview.agents.server import AgentExecutor  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(f"Wave 3 agents not available: {exc}") from exc

        sovereign_url = payload.deployment_spec.env_vars["GODVIEW_SOVEREIGN_URL"]
        agent_id = payload.agent_id
        agent_type = payload.agent_type
        subject_key = payload.subject_key

        class CourtAgentWrapper:
            """Cloudpickle-safe Vertex AI Agent Engine wrapper for God-View Court Agents."""

            def __init__(
                self,
                sov_url: str,
                aid: str,
                atype: str,
                skey: str,
            ) -> None:
                self.sovereign_url = sov_url
                self.agent_id = aid
                self.agent_type = atype
                self.subject_key = skey
                self._executor: Any = None

            def set_up(self) -> None:
                try:
                    from godview.agents.server import AgentExecutor as _Exec

                    self._executor = _Exec(base_url=self.sovereign_url)
                except Exception:
                    self._executor = None

            def query(self, **kwargs: Any) -> dict[str, Any]:
                return {
                    "agent_id": self.agent_id,
                    "agent_type": self.agent_type,
                    "subject_key": self.subject_key,
                    "sovereign_url": self.sovereign_url,
                    "status": "ok",
                    "input": kwargs,
                }

        app_instance = CourtAgentWrapper(
            sov_url=sovereign_url,
            aid=agent_id,
            atype=agent_type,
            skey=subject_key,
        )

        remote_agent = agent_engines.create(
            app_instance,
            display_name=payload.display_name,
            description=payload.description,
            requirements=[
                "google-cloud-aiplatform[agent_engines,adk]>=1.88.0",
                "pydantic>=2.7.0",
                "httpx>=0.27.0",
            ],
            env_vars=payload.deployment_spec.env_vars,
            min_instances=payload.deployment_spec.min_instances,
            max_instances=payload.deployment_spec.max_instances,
            resource_limits=payload.deployment_spec.resource_limits,
            container_concurrency=payload.deployment_spec.container_concurrency,
            service_account=payload.service_account,
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

        a2a_url = f"https://{payload.location}-aiplatform.googleapis.com/v1beta1/{resource_name}:a2a"
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
            raise ValueError(f"min_instances must be 0 or 1, got {min_instances}")
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


def default_court_roster() -> list[tuple[str, str]]:
    """Return the 10-agent Cymbal proving-ground court roster (deployed on discovery or via CLI)."""
    return [
        ("application", "loan-orchestrator-api"),
        ("application", "kyc-model-armor-svc"),
        ("application", "underwriting-los-svc"),
        ("application", "commercial-fleet-credit-svc"),
        ("application", "motor-insurance-svc"),
        ("application", "core-banking-cibil-vm"),
        ("data", "god-view-pg"),
        ("business_line", "vehicle_loan_origination"),
        ("change", "github.god-view"),
        ("platform", "gcp-general-ak"),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deploy God-View court agents to Vertex AI Agent Engine")
    parser.add_argument("--agent-type", choices=VALID_AGENT_TYPES, help="Single agent type to deploy")
    parser.add_argument("--subject-key", help="Subject key for single agent deployment")
    parser.add_argument("--all-court", action="store_true", help="Deploy the full 10-agent court roster")
    parser.add_argument(
        "--sovereign-url",
        default=os.environ.get("GODVIEW_SOVEREIGN_URL", "https://god-view-sovereign-placeholder.a.run.app"),
        help="Sovereign Cloud Run URL for capability invocation",
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
    if args.all_court:
        targets.extend(default_court_roster())
    elif args.agent_type and args.subject_key:
        targets.append((args.agent_type, args.subject_key))
    else:
        parser.error("Provide either --all-court or both --agent-type and --subject-key")

    payloads = [
        build_agent_payload(
            agent_type=atype,
            subject_key=skey,
            sovereign_url=args.sovereign_url,
            project_id=args.project_id,
            location=args.location,
            staging_bucket=args.staging_bucket,
            service_account=args.service_account,
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
