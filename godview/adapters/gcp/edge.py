"""Google Cloud Edge (Load Balancing, Cloud Armor, IAP) Sense Adapter.

Per plan §1.4, §1.5, §3.2:
Discovers Global Forwarding Rules (lb_frontend), URL Maps, Backend Services (backend_service),
Security Policies (security_policy), and target backends (NEGs -> Cloud Run / API Gateway).
Evaluates whether security policies configure WAF sensitivity on evaluatePreconfiguredWaf rules.
Implements capabilities:
- `edge.describe_backends`
- `edge.describe_security_policies`
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import httpx
from godview_shared.adapter_contract import (
    AdapterHealth,
    DiscoveryResult,
    SenseAdapter,
    SenseCategory,
)
from godview_shared.capabilities import CapabilitySpec, get_capability
from godview_shared.contracts import (
    DiscoveredEdgeDoc,
    DiscoveredServiceDoc,
    Posture,
    TrustDecision,
)

from godview.adapters.gcp.drift import (
    build_fixture_http_transport,
    is_resource_excluded,
    resolve_gcp_bearer_token,
    resolve_gcp_project_id,
)

_SENSITIVITY_RE = re.compile(r"['\"]sensitivity['\"]\s*:\s*(\d+)", re.IGNORECASE)


class CloudEdgeAdapter(SenseAdapter):
    """Discovers GCP external HTTP(S) load balancers, Cloud Armor WAF policies, and backend routing."""

    family = "gcp"
    name = "gcp-edge"

    def __init__(
        self,
        *,
        project_id: str | None = None,
        region: str = "us-central1",
        zone: str = "us-central1-a",
        auth: Any | None = None,
        bearer_token: str | None = None,
        fixtures: dict[str, Any] | None = None,
        http_transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._fixtures = fixtures
        self.project_id = resolve_gcp_project_id(project_id, fixtures)
        self.region = region
        self.zone = zone
        self.auth = auth
        self.bearer_token = resolve_gcp_bearer_token(bearer_token)
        self._transport = http_transport or (
            build_fixture_http_transport(fixtures) if fixtures is not None else None
        )
        self.env_id = f"gcp-{self.project_id}"

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "x-goog-user-project": self.project_id,
        }
        token: str | None = None
        if self.auth is not None and hasattr(self.auth, "access_token"):
            try:
                token = self.auth.access_token()
            except Exception:
                token = None
        if not token:
            token = self.bearer_token or resolve_gcp_bearer_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _http_client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"headers": self._headers(), "timeout": 15.0}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def _fetch_paginated(self, url: str, items_key: str = "items") -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        page_token: str | None = None
        async with self._http_client() as client:
            while True:
                params: dict[str, Any] = {}
                if page_token:
                    params["pageToken"] = page_token
                resp = await client.get(url, params=params)
                if resp.status_code == 404:
                    return results
                resp.raise_for_status()
                data = resp.json()
                raw_items = data.get(items_key, [])
                if isinstance(raw_items, dict):
                    # aggregatedList format: zone/region -> subdict
                    for sub in raw_items.values():
                        if isinstance(sub, dict):
                            for v in sub.values():
                                if isinstance(v, list):
                                    results.extend(v)
                        elif isinstance(sub, list):
                            results.extend(sub)
                elif isinstance(raw_items, list):
                    results.extend(raw_items)
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
        return results

    async def _fetch_forwarding_rules(self) -> list[dict[str, Any]]:
        agg_url = f"https://compute.googleapis.com/compute/v1/projects/{self.project_id}/aggregated/forwardingRules"
        try:
            return await self._fetch_paginated(agg_url, "items")
        except Exception:
            global_url = f"https://compute.googleapis.com/compute/v1/projects/{self.project_id}/global/forwardingRules"
            return await self._fetch_paginated(global_url, "items")

    async def _fetch_url_maps(self) -> list[dict[str, Any]]:
        agg_url = f"https://compute.googleapis.com/compute/v1/projects/{self.project_id}/aggregated/urlMaps"
        try:
            return await self._fetch_paginated(agg_url, "items")
        except Exception:
            global_url = f"https://compute.googleapis.com/compute/v1/projects/{self.project_id}/global/urlMaps"
            return await self._fetch_paginated(global_url, "items")

    async def _fetch_backend_services(self) -> list[dict[str, Any]]:
        agg_url = f"https://compute.googleapis.com/compute/v1/projects/{self.project_id}/aggregated/backendServices"
        try:
            return await self._fetch_paginated(agg_url, "items")
        except Exception:
            global_url = f"https://compute.googleapis.com/compute/v1/projects/{self.project_id}/global/backendServices"
            return await self._fetch_paginated(global_url, "items")

    async def _fetch_security_policies(self) -> list[dict[str, Any]]:
        agg_url = f"https://compute.googleapis.com/compute/v1/projects/{self.project_id}/aggregated/securityPolicies"
        try:
            return await self._fetch_paginated(agg_url, "items")
        except Exception:
            global_url = f"https://compute.googleapis.com/compute/v1/projects/{self.project_id}/global/securityPolicies"
            return await self._fetch_paginated(global_url, "items")

    def senses(self) -> list[SenseCategory]:
        return [SenseCategory.CHANGE_PLANE, SenseCategory.SCOPED_OBSERVABILITY]

    def capabilities(self) -> dict[str, CapabilitySpec]:
        return {
            "edge.describe_backends": get_capability("edge.describe_backends"),
            "edge.describe_security_policies": get_capability("edge.describe_security_policies"),
        }

    async def health(self) -> AdapterHealth:
        try:
            frs = await self._fetch_forwarding_rules()
            state = "available"
            count = len(frs)
            error = None
        except Exception as exc:
            state = "unavailable"
            count = 0
            error = str(exc)
        evidence = {
            "api": "compute.googleapis.com/compute/v1",
            "forwarding_rules_count": count,
        }
        if error:
            evidence["error"] = error
        return AdapterHealth(
            adapter_name=self.name,
            family="gcp",
            state=state,  # type: ignore[arg-type]
            evidence=evidence,
            checked_at=datetime.now(tz=UTC),
        )

    async def discover(self) -> DiscoveryResult:
        errors: dict[str, str] = {}
        try:
            forwarding_rules = await self._fetch_forwarding_rules()
        except Exception as exc:
            forwarding_rules = []
            errors["forwarding_rules"] = str(exc)

        try:
            url_maps = await self._fetch_url_maps()
        except Exception as exc:
            url_maps = []
            errors["url_maps"] = str(exc)

        try:
            backend_services = await self._fetch_backend_services()
        except Exception as exc:
            backend_services = []
            errors["backend_services"] = str(exc)

        try:
            security_policies = await self._fetch_security_policies()
        except Exception as exc:
            security_policies = []
            errors["security_policies"] = str(exc)

        now_iso = datetime.now(tz=UTC).isoformat()
        services: list[DiscoveredServiceDoc] = []
        edges: list[DiscoveredEdgeDoc] = []

        # 1. Security Policies
        policy_lookup: dict[str, dict[str, Any]] = {}
        for sp in security_policies:
            p_name = str(sp.get("name", ""))
            if not p_name:
                continue
            rules = sp.get("rules", [])
            has_waf = False
            waf_sensitivities: list[int] = []
            rules_missing_sensitivity: list[int] = []

            for r in rules:
                expr = str(r.get("match", {}).get("expr", {}).get("expression", ""))
                if "evaluatePreconfiguredWaf" in expr:
                    has_waf = True
                    match = _SENSITIVITY_RE.search(expr)
                    if match:
                        waf_sensitivities.append(int(match.group(1)))
                    else:
                        rules_missing_sensitivity.append(int(r.get("priority", 1000)))

            waf_posture_str = "none"
            if has_waf:
                if rules_missing_sensitivity:
                    waf_posture_str = "missing_sensitivity"
                elif waf_sensitivities:
                    waf_posture_str = f"sensitivity-{waf_sensitivities[0]}"
                else:
                    waf_posture_str = "default"

            sp_doc_id = f"{self.env_id}__security_policy.{p_name}"
            policy_lookup[p_name] = sp
            services.append(
                DiscoveredServiceDoc(
                    doc_id=sp_doc_id,
                    env_id=self.env_id,
                    name=p_name,
                    kind="security_policy",
                    adapter_family="gcp",
                    locator=f"compute://securityPolicies/{p_name}",
                    labels={"waf_posture": waf_posture_str},
                    posture=Posture(
                        waf=waf_posture_str,
                        details={
                            "rules_count": len(rules),
                            "has_waf": has_waf,
                            "rules_missing_sensitivity": rules_missing_sensitivity,
                            "waf_sensitivities": waf_sensitivities,
                        },
                    ),
                    classification="other_read_only",
                    first_seen=now_iso,
                    last_seen=now_iso,
                    status="active",
                )
            )

        # 2. Backend Services
        bs_lookup: dict[str, dict[str, Any]] = {}
        for bs in backend_services:
            bs_name = str(bs.get("name", ""))
            if not bs_name:
                continue
            bs_lookup[bs_name] = bs
            iap_cfg = bs.get("iap", {})
            iap_enabled = bool(iap_cfg.get("enabled", False))
            log_cfg = bs.get("logConfig", {})
            logging_enabled = bool(log_cfg.get("enable", False))
            sp_url = str(bs.get("securityPolicy", ""))
            sp_name = sp_url.rsplit("/", 1)[-1] if sp_url else None

            waf_val = "unknown"
            if sp_name and sp_name in policy_lookup:
                sp_info = policy_lookup[sp_name]
                for r in sp_info.get("rules", []):
                    expr = str(r.get("match", {}).get("expr", {}).get("expression", ""))
                    if "evaluatePreconfiguredWaf" in expr:
                        m = _SENSITIVITY_RE.search(expr)
                        waf_val = f"sensitivity-{m.group(1)}" if m else "missing_sensitivity"
                        break

            bs_doc_id = f"{self.env_id}__backend_service.{bs_name}"
            bs_labels = dict(bs.get("labels") or {})
            is_observer = (
                bs_labels.get("godview_role") == "observer"
                or is_resource_excluded(bs_name, bs_labels)
            )
            labels = dict(bs_labels)
            if is_observer:
                labels["godview_role"] = "observer"
            services.append(
                DiscoveredServiceDoc(
                    doc_id=bs_doc_id,
                    env_id=self.env_id,
                    name=bs_name,
                    kind="backend_service",
                    adapter_family="gcp",
                    locator=f"compute://backendServices/{bs_name}",
                    labels=labels,
                    posture=Posture(
                        iap=iap_enabled,
                        waf=waf_val,
                        details={
                            "logging_enabled": logging_enabled,
                            "sample_rate": log_cfg.get("sampleRate", 1.0),
                            "security_policy": sp_name,
                        },
                    ),
                    classification="other_read_only",
                    first_seen=now_iso,
                    last_seen=now_iso,
                    status="active",
                )
            )

            # Edge from backend service to security policy if attached
            if sp_name:
                sp_id = f"{self.env_id}__security_policy.{sp_name}"
                edges.append(
                    DiscoveredEdgeDoc(
                        doc_id=f"{bs_doc_id}__to__{sp_id}",
                        src=bs_doc_id,
                        dst=sp_id,
                        protocol="iam",
                        evidence="config",
                        evidence_kind="config",
                        confidence=1.0,
                        first_seen=now_iso,
                        last_seen=now_iso,
                    )
                )

            # Edge from backend service to compute backend (via NEGs or backends list)
            backends = bs.get("backends", [])
            for b in backends:
                group_url = str(b.get("group", ""))
                group_name = group_url.rsplit("/", 1)[-1]
                # If group points to a Serverless NEG or instance group
                if group_name:
                    # In GCP serverless NEGs are typically named <svc>-neg or similar
                    target_svc_name = group_name.removesuffix("-neg")
                    target_id = f"{self.env_id}__cloud_run.{target_svc_name}"
                    edges.append(
                        DiscoveredEdgeDoc(
                            doc_id=f"{bs_doc_id}__to__{target_id}",
                            src=bs_doc_id,
                            dst=target_id,
                            protocol="http",
                            evidence="lb_route",
                            evidence_kind="lb_route",
                            confidence=0.9,
                            first_seen=now_iso,
                            last_seen=now_iso,
                        )
                    )

        # 3. Forwarding Rules & URL Maps (lb_frontend)
        for fr in forwarding_rules:
            fr_name = str(fr.get("name", ""))
            ip_address = str(fr.get("IPAddress", ""))
            target_proxy = str(fr.get("target", "")).rsplit("/", 1)[-1]
            fr_doc_id = f"{self.env_id}__lb_frontend.{fr_name}"

            services.append(
                DiscoveredServiceDoc(
                    doc_id=fr_doc_id,
                    env_id=self.env_id,
                    name=fr_name,
                    kind="lb_frontend",
                    adapter_family="gcp",
                    locator=f"http://{ip_address}" if ip_address else fr_name,
                    labels={"ip": ip_address, "target_proxy": target_proxy},
                    classification="other_read_only",
                    first_seen=now_iso,
                    last_seen=now_iso,
                    status="active",
                )
            )

        # Parse URL map routing edges: frontend -> backend services
        for um in url_maps:
            um_name = str(um.get("name", ""))
            default_bs = str(um.get("defaultService", "")).rsplit("/", 1)[-1]
            frontend_id = f"{self.env_id}__lb_frontend.{um_name}"

            all_target_bs: set[str] = set()
            if default_bs:
                all_target_bs.add(default_bs)

            for host_rule in um.get("hostRules", []):
                pm_name = host_rule.get("pathMatcher")
                for pm in um.get("pathMatchers", []):
                    if pm.get("name") == pm_name:
                        d_svc = str(pm.get("defaultService", "")).rsplit("/", 1)[-1]
                        if d_svc:
                            all_target_bs.add(d_svc)
                        for pr in pm.get("pathRules", []):
                            p_svc = str(pr.get("service", "")).rsplit("/", 1)[-1]
                            if p_svc:
                                all_target_bs.add(p_svc)

            for target_bs in all_target_bs:
                dst_id = f"{self.env_id}__backend_service.{target_bs}"
                edges.append(
                    DiscoveredEdgeDoc(
                        doc_id=f"{frontend_id}__to__{dst_id}",
                        src=frontend_id,
                        dst=dst_id,
                        protocol="lb_route",
                        evidence="lb_route",
                        evidence_kind="lb_route",
                        confidence=1.0,
                        first_seen=now_iso,
                        last_seen=now_iso,
                    )
                )

        return DiscoveryResult(
            services=services,
            edges=edges,
            entities=[],
            evidence={
                "forwarding_rules_count": len(forwarding_rules),
                "url_maps_count": len(url_maps),
                "backend_services_count": len(backend_services),
                "security_policies_count": len(security_policies),
            },
        )

    async def _execute_capability_impl(
        self,
        capability_id: str,
        args: dict[str, Any],
        trust_decision: TrustDecision | None = None,
    ) -> Any:
        if capability_id == "edge.describe_backends":
            backends = await self._fetch_backend_services()
            return backends
        if capability_id == "edge.describe_security_policies":
            policies = await self._fetch_security_policies()
            return policies
        raise NotImplementedError(capability_id)
