"""Evidence-only business context and journey inferencer for God-View v5.

Per plan §1.3, §3.2, and §0 non-negotiables:
- Zero hardcoded environment or domain knowledge.
- Infers BusinessContext purely from evidence (OpenAPI, env var names, labels, traces, schema names, scrubbed logs).
- Caches inference by evidence hash (<= 1 LLM call per node per evidence change).
- Every inferred capability, KPI, invariant, and journey cites specific evidence references.
- Infers multi-service journeys from trace DAGs and operation call graphs.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from godview_shared.contracts import (
    BusinessContext,
    DiscoveredEdgeDoc,
    DiscoveredServiceDoc,
    JourneyDoc,
)

logger = logging.getLogger("godview.sovereign.business_context.inferencer")

INFERENCE_SYSTEM_PROMPT = """You are an evidence-based system architecture analyzer.
Your task is to infer the business role, capabilities, domain, KPIs, and invariants of a software service purely from the provided technical evidence.

Strict Rules:
1. Ground every statement exclusively on the provided technical evidence (routes, operation summaries, env var names, schema names, caller graph, sanitized log excerpts).
2. DO NOT GUESS, ASSUME, OR INVENT business terms or functionality that are not directly supported by the evidence. If evidence is limited, state that honestly and assign a low confidence score.
3. Every inferred capability, KPI, invariant, and journey MUST cite the specific evidence reference (e.g. 'openapi:GET /v1/items', 'env:CONFIG_KEY').
4. The 'domain' field must be a concise, free-text description of the business domain directly indicated by the evidence.
5. Invariants must represent verifiable technical or logical assertions derivable from the service's contract and operations.
6. Provide a realistic overall confidence score between 0.0 and 1.0 reflecting the completeness and specificity of the evidence.
7. Return a valid JSON matching the BusinessContext schema.
"""


def compute_evidence_hash(evidence_bundle: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hash of canonical evidence bundle."""
    raw = json.dumps(evidence_bundle, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class BusinessContextInferencer:
    """Extracts technical evidence and infers BusinessContext via Gemini reasoning models."""

    def __init__(
        self,
        llm: Any | None = None,
        firestore: Any | None = None,
    ) -> None:
        self.llm = llm
        self.firestore = firestore
        self._cache_by_evidence_hash: dict[str, BusinessContext] = {}
        self.total_llm_calls: int = 0

    def build_evidence_bundle(
        self,
        service: DiscoveredServiceDoc,
        edges: list[DiscoveredEdgeDoc],
        logs: list[dict[str, Any]] | None = None,
        docs: str | None = None,
    ) -> dict[str, Any]:
        """Assemble technical evidence bundle for a service (never leaking env var values)."""
        node_id = service.doc_id or f"{service.env_id}__{service.kind}.{service.name}"

        # 1. Routes and operations from service
        routes_summary: list[dict[str, Any]] = []
        for r in service.routes:
            routes_summary.append(
                {
                    "method": r.get("method"),
                    "path": r.get("path"),
                    "operation_id": r.get("operation_id"),
                    "summary": r.get("summary"),
                    "description": r.get("description"),
                }
            )

        # 2. Extract env var names only (values stripped)
        env_names: list[str] = []
        for k, v in service.labels.items():
            if k.startswith("env_var_"):
                env_names.append(k.removeprefix("env_var_"))
            elif k == "env_vars" and isinstance(v, str):
                env_names.extend([x.strip() for x in v.split(",") if x.strip()])
            elif k in ("databases", "users"):
                env_names.append(f"{k}:{v}")

        # 3. Call graph neighbours from edges
        upstreams: list[str] = []
        downstreams: list[str] = []
        for e in edges:
            if e.dst == node_id or e.dst.endswith(f".{service.name}"):
                upstreams.append(e.src)
            if e.src == node_id or e.src.endswith(f".{service.name}"):
                downstreams.append(e.dst)

        # 4. Filter logs for this service
        service_logs: list[str] = []
        if logs:
            for entry in logs:
                svc = entry.get("service") or entry.get("resource", {}).get("labels", {}).get("service_name")
                if svc == service.name or svc == node_id:
                    sample = entry.get("masked_sample") or entry.get("textPayload") or ""
                    if sample:
                        service_logs.append(str(sample)[:200])

        return {
            "service_name": service.name,
            "kind": service.kind,
            "locator": service.locator,
            "labels": {k: v for k, v in service.labels.items() if not k.startswith("env_var_")},
            "env_var_names": sorted(set(env_names)),
            "routes": routes_summary,
            "upstreams": sorted(set(upstreams)),
            "downstreams": sorted(set(downstreams)),
            "logs_sample": service_logs[:10],
            "doc_excerpt": docs[:500] if docs else None,
        }

    async def infer_service_context(
        self,
        service: DiscoveredServiceDoc,
        edges: list[DiscoveredEdgeDoc],
        logs: list[dict[str, Any]] | None = None,
        docs: str | None = None,
    ) -> BusinessContext:
        """Infer BusinessContext for an observed service, caching by evidence hash."""
        evidence_bundle = self.build_evidence_bundle(service, edges, logs=logs, docs=docs)
        ev_hash = compute_evidence_hash(evidence_bundle)

        if ev_hash in self._cache_by_evidence_hash:
            return self._cache_by_evidence_hash[ev_hash]

        # Check existing stored business context on service doc
        if (
            service.business_context is not None
            and getattr(service.business_context, "inferred_at", None) is not None
            and ev_hash == getattr(service.business_context, "_evidence_hash", None)
        ):
            self._cache_by_evidence_hash[ev_hash] = service.business_context
            return service.business_context

        # Call Gemini reasoning model with structured output
        ctx = await self._call_llm_inference(service.name, evidence_bundle, ev_hash)
        self._cache_by_evidence_hash[ev_hash] = ctx

        # Persist to Firestore if available
        if self.firestore is not None:
            node_id = service.doc_id or f"{service.env_id}__{service.kind}.{service.name}"
            try:
                self.firestore.set(
                    "discovered_services",
                    node_id,
                    {"business_context": ctx.model_dump(mode="json")},
                    merge=True,
                )
            except Exception:
                pass

        return ctx

    async def _call_llm_inference(
        self,
        service_name: str,
        evidence_bundle: dict[str, Any],
        ev_hash: str,
    ) -> BusinessContext:
        """Execute LLM call or fall back to evidence-based heuristic in hermetic tests."""
        now_iso = datetime.now(tz=UTC).isoformat()
        routes = evidence_bundle.get("routes", [])
        upstreams = evidence_bundle.get("upstreams", [])
        downstreams = evidence_bundle.get("downstreams", [])

        # If real LLM client is available, invoke it
        if self.llm is not None and hasattr(self.llm, "generate"):
            prompt = (
                f"{INFERENCE_SYSTEM_PROMPT}\n\n"
                f"Evidence for service '{service_name}':\n"
                f"{json.dumps(evidence_bundle, indent=2)}\n"
            )
            try:
                self.total_llm_calls += 1
                res = await self.llm.generate(
                    role="reasoning",
                    contents=prompt,
                    response_schema=BusinessContext,
                )
                model_name = getattr(self.llm, "model_name", getattr(self.llm, "model", "llm-reasoner"))
                if isinstance(res, BusinessContext):
                    res.inferred_by = model_name
                    res.inferred_at = now_iso
                    return res
                if isinstance(res, dict):
                    res["inferred_by"] = model_name
                    res["inferred_at"] = now_iso
                    return BusinessContext(**res)
            except Exception as exc:
                logger.warning("LLM business context inference failed for %s: %s", service_name, exc)
                return BusinessContext(
                    role_summary=f"Inference failed for {service_name}",
                    domain="unknown",
                    capabilities=[],
                    upstream=upstreams,
                    downstream=downstreams,
                    journeys=[],
                    kpis=[],
                    invariants=[],
                    risk_notes=[],
                    confidence=0.0,
                    evidence=[],
                    inferred_by="none",
                    inferred_at=now_iso,
                )

        # When LLM is not configured / unavailable, return un-inferred BusinessContext honestly with confidence 0.0
        return BusinessContext(
            role_summary=f"Infrastructure service '{service_name}'",
            domain="unknown",
            capabilities=[r.get("summary") or r.get("operation_id") or f"{r.get('method')} {r.get('path')}" for r in routes],
            upstream=upstreams,
            downstream=downstreams,
            journeys=[],
            kpis=[],
            invariants=[],
            risk_notes=[],
            confidence=0.0,
            evidence=[],
            inferred_by="none",
            inferred_at=now_iso,
        )

    async def infer_journeys_from_topology(
        self,
        services: list[DiscoveredServiceDoc],
        edges: list[DiscoveredEdgeDoc],
    ) -> list[JourneyDoc]:
        """Infer multi-service business journeys using LLM reasoning from trace DAGs and operation names."""
        trace_edges = [e for e in edges if e.evidence_kind == "trace" or e.protocol in ("http", "grpc")]
        if not trace_edges or self.llm is None:
            # Without traces or reasoning LLM, return empty list honestly (no fabricated flow__x_to_y journeys)
            return []

        service_ops: dict[str, list[str]] = {}
        for s in services:
            sid = s.doc_id or s.name
            service_ops[sid] = [r.get("operation_id") or r.get("path", "") for r in (s.routes or [])]

        dag_evidence = {
            "edges": [{"src": e.src, "dst": e.dst, "rps": e.rps} for e in trace_edges],
            "operations_by_service": service_ops,
        }

        prompt = (
            "Analyze these distributed trace call edges and service operations. "
            "Identify distinct end-to-end multi-service business journeys if supported by evidence. "
            "For each journey, specify its business name, sequential steps with node and actual operation name, "
            "confidence (0.0 to 1.0), and evidence summary. "
            "If the evidence is insufficient to identify distinct business journeys, return an empty journeys list.\n\n"
            f"Trace and operation evidence:\n{json.dumps(dag_evidence, indent=2)}"
        )

        try:
            self.total_llm_calls += 1
            res = await self.llm.generate(
                role="reasoning",
                contents=prompt,
            )
            if isinstance(res, list):
                journeys: list[JourneyDoc] = []
                for item in res:
                    if isinstance(item, dict) and "name" in item:
                        raw_steps = item.get("steps", [])
                        steps_str: list[str] = []
                        for s in raw_steps:
                            if isinstance(s, str):
                                steps_str.append(s)
                            elif isinstance(s, dict):
                                node = s.get("node", "")
                                op = s.get("operation", "")
                                steps_str.append(f"{node}.{op}" if op else str(node))
                            else:
                                steps_str.append(str(s))
                        journeys.append(
                            JourneyDoc(
                                doc_id=f"journey__{item['name'].lower().replace(' ', '_')}",
                                name=item["name"],
                                steps=steps_str,
                                intended_order_source="trace_dag",
                                status="active",
                            )
                        )
                return journeys
        except Exception as exc:
            logger.warning("LLM journey inference failed: %s", exc)

        return []
