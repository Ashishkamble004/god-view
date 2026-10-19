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
3. Every inferred capability, KPI, invariant, and journey MUST cite the specific evidence reference (e.g. 'openapi:GET /v1/items', 'env:CONFIG_KEY'). Every capability string MUST include an inline citation of the exact OpenAPI route path or operation_id when routes exist, and every `evidence` item's `ref` and every `kpis`, `invariants`, and `journeys` item's `evidence` field MUST use exact strings from `valid_evidence_refs`.
4. The 'domain' field must be a concise, free-text description of the business domain directly indicated by the evidence.
5. Invariants must represent verifiable technical or logical assertions derivable from the service's contract and operations. Include grounded `kpis` and `invariants` when supported by `valid_evidence_refs`, citing exact strings from `valid_evidence_refs` in their `evidence` field.
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


def _attach_metadata(obj: Any, key: str, value: Any) -> None:
    """Safely attach non-schema metadata to Pydantic models or objects."""
    try:
        object.__setattr__(obj, key, value)
    except Exception:
        pass


def compute_evidence_hash(evidence_bundle: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hash of canonical evidence bundle."""
    raw = json.dumps(evidence_bundle, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def extract_bundle_valid_refs(evidence_bundle: dict[str, Any]) -> set[str]:
    """Extract canonical and exact matchable reference IDs from an evidence bundle."""
    valid_refs: set[str] = set()

    # 1. Routes and operations
    routes = evidence_bundle.get("routes", [])
    for r in routes:
        if isinstance(r, dict):
            method = str(r.get("method") or "").strip().upper()
            path = str(r.get("path") or "").strip()
            op_id = str(r.get("operation_id") or "").strip()
            summary = str(r.get("summary") or "").strip()
            if method and path:
                valid_refs.add(f"openapi:{method} {path}")
                valid_refs.add(f"{method} {path}")
                valid_refs.add(f"openapi:{path}")
                valid_refs.add(path)
            elif path:
                valid_refs.add(f"openapi:{path}")
                valid_refs.add(path)
            if op_id:
                valid_refs.add(f"openapi:{op_id}")
                valid_refs.add(op_id)
            if summary:
                valid_refs.add(f"openapi:{summary}")

    # 2. Environment variables
    for env in evidence_bundle.get("env_var_names", []):
        env_str = str(env).strip()
        if env_str:
            valid_refs.add(f"env:{env_str}")
            valid_refs.add(f"env_var:{env_str}")
            valid_refs.add(env_str)

    # Labels
    labels = evidence_bundle.get("labels", {})
    if isinstance(labels, dict):
        for k, v in labels.items():
            k_str = str(k).strip()
            if k_str:
                valid_refs.add(f"label:{k_str}")
                valid_refs.add(k_str)
                if v:
                    valid_refs.add(f"label:{k_str}={v}")

    # Attributes & config summary
    attrs = evidence_bundle.get("attributes", {})
    if isinstance(attrs, dict):
        for k, v in attrs.items():
            k_str = str(k).strip()
            if k_str:
                valid_refs.add(f"attr:{k_str}")
                valid_refs.add(f"attribute:{k_str}")
                valid_refs.add(k_str)
                if v is not None and not isinstance(v, (dict, list)):
                    valid_refs.add(f"attr:{k_str}={v}")
                    valid_refs.add(f"{k_str}={v}")

    config = evidence_bundle.get("config_summary", {})
    if isinstance(config, dict):
        for k, v in config.items():
            k_str = str(k).strip()
            if k_str:
                valid_refs.add(f"config:{k_str}")
                valid_refs.add(f"attr:{k_str}")
                valid_refs.add(k_str)
                if v is not None and not isinstance(v, (dict, list)):
                    valid_refs.add(f"config:{k_str}={v}")

    # 3. Upstreams / Downstreams
    all_upstreams = list(evidence_bundle.get("upstreams", [])) + list(
        evidence_bundle.get("upstream_services", [])
    )
    for up in all_upstreams:
        up_str = str(up).strip()
        if up_str:
            valid_refs.add(f"trace:{up_str}")
            valid_refs.add(f"edge:{up_str}")
            valid_refs.add(f"upstream:{up_str}")
            valid_refs.add(up_str)
    all_downstreams = list(evidence_bundle.get("downstreams", [])) + list(
        evidence_bundle.get("downstream_services", [])
    )
    for down in all_downstreams:
        down_str = str(down).strip()
        if down_str:
            valid_refs.add(f"trace:{down_str}")
            valid_refs.add(f"edge:{down_str}")
            valid_refs.add(f"downstream:{down_str}")
            valid_refs.add(down_str)

    # 4. Service name / kind / locator
    svc_name = str(evidence_bundle.get("service_name") or "").strip()
    if svc_name:
        valid_refs.add(f"service:{svc_name}")
        valid_refs.add(f"service_name:{svc_name}")
        valid_refs.add(svc_name)
        valid_refs.add(f"log:{svc_name}")
    kind = str(evidence_bundle.get("kind") or "").strip()
    if kind:
        valid_refs.add(f"kind:{kind}")
        valid_refs.add(kind)
    locator = str(evidence_bundle.get("locator") or "").strip()
    if locator:
        valid_refs.add(f"locator:{locator}")
        valid_refs.add(locator)

    # 5. Logs sample
    for log_item in evidence_bundle.get("logs_sample", []):
        log_str = str(log_item).strip()
        if log_str:
            valid_refs.add(f"log:{log_str}")
            valid_refs.add(log_str)

    # 6. Doc excerpt
    if evidence_bundle.get("doc_excerpt"):
        valid_refs.add("doc")
        valid_refs.add("doc_excerpt")
        if svc_name:
            valid_refs.add(f"doc:{svc_name}")

    # 7. IAM summary
    iam = evidence_bundle.get("iam_summary")
    if iam:
        valid_refs.add("iam")
        valid_refs.add("iam_summary")
        if isinstance(iam, (list, tuple, set)):
            for item in iam:
                item_str = str(item).strip()
                if item_str:
                    valid_refs.add(f"iam:{item_str}")
                    valid_refs.add(item_str)
        elif isinstance(iam, str):
            valid_refs.add(f"iam:{iam}")
            valid_refs.add(iam)

    return valid_refs


def check_ref_resolves(ref_input: Any, valid_refs: set[str]) -> bool:
    """Check if an evidence ref exact-matches any valid bundle reference ID."""
    if not ref_input:
        return False
    ref_str = str(ref_input).strip().strip("\"'()[]{} ")
    if not ref_str:
        return False
    if ref_str in valid_refs:
        return True
    ref_lower = ref_str.lower()
    return any(ref_lower == v.lower() for v in valid_refs)


def check_item_evidence_resolves(evidence_val: Any, valid_refs: set[str]) -> bool:
    """Check if a KPI/invariant/journey evidence field resolves to bundle items."""
    if not evidence_val:
        return False
    if isinstance(evidence_val, (list, tuple, set)):
        if not evidence_val:
            return False
        return all(check_item_evidence_resolves(item, valid_refs) for item in evidence_val)
    if isinstance(evidence_val, BaseModel):
        ref = getattr(evidence_val, "ref", None)
        return check_ref_resolves(ref, valid_refs)
    if isinstance(evidence_val, dict):
        ref = evidence_val.get("ref") or evidence_val.get("id")
        return check_ref_resolves(ref, valid_refs)

    s = str(evidence_val).strip()
    if not s:
        return False
    if check_ref_resolves(s, valid_refs):
        return True
    parts = [p.strip() for p in re.split(r"[,;\n]+", s) if p.strip()]
    if parts and all(check_ref_resolves(p, valid_refs) for p in parts):
        return True
    return False


def is_business_context_stale(bc: BusinessContext | None) -> bool:
    """Return True if a stored BusinessContext is missing, uncited, low-confidence, or has placeholder inferred_by."""
    if bc is None:
        return True
    if getattr(bc, "confidence", 0.0) < 0.5:
        return True
    inferred_by = str(getattr(bc, "inferred_by", "") or "").strip()
    if inferred_by in ("", "none", "llm-reasoner"):
        return True
    evidence = getattr(bc, "evidence", None) or []
    if not evidence:
        return True
    if not any(
        getattr(e, "ref", None) or (isinstance(e, dict) and e.get("ref"))
        for e in evidence
    ):
        return True
    role_summary = str(getattr(bc, "role_summary", "") or "").lower()
    if "cannot be verified" in role_summary:
        return True
    risk_notes = getattr(bc, "risk_notes", []) or []
    for rk in risk_notes:
        rk_str = str(
            getattr(rk, "description", None)
            or getattr(rk, "note", None)
            or (rk.get("description") if isinstance(rk, dict) else None)
            or (rk.get("note") if isinstance(rk, dict) else None)
            or rk
        ).lower()
        if "cannot be verified" in rk_str:
            return True
    return False


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

        # 3. Call graph neighbours from edges (matching node ID, short name, or backend target)
        upstreams: list[str] = []
        downstreams: list[str] = []
        upstream_services: list[str] = []
        downstream_services: list[str] = []
        svc_name = service.name
        for e in edges:
            is_dst = (
                e.dst == node_id
                or e.dst == svc_name
                or e.dst.endswith(f".{svc_name}")
                or e.dst.endswith(f"__{svc_name}")
                or f".{svc_name}" in e.dst
            )
            if is_dst:
                upstreams.append(e.src)
                src_short = e.src.split(".")[-1].split("__")[-1]
                upstream_services.append(src_short)
                upstream_services.append(e.src)

            is_src = (
                e.src == node_id
                or e.src == svc_name
                or e.src.endswith(f".{svc_name}")
                or e.src.endswith(f"__{svc_name}")
                or f".{svc_name}" in e.src
            )
            if is_src:
                downstreams.append(e.dst)
                dst_short = e.dst.split(".")[-1].split("__")[-1]
                downstream_services.append(dst_short)
                downstream_services.append(e.dst)

        # 4. Filter logs for this service
        service_logs: list[str] = []
        if logs:
            for entry in logs:
                svc = entry.get("service") or entry.get("resource", {}).get("labels", {}).get("service_name")
                if svc == service.name or svc == node_id:
                    sample = entry.get("masked_sample") or entry.get("textPayload") or ""
                    if sample:
                        service_logs.append(str(sample)[:200])

        # 5. Attributes, IAM, Config summary
        attrs = getattr(service, "attributes", {}) or {}
        labels = {k: v for k, v in service.labels.items() if not k.startswith("env_var_")}
        iam_summary = []
        if "service_account" in service.labels:
            iam_summary.append(f"service_account:{service.labels['service_account']}")
        if "iam_role" in service.labels:
            iam_summary.append(f"role:{service.labels['iam_role']}")

        config_summary = {
            "kind": service.kind,
            "locator": service.locator,
            "adapter_family": service.adapter_family,
        }
        for k in ("region", "zone", "port", "ingress", "min_instances", "max_instances"):
            if k in service.labels:
                config_summary[k] = service.labels[k]
            elif isinstance(attrs, dict) and k in attrs:
                config_summary[k] = attrs[k]

        return {
            "service_name": service.name,
            "kind": service.kind,
            "locator": service.locator,
            "labels": labels,
            "attributes": attrs,
            "env_var_names": sorted(set(env_names)),
            "routes": routes_summary,
            "upstreams": sorted(set(upstreams)),
            "downstreams": sorted(set(downstreams)),
            "upstream_services": sorted(set(upstream_services)),
            "downstream_services": sorted(set(downstream_services)),
            "iam_summary": sorted(set(iam_summary)),
            "config_summary": config_summary,
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
        if service.business_context is not None and not is_business_context_stale(service.business_context):
            existing_good = service.business_context
        elif self.firestore is not None:
            node_id = service.doc_id or f"{service.env_id}__{service.kind}.{service.name}"
            try:
                stored = self.firestore.get("discovered_services", node_id)
                if stored and stored.get("business_context"):
                    candidate = BusinessContext(**stored["business_context"])
                    if not is_business_context_stale(candidate):
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

            uncited_ctx = BusinessContext(
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
            _attach_metadata(uncited_ctx, "inference_status", "failed")
            return uncited_ctx

        # Check if inference is uncited
        is_uncited = not ctx.evidence
        inference_status = getattr(ctx, "inference_status", "uncited" if is_uncited else "inferred")
        _attach_metadata(ctx, "inference_status", inference_status)
        _attach_metadata(ctx, "_evidence_hash", ev_hash)

        node_id = service.doc_id or f"{service.env_id}__{service.kind}.{service.name}"
        now_iso = datetime.now(tz=UTC).isoformat()

        if is_uncited:
            # Uncited inference: cap confidence below 0.5, schedule retry, and do NOT cache!
            if self.firestore is not None:
                try:
                    self.firestore.set(
                        "discovered_services",
                        node_id,
                        {
                            "business_context": ctx.model_dump(mode="json"),
                            "inference_status": "uncited",
                            "inference_retry_after": now_iso,
                        },
                        merge=True,
                    )
                except Exception:
                    pass
            return ctx

        # Successful cited inference: cache by evidence hash and persist to Firestore
        self._cache_by_evidence_hash[ev_hash] = ctx
        if self.firestore is not None:
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
            prompt_bundle = dict(evidence_bundle)
            prompt_bundle["valid_evidence_refs"] = sorted(extract_bundle_valid_refs(evidence_bundle))
            prompt = (
                f"{INFERENCE_SYSTEM_PROMPT}\n\n"
                f"Evidence for service '{service_name}':\n"
                f"{json.dumps(prompt_bundle, indent=2)}\n"
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
            from godview_shared.config import MODEL_TABLE

            res_inferred_by = getattr(res, "inferred_by", None)
            if res_inferred_by in ("", "none", "llm-reasoner"):
                res_inferred_by = None
            model_name = (
                getattr(res, "model_id", None)
                or getattr(self.llm, "last_model_id", None)
                or getattr(self.llm, "model_name", None)
                or getattr(self.llm, "model", None)
                or res_inferred_by
                or MODEL_TABLE["reasoning"].model_id
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

            valid_refs = extract_bundle_valid_refs(evidence_bundle)

            # Keep only refs the model emitted, and drop any whose ref doesn't resolve to an item in the bundle
            cleaned_evidence: list[BusinessContextEvidence] = []
            for ev in res_bc.evidence:
                if not _is_non_empty(ev):
                    continue
                ref_val = (
                    getattr(ev, "ref", None)
                    if isinstance(ev, BaseModel)
                    else ev.get("ref")
                    if isinstance(ev, dict)
                    else str(ev)
                )
                if check_ref_resolves(ref_val, valid_refs):
                    if isinstance(ev, BusinessContextEvidence):
                        cleaned_evidence.append(ev)
                    elif isinstance(ev, dict):
                        cleaned_evidence.append(BusinessContextEvidence(**ev))
                    else:
                        cleaned_evidence.append(BusinessContextEvidence(ref=str(ref_val)))

            res_bc.evidence = cleaned_evidence

            # If no usable evidence survived, cap confidence below 0.5 (min(conf, 0.3)) and mark uncited
            if not res_bc.evidence:
                res_bc.confidence = min(res_bc.confidence, 0.3)
                _attach_metadata(res_bc, "inference_status", "uncited")
            else:
                _attach_metadata(res_bc, "inference_status", "inferred")

            # Same check for KPIs/invariants/journeys: their evidence must also resolve to bundle items, or the item is dropped
            res_bc.kpis = [
                k
                for k in res_bc.kpis
                if _is_non_empty(k)
                and check_item_evidence_resolves(
                    getattr(k, "evidence", None)
                    if isinstance(k, BaseModel)
                    else (k.get("evidence") if isinstance(k, dict) else None),
                    valid_refs,
                )
            ]
            res_bc.invariants = [
                i
                for i in res_bc.invariants
                if _is_non_empty(i)
                and check_item_evidence_resolves(
                    getattr(i, "evidence", None)
                    if isinstance(i, BaseModel)
                    else (i.get("evidence") if isinstance(i, dict) else None),
                    valid_refs,
                )
            ]
            res_bc.journeys = [
                j
                for j in res_bc.journeys
                if _is_non_empty(j)
                and check_item_evidence_resolves(
                    getattr(j, "evidence", None)
                    if isinstance(j, BaseModel)
                    else (j.get("evidence") if isinstance(j, dict) else None),
                    valid_refs,
                )
            ]

            # Post-validate capabilities against OpenAPI routes when operation_id is present
            route_paths = {
                str(r.get("path") or "").strip()
                for r in routes
                if isinstance(r, dict) and r.get("path")
            }
            route_ops = {
                str(r.get("operation_id") or "").strip()
                for r in routes
                if isinstance(r, dict) and r.get("operation_id")
            }
            if route_ops:
                grounded_caps = [
                    c
                    for c in res_bc.capabilities
                    if isinstance(c, str)
                    and (
                        any(p in c for p in route_paths if p)
                        or any(op in c for op in route_ops if op)
                    )
                ]
                if grounded_caps:
                    res_bc.capabilities = grounded_caps
                else:
                    res_bc.capabilities = [
                        f"{r.get('summary') or r.get('operation_id')} (openapi:{r.get('method', 'GET')} {r.get('path')} {r.get('operation_id')})"
                        for r in routes
                        if isinstance(r, dict) and (r.get("path") or r.get("operation_id"))
                    ]

            res_bc.inferred_by = model_name
            res_bc.inferred_at = now_iso
            return res_bc

        # When LLM is not configured / unavailable, return un-inferred BusinessContext honestly with confidence 0.0
        no_llm_ctx = BusinessContext(
            role_summary=f"Infrastructure service '{service_name}'",
            domain="unknown",
            capabilities=[
                r.get("summary") or r.get("operation_id") or f"{r.get('method')} {r.get('path')}"
                for r in routes
            ],
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
        _attach_metadata(no_llm_ctx, "inference_status", "uncited")
        return no_llm_ctx

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
