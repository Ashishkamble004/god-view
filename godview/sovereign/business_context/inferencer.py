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
import re
from datetime import UTC, datetime
from typing import Any

from godview_shared.contracts import (
    BusinessContext,
    BusinessContextEvidence,
    DiscoveredEdgeDoc,
    DiscoveredServiceDoc,
    JourneyDoc,
)
from pydantic import BaseModel, Field

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


class InferredJourneyStep(BaseModel):
    node: str = ""
    operation: str = ""


class InferredJourney(BaseModel):
    name: str
    steps: list[InferredJourneyStep | str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[dict[str, Any]] | list[str] | str = Field(default_factory=list)


class InferredJourneysResponse(BaseModel):
    journeys: list[InferredJourney] = Field(default_factory=list)


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

        # Check existing stored business context on service doc or Firestore
        existing_good: BusinessContext | None = None
        if service.business_context is not None and getattr(service.business_context, "confidence", 0.0) > 0.0:
            existing_good = service.business_context
        elif self.firestore is not None:
            node_id = service.doc_id or f"{service.env_id}__{service.kind}.{service.name}"
            try:
                stored = self.firestore.get("discovered_services", node_id)
                if stored and stored.get("business_context"):
                    candidate = BusinessContext(**stored["business_context"])
                    if candidate.confidence > 0.0:
                        existing_good = candidate
            except Exception:
                pass

        if (
            existing_good is not None
            and getattr(existing_good, "inferred_at", None) is not None
            and ev_hash == getattr(existing_good, "_evidence_hash", None)
        ):
            self._cache_by_evidence_hash[ev_hash] = existing_good
            return existing_good

        failed = False
        try:
            ctx = await self._call_llm_inference(service.name, evidence_bundle, ev_hash)
            if ctx.confidence == 0.0 and ctx.domain == "unknown" and existing_good is not None:
                failed = True
        except Exception as exc:
            logger.warning("LLM business context inference failed for %s: %s", service.name, exc)
            failed = True

        if failed:
            # On failure: keep the last good context, mark inference_status=failed with a retry-after, and don't cache!
            node_id = service.doc_id or f"{service.env_id}__{service.kind}.{service.name}"
            if self.firestore is not None:
                now_iso = datetime.now(tz=UTC).isoformat()
                try:
                    self.firestore.set(
                        "discovered_services",
                        node_id,
                        {
                            "inference_status": "failed",
                            "inference_retry_after": now_iso,
                        },
                        merge=True,
                    )
                except Exception:
                    pass
            if existing_good is not None:
                return existing_good

            return BusinessContext(
                role_summary=f"Inference failed for {service.name}",
                domain="unknown",
                capabilities=[r.get("summary") or r.get("operation_id") or f"{r.get('method')} {r.get('path')}" for r in evidence_bundle.get("routes", [])],
                upstream=evidence_bundle.get("upstreams", []),
                downstream=evidence_bundle.get("downstreams", []),
                journeys=[],
                kpis=[],
                invariants=[],
                risk_notes=[],
                confidence=0.0,
                evidence=[],
                inferred_by="none",
                inferred_at=datetime.now(tz=UTC).isoformat(),
            )

        # Successful inference: cache by evidence hash and persist to Firestore
        self._cache_by_evidence_hash[ev_hash] = ctx
        if self.firestore is not None:
            node_id = service.doc_id or f"{service.env_id}__{service.kind}.{service.name}"
            try:
                self.firestore.set(
                    "discovered_services",
                    node_id,
                    {
                        "business_context": ctx.model_dump(mode="json"),
                        "inference_status": "inferred",
                    },
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
            self.total_llm_calls += 1
            if hasattr(self.llm, "generate_structured"):
                try:
                    res = await self.llm.generate_structured(
                        role="reasoning",
                        prompt=prompt,
                        schema=BusinessContext,
                    )
                except Exception:
                    res = await self.llm.generate(
                        role="reasoning",
                        contents=prompt,
                        response_schema=BusinessContext,
                    )
            else:
                res = await self.llm.generate(
                    role="reasoning",
                    contents=prompt,
                    response_schema=BusinessContext,
                )
            model_name = (
                getattr(res, "model_id", None)
                or getattr(self.llm, "model_name", getattr(self.llm, "model", "llm-reasoner"))
            )
            if isinstance(res, BusinessContext):
                res_bc = res
            else:
                parsed_ctx = getattr(res, "parsed", None)
                if parsed_ctx is None and hasattr(res, "text") and res.text:
                    raw_text = res.text.strip()
                    if raw_text.startswith("```"):
                        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                        raw_text = re.sub(r"\s*```$", "", raw_text)
                    try:
                        parsed_ctx = json.loads(raw_text)
                    except Exception:
                        pass
                elif parsed_ctx is None and isinstance(res, dict):
                    parsed_ctx = res

                if isinstance(parsed_ctx, BusinessContext):
                    res_bc = parsed_ctx
                elif isinstance(parsed_ctx, dict):
                    res_bc = BusinessContext(**parsed_ctx)
                else:
                    raise ValueError(f"Could not parse BusinessContext from LLM response: {res}")

            def _is_non_empty(item: Any) -> bool:
                if isinstance(item, BaseModel):
                    return any(bool(v) for v in item.model_dump().values())
                if isinstance(item, dict):
                    return any(bool(v) for v in item.values())
                return bool(item)

            # Ensure evidence references are never empty objects
            cleaned_evidence = [e for e in res_bc.evidence if _is_non_empty(e)]
            if not cleaned_evidence:
                ev_items: list[BusinessContextEvidence] = []
                for r in routes[:10]:
                    ref = f"openapi:{r.get('method', 'GET')} {r.get('path', '/')}"
                    summary = r.get("summary") or r.get("operation_id") or ""
                    ev_items.append(
                        BusinessContextEvidence(
                            kind="openapi",
                            ref=ref,
                            excerpt=summary or f"Route {ref}",
                        )
                    )
                for env_k in list(evidence_bundle.get("env_vars", {}).keys())[:5]:
                    ev_items.append(
                        BusinessContextEvidence(
                            kind="env",
                            ref=f"env:{env_k}",
                            excerpt=f"Environment variable {env_k}",
                        )
                    )
                cleaned_evidence = ev_items
            res_bc.evidence = cleaned_evidence

            # Ensure kpis/invariants/journeys do not contain empty objects
            res_bc.kpis = [k for k in res_bc.kpis if _is_non_empty(k)]
            res_bc.invariants = [i for i in res_bc.invariants if _is_non_empty(i)]
            res_bc.journeys = [j for j in res_bc.journeys if _is_non_empty(j)]

            res_bc.inferred_by = model_name
            res_bc.inferred_at = now_iso
            return res_bc

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
            if hasattr(self.llm, "generate_structured"):
                try:
                    res = await self.llm.generate_structured(
                        role="reasoning",
                        prompt=prompt,
                        schema=InferredJourneysResponse,
                    )
                except Exception:
                    res = await self.llm.generate(
                        role="reasoning",
                        contents=prompt,
                        response_schema=InferredJourneysResponse,
                    )
            else:
                res = await self.llm.generate(
                    role="reasoning",
                    contents=prompt,
                    response_schema=InferredJourneysResponse,
                )

            if isinstance(res, list):
                journey_items = res
            elif isinstance(res, dict) and "journeys" in res:
                journey_items = res["journeys"]
            else:
                parsed = getattr(res, "parsed", None)
                if parsed is None and isinstance(res, InferredJourneysResponse):
                    parsed = res
                elif parsed is None and hasattr(res, "text") and res.text:
                    raw_text = res.text.strip()
                    if raw_text.startswith("```"):
                        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                        raw_text = re.sub(r"\s*```$", "", raw_text)
                    try:
                        parsed = json.loads(raw_text)
                    except Exception as parse_err:
                        logger.warning("Failed to parse journey JSON: %s (raw=%s)", parse_err, raw_text[:200])

                if isinstance(parsed, InferredJourneysResponse):
                    journey_items = parsed.journeys
                elif isinstance(parsed, dict) and "journeys" in parsed:
                    journey_items = parsed["journeys"]
                elif isinstance(parsed, list):
                    journey_items = parsed
                elif hasattr(parsed, "journeys"):
                    journey_items = getattr(parsed, "journeys", [])
                else:
                    journey_items = []

            model_id = getattr(res, "model_id", None) or "gemini-4-argon"
            now_iso = datetime.now(tz=UTC).isoformat()
            journeys: list[JourneyDoc] = []
            for item in journey_items:
                name = getattr(item, "name", None) or (item.get("name") if isinstance(item, dict) else "")
                if not name:
                    continue
                raw_steps = getattr(item, "steps", None) or (item.get("steps", []) if isinstance(item, dict) else [])
                steps_data: list[dict[str, str]] = []
                for s in raw_steps:
                    node = ""
                    op = ""
                    if isinstance(s, dict):
                        node = str(s.get("node", ""))
                        op = str(s.get("operation", ""))
                    elif hasattr(s, "node"):
                        node = str(getattr(s, "node", ""))
                        op = str(getattr(s, "operation", ""))
                    elif isinstance(s, str):
                        node = s
                        op = ""

                    # Disambiguate if node contains operation (e.g. node_id.op or node_id:op)
                    if node:
                        for svc in services:
                            svc_id = svc.doc_id
                            if svc_id and (node.startswith(f"{svc_id}.") or node.startswith(f"{svc_id}:")):
                                if not op:
                                    op = node[len(svc_id) + 1:]
                                node = svc_id
                                break
                            svc_short = svc.name
                            if svc_short and (node.startswith(f"{svc_short}.") or node.startswith(f"{svc_short}:")):
                                if not op:
                                    op = node[len(svc_short) + 1:]
                                node = svc.doc_id or svc_short
                                break

                    steps_data.append({"node": node, "operation": op})
                conf_val = float(getattr(item, "confidence", 0.0) or (item.get("confidence", 0.0) if isinstance(item, dict) else 0.0))
                ev_val = getattr(item, "evidence", []) or (item.get("evidence", []) if isinstance(item, dict) else [])
                if isinstance(ev_val, str):
                    ev_val = [ev_val]
                journeys.append(
                    JourneyDoc(
                        doc_id=f"journey__{name.lower().replace(' ', '_')}",
                        name=name,
                        steps=steps_data,
                        intended_order_source="trace_dag",
                        status="active",
                        confidence=conf_val,
                        evidence=ev_val,
                        inferred_by=model_id,
                        inferred_at=now_iso,
                    )
                )
            return journeys
        except Exception as exc:
            logger.warning("LLM journey inference failed: %s", exc)

        return []
