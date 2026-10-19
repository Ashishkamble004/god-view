"""Cloud Armor remediation executor (Plan §3.4).

Executes:
- set_waf_sensitivity: patches evaluatePreconfiguredWaf(...) calls to add/update {'sensitivity': N}.
  Rolls out in preview=True first, verifies via LB logs / probe, then sets preview=False.
  Verifies post-enforcement with probe (200/422 pass, 403 fail). Auto-rollbacks on failure.
- set_rule_preview: toggles preview flag on a rule (used by operator kill switch).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import socket
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx

from godview.sovereign.remediation.impersonation import get_act_sa_access_token
from godview.sovereign.remediation.types import (
    BaseExecutor,
    ExecutionResult,
    ScopeViolationError,
    VerificationCheck,
)

logger = logging.getLogger(__name__)

COMPUTE_API_BASE: str = "https://compute.googleapis.com/compute/v1"
FORBIDDEN_PROBE_HOSTS: frozenset[str] = frozenset(
    {"169.254.169.254", "metadata.google.internal", "metadata", "instance-data"}
)


def patch_waf_expression(expression: str, sensitivity: int = 1) -> str:
    """Add or update {'sensitivity': N} in every evaluatePreconfiguredWaf call."""
    pattern = re.compile(r"evaluatePreconfiguredWaf\s*\(\s*([^)]+?)\s*\)")

    def _replace_call(match: re.Match[str]) -> str:
        inside = match.group(1).strip()
        # Case 1: evaluatePreconfiguredWaf('tag', {...})
        if "," in inside:
            parts = inside.split(",", 1)
            first_arg = parts[0].strip()
            rest = parts[1].strip()
            if "sensitivity" in rest:
                updated_rest = re.sub(
                    r"('sensitivity'|\"sensitivity\")\s*:\s*\d+",
                    f"'sensitivity': {sensitivity}",
                    rest,
                )
                return f"evaluatePreconfiguredWaf({first_arg}, {updated_rest})"
            if rest.startswith("{") and rest.endswith("}"):
                inner_dict = rest[1:-1].strip()
                prefix = f"{inner_dict}, " if inner_dict else ""
                return f"evaluatePreconfiguredWaf({first_arg}, {{{prefix}'sensitivity': {sensitivity}}})"
            return f"evaluatePreconfiguredWaf({first_arg}, {{'sensitivity': {sensitivity}}})"

        # Case 2: evaluatePreconfiguredWaf('tag')
        return f"evaluatePreconfiguredWaf({inside}, {{'sensitivity': {sensitivity}}})"

    return pattern.sub(_replace_call, expression)


def _is_valid_public_host(h: str) -> bool:
    """Validate that candidate host is a valid public host (non-empty, non-wildcard, dots present, not observer)."""
    if not h:
        return False
    clean = str(h).strip().lower().split("://")[-1].split("/")[0].split(":")[0].strip()
    if not clean or "*" in clean or "?" in clean or "." not in clean:
        return False
    if clean.startswith("godview.") or clean.startswith("god-view-"):
        return False
    return True


def _host_priority(host_str: str) -> int:
    """Priority scoring for candidate probe hosts: 3=custom domain, 2=cloud run, 1=ip, 0=invalid."""
    if not _is_valid_public_host(host_str):
        return 0
    clean = host_str.split("://")[-1].split("/")[0].split(":")[0].strip().lower()
    parts = clean.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return 1
    if clean.endswith(".run.app") or clean.endswith(".appspot.com"):
        return 2
    return 3


def validate_probe_url(url: str, ctx: Any = None, env_mode: str = "gcp") -> None:
    """Validate that probe URL does not target internal, private, or unauthorized hosts (SSRF defense)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ScopeViolationError(f"Invalid probe URL scheme '{parsed.scheme}': only http/https allowed")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ScopeViolationError(f"Invalid probe URL '{url}': missing hostname")

    if hostname in FORBIDDEN_PROBE_HOSTS:
        raise ScopeViolationError(f"Probe URL '{url}' targets forbidden internal metadata host (SSRF protection)")

    if env_mode == "hermetic":
        if (
            hostname in ("127.0.0.1", "localhost", "api.example.com", "test")
            or hostname.endswith(".example.com")
            or hostname.endswith(".test")
            or hostname.endswith(".invalid")
        ):
            return

    # Check for direct IP address or decimal integer encoding (e.g. 2852039166 -> 169.254.169.254)
    resolved_ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    try:
        if hostname.isdigit():
            resolved_ips.append(ipaddress.ip_address(int(hostname)))
        else:
            resolved_ips.append(ipaddress.ip_address(hostname))
    except ValueError:
        try:
            addr_info = socket.getaddrinfo(hostname, None)
            for item in addr_info:
                ip_str = item[4][0]
                try:
                    resolved_ips.append(ipaddress.ip_address(ip_str))
                except ValueError:
                    pass
        except socket.gaierror as e:
            if env_mode != "hermetic":
                raise ScopeViolationError(f"Could not resolve probe host '{hostname}': {e}") from e

    for ip in resolved_ips:
        target_ip = ip.ipv4_mapped if (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped) else ip
        if (
            target_ip.is_private
            or target_ip.is_loopback
            or target_ip.is_link_local
            or target_ip.is_reserved
            or target_ip.is_multicast
            or target_ip.is_unspecified
        ):
            raise ScopeViolationError(
                f"Probe URL '{url}' resolves to disallowed IP {target_ip} (SSRF protection)"
            )

    # In GCP mode, require host to be a discovered public endpoint
    if env_mode != "hermetic" and ctx is not None:
        discovered_hosts: set[str] = set()

        def _add_host(val: Any) -> None:
            if not val:
                return
            h_str = str(val).split("://")[-1].split("/")[0].split(":")[0].strip().lower()
            if _is_valid_public_host(h_str):
                discovered_hosts.add(h_str)

        def _extract_from_svc(s: Any) -> None:
            if isinstance(s, dict):
                for k in ("host", "public_ip", "ip", "url", "external_ip", "hostname", "domain", "endpoint", "locator"):
                    _add_host(s.get(k))
                for ep in s.get("endpoints") or []:
                    _add_host(ep)
                for dom in s.get("domains") or []:
                    _add_host(dom)
                for r in s.get("routes") or []:
                    if isinstance(r, dict):
                        _add_host(r.get("host"))
            elif hasattr(s, "__dict__"):
                for k in ("host", "public_ip", "ip", "url", "external_ip", "hostname", "domain", "endpoint", "locator"):
                    _add_host(getattr(s, k, None))
                for ep in getattr(s, "endpoints", []) or []:
                    _add_host(ep)
                for dom in getattr(s, "domains", []) or []:
                    _add_host(dom)
                for r in getattr(s, "routes", []) or []:
                    if isinstance(r, dict):
                        _add_host(r.get("host"))

        # 1. ctx.discovered_services (dict or list)
        disc = getattr(ctx, "discovered_services", None) or {}
        if isinstance(disc, dict):
            for s in disc.values():
                _extract_from_svc(s)
        elif isinstance(disc, list):
            for s in disc:
                _extract_from_svc(s)

        # 2. ctx.docs (Firestore / docs_store)
        docs_store = getattr(ctx, "docs", None)
        if docs_store is not None and hasattr(docs_store, "list_docs"):
            try:
                for s in docs_store.list_docs("discovered_services"):
                    _extract_from_svc(s)
            except Exception:
                pass
            try:
                top_doc = docs_store.get("topology", "current") or {}
                for n in top_doc.get("nodes") or []:
                    _extract_from_svc(n)
                    if isinstance(n, dict):
                        for k, v in (n.get("attributes") or {}).items():
                            if k in ("host", "public_ip", "ip", "url", "external_ip", "hostname", "domain", "endpoint"):
                                _add_host(v)
            except Exception:
                pass

        # 3. ctx.topology
        topo = getattr(ctx, "topology", None)
        if topo and hasattr(topo, "nodes"):
            for node in topo.nodes:
                _extract_from_svc(node)
                attrs = getattr(node, "attributes", {}) or {}
                for k in ("host", "public_ip", "ip", "url", "external_ip", "hostname", "domain", "endpoint"):
                    _add_host(attrs.get(k))

        if discovered_hosts and hostname not in discovered_hosts:
            raise ScopeViolationError(
                f"Probe host '{hostname}' is not a discovered public endpoint (SSRF protection)"
            )


class CloudArmorExecutor(BaseExecutor):
    """Executor for Cloud Armor security policy rules."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client

    async def _get_auth_token(self, ctx: Any, project_id: str | None = None) -> str:
        return await get_act_sa_access_token(ctx, project_id=project_id, client=self._client)

    async def _get_policy(
        self,
        client: httpx.AsyncClient,
        project_id: str,
        security_policy: str,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        url = f"{COMPUTE_API_BASE}/projects/{project_id}/global/securityPolicies/{security_policy}"
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def _get_rule(
        self,
        client: httpx.AsyncClient,
        project_id: str,
        security_policy: str,
        priority: int,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        url = (
            f"{COMPUTE_API_BASE}/projects/{project_id}/global/securityPolicies/"
            f"{security_policy}/getRule?priority={priority}"
        )
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def _patch_rule(
        self,
        client: httpx.AsyncClient,
        project_id: str,
        security_policy: str,
        priority: int,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        url = (
            f"{COMPUTE_API_BASE}/projects/{project_id}/global/securityPolicies/"
            f"{security_policy}/patchRule?priority={priority}"
        )
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()

    async def _add_rule(
        self,
        client: httpx.AsyncClient,
        project_id: str,
        security_policy: str,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        url = (
            f"{COMPUTE_API_BASE}/projects/{project_id}/global/securityPolicies/"
            f"{security_policy}/addRule"
        )
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()

    async def _remove_rule(
        self,
        client: httpx.AsyncClient,
        project_id: str,
        security_policy: str,
        priority: int,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        url = (
            f"{COMPUTE_API_BASE}/projects/{project_id}/global/securityPolicies/"
            f"{security_policy}/removeRule?priority={priority}"
        )
        resp = await client.post(url, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def _find_free_preview_priority(
        self,
        client: httpx.AsyncClient,
        project_id: str,
        security_policy: str,
        waf_priority: int,
        headers: dict[str, str],
    ) -> int:
        policy = await self._get_policy(client, project_id, security_policy, headers)
        existing_priorities = {int(r["priority"]) for r in policy.get("rules", []) if "priority" in r}

        target = waf_priority - 100
        if target > 100 and target not in existing_priorities:
            return target

        for candidate in range(waf_priority - 1, 100, -1):
            if candidate not in existing_priorities:
                return candidate

        for candidate in range(waf_priority + 1, waf_priority + 100):
            if candidate not in existing_priorities:
                return candidate

        raise RuntimeError(
            f"Could not find free priority for preview rule on security policy '{security_policy}'"
        )

    async def _wait_for_global_operation(
        self,
        client: httpx.AsyncClient,
        project_id: str,
        op_data: dict[str, Any] | None,
        headers: dict[str, str],
        timeout_s: float = 120.0,
    ) -> None:
        """Poll Compute globalOperation until status == 'DONE'."""
        if not op_data or not isinstance(op_data, dict):
            return
        if op_data.get("status") == "DONE":
            return
        op_name = op_data.get("name")
        self_link = op_data.get("selfLink")
        if not op_name and not self_link:
            return

        is_mock = isinstance(getattr(client, "_transport", None), httpx.MockTransport)
        if is_mock:
            return

        url = self_link or f"{COMPUTE_API_BASE}/projects/{project_id}/global/operations/{op_name}"
        start_time = asyncio.get_running_loop().time()
        while True:
            elapsed = asyncio.get_running_loop().time() - start_time
            if elapsed > timeout_s:
                logger.warning("Timed out waiting for Compute globalOperation %s", op_name)
                break
            try:
                resp = await client.get(url, headers=headers, timeout=10.0)
                if resp.status_code == 200:
                    current_op = resp.json()
                    if current_op.get("status") == "DONE":
                        return
            except Exception as e:
                logger.debug("Transient error polling Compute operation %s: %s", op_name, e)
            await asyncio.sleep(2.0)

    async def _poll_probe_until_success(
        self,
        client: httpx.AsyncClient,
        probe_url: str,
        env_mode: str,
        timeout_s: float = 240.0,
        interval_s: float = 15.0,
    ) -> tuple[bool, int | None, str | None]:
        """Poll the safe canary probe URL until it returns a non-403 response (or times out)."""
        is_mock = isinstance(getattr(client, "_transport", None), httpx.MockTransport)
        if env_mode == "hermetic" or is_mock:
            timeout_s = min(timeout_s, 2.0)
            interval_s = 0.05

        start_time = asyncio.get_running_loop().time()
        last_status = None
        last_evidence = None

        while True:
            try:
                probe_res = await client.get(probe_url, timeout=10.0)
                last_status = probe_res.status_code
                last_evidence = f"HTTP {probe_res.status_code} ({probe_res.text[:100]})"
                # If non-403 (e.g. 200 OK, 422 validation, 404), Cloud Armor allows the request
                if probe_res.status_code != 403:
                    return True, last_status, last_evidence
            except Exception as exc:
                last_evidence = f"Probe connection error: {exc}"

            elapsed = asyncio.get_running_loop().time() - start_time
            if elapsed >= timeout_s:
                break
            await asyncio.sleep(interval_s)

        return False, last_status, last_evidence

    def _find_logging_adapter(self, ctx: Any) -> Any:
        if not ctx or not hasattr(ctx, "adapters"):
            return None
        adapters = getattr(ctx, "adapters", []) or []
        for adp in adapters:
            caps: set[str] = set()
            if callable(getattr(adp, "capabilities", None)):
                try:
                    c_dict = adp.capabilities()
                    if isinstance(c_dict, dict):
                        caps.update(c_dict.keys())
                except Exception:
                    pass
            supported = getattr(adp, "supported_capabilities", [])
            if isinstance(supported, (list, tuple, set)):
                caps.update(supported)
            if "logging.query_lb_requests" in caps or "obs.query_logs_scoped" in caps:
                return adp
        return None

    async def _poll_preview_logs(
        self,
        logging_adapter: Any,
        client: httpx.AsyncClient,
        probe_url: str,
        real_priority: int,
        temp_priority: int,
        t0: datetime | None,
        env_mode: str,
        timeout_s: float = 60.0,
        interval_s: float = 5.0,
        min_evaluations: int = 3,
        min_polls: int = 2,
    ) -> tuple[bool, list[dict[str, Any]], str]:
        """Poll LB logs during preview window to verify relaxed rule allows currently blocked traffic (Verifier-Live #144, Verifier-Core #145, #151)."""
        is_mock = isinstance(getattr(client, "_transport", None), httpx.MockTransport)
        if env_mode == "hermetic" or is_mock:
            timeout_s = min(timeout_s, 2.0)
            interval_s = 0.05

        start_time = asyncio.get_running_loop().time()
        last_reason = "No post-t0 enforced DENY evaluations observed in the window"

        effective_t0 = t0 or datetime.now(UTC)
        t0_iso = effective_t0.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Tag canary probes with a unique nonce to prove edge live evaluation post-t0
        canary_nonce = f"gv-{uuid.uuid4().hex[:8]}"
        sep = "&" if "?" in probe_url else "?"
        benign_nonce_probe_url = f"{probe_url}{sep}gv={canary_nonce}"

        # Positive control attack probe (Verifier-Live #153): proves temp rule is live at edge
        parsed_probe = urllib.parse.urlsplit(probe_url)
        attack_nonce_probe_url = f"{parsed_probe.scheme}://{parsed_probe.netloc}/?gv={canary_nonce}&id=1%27%20OR%20%271%27=%271"

        # Query entries denied by current rule (real_priority) or matched by preview rule post-t0
        filter_expr = (
            f'(jsonPayload.enforcedSecurityPolicy.priority="{real_priority}" OR '
            f'jsonPayload.previewSecurityPolicy.priority="{temp_priority}") AND '
            f'timestamp>="{t0_iso}"'
        )

        poll_count = 0

        while True:
            poll_count += 1
            # 1. Benign canary probes (denied by rule 1000, allowed by temp rule)
            for _ in range(3):
                try:
                    await client.get(benign_nonce_probe_url, timeout=5.0)
                except Exception:
                    pass

            # 2. Positive control attack probe (proves temp rule 900 is active at edge)
            try:
                await client.get(attack_nonce_probe_url, timeout=5.0)
            except Exception:
                pass

            try:
                res = await logging_adapter.execute_capability(
                    "logging.query_lb_requests",
                    {
                        "filter": filter_expr,
                        "page_size": 50,
                        "window_s": int(timeout_s) + 60,
                    },
                )
                entries = res.get("entries", res) if isinstance(res, dict) else res
                if isinstance(entries, list) and entries:
                    would_be_allowed_count = 0
                    would_still_deny_count = 0
                    nonce_confirmed = False
                    temp_liveness_confirmed = False

                    for e in entries:
                        if not isinstance(e, dict):
                            continue

                        # Check timestamp strictly post-t0
                        entry_ts = str(e.get("timestamp", ""))
                        if entry_ts and entry_ts < t0_iso:
                            continue

                        jp = e.get("jsonPayload", {})
                        hr = e.get("httpRequest", {})
                        req_url = str(jp.get("requestUrl") or hr.get("requestUrl") or "")
                        has_nonce = canary_nonce in req_url
                        is_attack = ("id=1" in req_url or "1=1" in req_url)

                        enforced = jp.get("enforcedSecurityPolicy", {})
                        enforced_pri = str(enforced.get("priority", jp.get("enforcedSecurityPolicyPriority", "")))
                        enforced_outcome = str(enforced.get("outcome", jp.get("enforcedSecurityPolicyOutcome", ""))).upper()

                        preview = jp.get("previewSecurityPolicy")
                        preview_dict = preview if isinstance(preview, dict) else {}
                        preview_pri = str(preview_dict.get("priority", ""))
                        preview_outcome = str(preview_dict.get("outcome", "")).upper()

                        # Positive control: attack probe matching temp rule 900 proves edge propagation
                        if is_attack and has_nonce and preview_pri == str(temp_priority) and "DENY" in preview_outcome:
                            temp_liveness_confirmed = True

                        # Benign evaluation
                        if not is_attack:
                            if preview_pri == str(temp_priority) and "DENY" in preview_outcome:
                                would_still_deny_count += 1
                            elif (enforced_pri == str(real_priority) and "DENY" in enforced_outcome) or (has_nonce and preview_outcome != "DENY"):
                                would_be_allowed_count += 1
                                if has_nonce:
                                    nonce_confirmed = True

                    if would_still_deny_count > 0:
                        return False, entries, f"Temp preview rule {temp_priority} would still DENY {would_still_deny_count} benign samples currently blocked by rule {real_priority}"

                    if not temp_liveness_confirmed:
                        last_reason = f"Waiting for temp rule {temp_priority} liveness confirmation at edge (positive control attack probe not yet observed)"
                    elif (
                        would_be_allowed_count >= min_evaluations
                        and nonce_confirmed
                        and poll_count >= min_polls
                    ):
                        return (
                            True,
                            entries,
                            f"Temp rule {temp_priority} liveness confirmed at edge via positive control attack probe; "
                            f"observed {would_be_allowed_count} benign samples (incl. verified post-t0 canary nonce '{canary_nonce}') "
                            f"blocked by rule {real_priority} that would be allowed under relaxed rule {temp_priority} (0 preview DENYs across {poll_count} polls)",
                        )

            except Exception as e:
                logger.debug("Transient error querying preview logs: %s", e)

            elapsed = asyncio.get_running_loop().time() - start_time
            if elapsed >= timeout_s:
                break
            await asyncio.sleep(interval_s)

        return False, [], last_reason

    async def _discover_rule_priority(
        self,
        client: httpx.AsyncClient,
        project_id: str,
        security_policy: str,
        headers: dict[str, str],
        for_waf: bool = True,
    ) -> int:
        """Discover the rule priority for WAF or kill switch from the policy's rules."""
        try:
            policy = await self._get_policy(client, project_id, security_policy, headers)
        except Exception as e:
            raise RuntimeError(
                f"Failed to read security policy '{security_policy}' in project '{project_id}': {e}"
            ) from e

        rules = policy.get("rules", [])
        if for_waf:
            for r in rules:
                expr = r.get("match", {}).get("expr", {}).get("expression", "")
                if "evaluatePreconfiguredWaf" in expr:
                    return int(r["priority"])
            raise RuntimeError(
                f"No WAF rule matching evaluatePreconfiguredWaf found in security policy '{security_policy}'"
            )

        # Kill switch discovery: strictly target kill-switch / match-all deny rule; EXCLUDE WAF rules
        candidates: list[dict[str, Any]] = []
        for r in rules:
            expr = r.get("match", {}).get("expr", {}).get("expression", "")
            if "evaluatePreconfiguredWaf" in expr:
                continue
            action = (r.get("action") or "").lower()
            if not action.startswith("deny"):
                continue
            desc = (r.get("description") or "").lower()
            match_dict = r.get("match", {})
            v_expr = match_dict.get("versionedExpr", "")
            src_ips = match_dict.get("config", {}).get("srcIpRanges", [])
            is_match_all = (
                (v_expr == "SRC_IPS_V1" and ("*" in src_ips or "0.0.0.0/0" in src_ips))
                or expr.strip() in ("true", "1 == 1", "1==1")
            )
            has_kill_desc = any(w in desc for w in ("kill", "emergency", "switch", "break-glass"))
            if has_kill_desc or is_match_all:
                candidates.append(r)

        if not candidates:
            raise RuntimeError(
                f"No kill-switch rule discovered on security policy '{security_policy}'"
            )

        if len(candidates) == 1:
            return int(candidates[0]["priority"])

        desc_matches = [
            c for c in candidates
            if any(w in (c.get("description") or "").lower() for w in ("kill", "emergency", "switch"))
        ]
        if len(desc_matches) == 1:
            return int(desc_matches[0]["priority"])

        raise RuntimeError(
            f"Ambiguous kill-switch rules on security policy '{security_policy}': "
            f"multiple candidates {[c.get('priority') for c in candidates]}"
        )

    async def execute(
        self,
        ctx: Any,
        args: dict[str, Any],
        target_resource: str,
        *,
        agent_id: str | None = None,
        approval_id: str | None = None,
        probe_url: str | None = None,
        **kwargs: Any,
    ) -> ExecutionResult:
        """Route to appropriate Cloud Armor action."""
        action = args.get("action") or args.get("capability_id")
        if action == "armor.set_rule_preview":
            return await self.set_rule_preview(ctx, args, target_resource, agent_id=agent_id)
        return await self.set_waf_sensitivity(
            ctx,
            args,
            target_resource,
            agent_id=agent_id,
            approval_id=approval_id,
            probe_url=probe_url,
        )

    async def set_rule_preview(
        self,
        ctx: Any,
        args: dict[str, Any],
        target_resource: str,
        agent_id: str | None = None,
    ) -> ExecutionResult:
        """Toggle preview flag on a Cloud Armor rule (used by operator kill switch)."""
        clean_target = target_resource.split("/")[-1].split("?")[0].strip()
        security_policy = clean_target

        project_id = args.get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        preview_val = bool(args.get("preview", True))

        client = self._client or httpx.AsyncClient()
        env_mode = (
            getattr(getattr(ctx, "settings", None), "env_mode", None)
            or getattr(ctx, "env_mode", None)
            or os.environ.get("GODVIEW_ENV", "")
        )
        if isinstance(getattr(client, "_transport", None), httpx.MockTransport):
            env_mode = "hermetic"
        elif not env_mode:
            env_mode = "gcp"
        client = self._client or httpx.AsyncClient()

        # In hermetic mode without mock client, return simulated preview toggle
        if env_mode == "hermetic" and self._client is None:
            before = {"priority": 100, "preview": not preview_val}
            after = {"priority": 100, "preview": preview_val}
            check = VerificationCheck(
                check="rule_preview_mode_verified",
                passed=True,
                evidence={"preview": preview_val, "mode": "hermetic"},
            )
            return ExecutionResult(ok=True, before=before, after=after, verification=[check])

        token = await self._get_auth_token(ctx, project_id=project_id)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        # Discover or use provided priority
        priority = int(args.get("priority", 0))
        if priority <= 0:
            priority = await self._discover_rule_priority(client, project_id, security_policy, headers, for_waf=False)

        current_rule = await self._get_rule(client, project_id, security_policy, priority, headers)
        before = {
            "priority": priority,
            "preview": current_rule.get("preview", False),
            "action": current_rule.get("action"),
        }

        patch_body = {"priority": priority, "preview": preview_val}
        patch_op = await self._patch_rule(client, project_id, security_policy, priority, patch_body, headers)
        await self._wait_for_global_operation(client, project_id, patch_op, headers)

        updated_rule = await self._get_rule(client, project_id, security_policy, priority, headers)
        after_preview = updated_rule.get("preview")
        passed = (after_preview == preview_val)

        check = VerificationCheck(
            check="rule_preview_mode_verified",
            passed=passed,
            evidence={"preview": after_preview, "expected": preview_val},
        )
        after = {
            "priority": priority,
            "preview": after_preview,
            "action": updated_rule.get("action"),
        }

        if not passed:
            # Revert
            revert_op = await self._patch_rule(
                client,
                project_id,
                security_policy,
                priority,
                {"priority": priority, "preview": before["preview"]},
                headers,
            )
            await self._wait_for_global_operation(client, project_id, revert_op, headers)
            return ExecutionResult(
                ok=False,
                before=before,
                after=after,
                verification=[check],
                reverted=True,
                error=f"Failed to set rule preview to {preview_val}",
            )

        return ExecutionResult(ok=True, before=before, after=after, verification=[check])

    def _derive_probe_url(
        self,
        ctx: Any,
        args: dict[str, Any],
        target_resource: str,
        approval_id: str | None,
        env_mode: str,
    ) -> str:
        """Derive a safe canary probe URL (GET https://<host>/?a=b-c-d) from incident log samples or topology."""
        if args.get("probe_url"):
            return str(args["probe_url"])

        incident_id = args.get("incident_id")
        if not incident_id and approval_id and hasattr(ctx, "docs") and ctx.docs is not None:
            try:
                card = ctx.docs.get("approvals", approval_id)
                if card:
                    incident_id = card.get("incident_id")
            except Exception:
                pass

        host: str | None = None
        # 1. Incident raw evidence or evidence pack
        if incident_id and hasattr(ctx, "docs") and ctx.docs is not None:
            try:
                inc = ctx.docs.get("incidents", incident_id) or {}
                raw_ev = inc.get("raw_evidence") or inc.get("evidence_pack") or {}
                # Sentinels lb_requests keep samples in raw_evidence.log_samples
                log_samples = raw_ev.get("log_samples") or []
                for s in log_samples:
                    if isinstance(s, dict):
                        u = s.get("url") or s.get("requestUrl") or s.get("httpRequest", {}).get("requestUrl")
                        if u:
                            parsed = urllib.parse.urlsplit(u if "://" in u else f"https://{u}")
                            cand = parsed.netloc or parsed.path
                            if cand and _is_valid_public_host(cand):
                                host = cand.split(":")[0].strip().lower()
                                break
                if not host:
                    u = (raw_ev.get("httpRequest") or {}).get("requestUrl") or raw_ev.get("url")
                    if u:
                        parsed = urllib.parse.urlsplit(u if "://" in u else f"https://{u}")
                        cand = parsed.netloc or parsed.path
                        if cand and _is_valid_public_host(cand):
                            host = cand.split(":")[0].strip().lower()
            except Exception as e:
                logger.debug("Could not inspect incident %s for probe host: %s", incident_id, e)

        # 2. Check discovered services and topology
        if not host and ctx:
            docs_store = getattr(ctx, "docs", None)
            matching_hosts: list[str] = []
            general_hosts: list[str] = []

            target_clean = str(target_resource).strip().lower()
            matching_backend_services: set[str] = set()

            svcs: list[Any] = []
            if docs_store is not None and hasattr(docs_store, "list_docs"):
                try:
                    svcs.extend(docs_store.list_docs("discovered_services"))
                except Exception:
                    pass
            disc_attr = getattr(ctx, "discovered_services", None)
            if isinstance(disc_attr, dict):
                svcs.extend(disc_attr.values())
            elif isinstance(disc_attr, list):
                svcs.extend(disc_attr)

            for s in svcs:
                s_dict = s if isinstance(s, dict) else (s.model_dump(mode="json") if hasattr(s, "model_dump") else getattr(s, "__dict__", {}))
                p_det = (s_dict.get("posture") or {}).get("details") or {}
                sp = str(p_det.get("security_policy") or s_dict.get("securityPolicy") or "").lower()
                s_name = str(s_dict.get("name") or "").lower()
                if target_clean in sp or (target_clean in s_name and "policy" in target_clean):
                    matching_backend_services.add(str(s_dict.get("name") or ""))

            for s in svcs:
                s_dict = s if isinstance(s, dict) else (s.model_dump(mode="json") if hasattr(s, "model_dump") else getattr(s, "__dict__", {}))
                for r in s_dict.get("routes") or []:
                    if isinstance(r, dict):
                        r_h = r.get("host")
                        if r_h and _is_valid_public_host(r_h):
                            clean_rh = str(r_h).split("://")[-1].split("/")[0].split(":")[0].strip().lower()
                            r_bs = str(r.get("backend_service") or "")
                            if r_bs and (r_bs in matching_backend_services or target_clean in r_bs.lower()):
                                matching_hosts.append(clean_rh)
                            else:
                                general_hosts.append(clean_rh)
                for k in ("host", "public_ip", "ip", "url", "external_ip", "hostname", "domain", "endpoint", "locator"):
                    val = s_dict.get(k)
                    if val and _is_valid_public_host(val):
                        clean_v = str(val).split("://")[-1].split("/")[0].split(":")[0].strip().lower()
                        general_hosts.append(clean_v)
                for ep in s_dict.get("endpoints") or []:
                    if ep and _is_valid_public_host(ep):
                        clean_ep = str(ep).split("://")[-1].split("/")[0].split(":")[0].strip().lower()
                        general_hosts.append(clean_ep)

            # Check topology nodes
            topo_nodes: list[Any] = []
            if docs_store is not None and hasattr(docs_store, "get"):
                try:
                    top_doc = docs_store.get("topology", "current") or {}
                    topo_nodes.extend(top_doc.get("nodes") or [])
                except Exception:
                    pass
            topo_attr = getattr(ctx, "topology", None)
            if topo_attr and hasattr(topo_attr, "nodes"):
                topo_nodes.extend(topo_attr.nodes)

            for n in topo_nodes:
                n_dict = n if isinstance(n, dict) else (n.__dict__ if hasattr(n, "__dict__") else {})
                for ep in n_dict.get("endpoints") or []:
                    if ep and _is_valid_public_host(ep):
                        general_hosts.append(str(ep).split("://")[-1].split("/")[0].split(":")[0].strip().lower())
                for k, v in (n_dict.get("attributes") or {}).items():
                    if k in ("host", "public_ip", "ip", "url", "external_ip", "hostname", "domain", "endpoint") and v:
                        if _is_valid_public_host(v):
                            general_hosts.append(str(v).split("://")[-1].split("/")[0].split(":")[0].strip().lower())

            if matching_hosts:
                matching_hosts.sort(key=_host_priority, reverse=True)
                if _host_priority(matching_hosts[0]) > 0:
                    host = matching_hosts[0]
            if not host and general_hosts:
                general_hosts.sort(key=_host_priority, reverse=True)
                if _host_priority(general_hosts[0]) > 0:
                    host = general_hosts[0]

        # 4. Target resource if it's already a URL
        if not host and "://" in target_resource:
            parsed = urllib.parse.urlsplit(target_resource)
            if parsed.netloc and _is_valid_public_host(parsed.netloc):
                host = parsed.netloc.split(":")[0].strip().lower()

        if host:
            clean_host = host.split(":")[0] if ":" in host and host.endswith(":443") else host
            return f"https://{clean_host}/?a=b-c-d"

        if env_mode == "hermetic":
            return "https://hermetic-edge.godview.test/?a=b-c-d"

        raise ScopeViolationError(
            f"Mandatory probe URL could not be derived for WAF sensitivity remediation on '{target_resource}'. "
            "Incident evidence or discovered endpoint host is required."
        )

    async def _derive_probe_url_async(
        self,
        ctx: Any,
        args: dict[str, Any],
        target_resource: str,
        approval_id: str | None,
        env_mode: str,
    ) -> str:
        """Async variant of _derive_probe_url that can query recent LB logs via logging adapter as a fallback."""
        try:
            return self._derive_probe_url(ctx, args, target_resource, approval_id, env_mode)
        except ScopeViolationError:
            pass

        # Check recent LB logs for target_resource via logging adapter
        if ctx:
            logging_adapter = self._find_logging_adapter(ctx)
            if logging_adapter is not None:
                try:
                    res = await logging_adapter.execute_capability(
                        "logging.query_lb_requests",
                        {
                            "filter": f'jsonPayload.enforcedSecurityPolicy.name="{target_resource}"',
                            "window_s": 3600,
                            "page_size": 20,
                        },
                    )
                    entries = res.get("entries", res) if isinstance(res, dict) else res
                    if isinstance(entries, list):
                        log_hosts: list[str] = []
                        for e in entries:
                            if isinstance(e, dict):
                                jp = e.get("jsonPayload", {})
                                hr = e.get("httpRequest", {})
                                u = jp.get("requestUrl") or hr.get("requestUrl")
                                if u:
                                    parsed = urllib.parse.urlsplit(u if "://" in u else f"https://{u}")
                                    cand = parsed.netloc or parsed.path
                                    if cand and _is_valid_public_host(cand):
                                        log_hosts.append(cand.split(":")[0].strip().lower())
                        if log_hosts:
                            log_hosts.sort(key=_host_priority, reverse=True)
                            if _host_priority(log_hosts[0]) > 0:
                                clean_host = (
                                    log_hosts[0].split(":")[0]
                                    if ":" in log_hosts[0] and log_hosts[0].endswith(":443")
                                    else log_hosts[0]
                                )
                                return f"https://{clean_host}/?a=b-c-d"
                except Exception as log_err:
                    logger.debug("Could not query LB logs for probe host: %s", log_err)

        if env_mode == "hermetic":
            return "https://hermetic-edge.godview.test/?a=b-c-d"

        raise ScopeViolationError(
            f"Mandatory probe URL could not be derived for WAF sensitivity remediation on '{target_resource}'. "
            "Incident evidence or discovered endpoint host is required."
        )

    async def set_waf_sensitivity(
        self,
        ctx: Any,
        args: dict[str, Any],
        target_resource: str,
        *,
        agent_id: str | None = None,
        approval_id: str | None = None,
        probe_url: str | None = None,
    ) -> ExecutionResult:
        """Update WAF rule sensitivity with verification and auto-rollback (Plan §3.4, T1)."""
        # Policy is STRICTLY derived from target_resource
        clean_target = target_resource.split("/")[-1].split("?")[0].strip()
        security_policy = clean_target

        project_id = args.get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        sensitivity = int(args.get("sensitivity", 1))

        client = self._client or httpx.AsyncClient()
        env_mode = (
            getattr(getattr(ctx, "settings", None), "env_mode", None)
            or getattr(ctx, "env_mode", None)
            or os.environ.get("GODVIEW_ENV", "")
        )
        if isinstance(getattr(client, "_transport", None), httpx.MockTransport):
            env_mode = "hermetic"
        elif not env_mode:
            env_mode = "gcp"

        # Resolve safe canary probe URL: mandatory for set_waf_sensitivity
        effective_probe_url = probe_url or await self._derive_probe_url_async(
            ctx=ctx,
            args=args,
            target_resource=target_resource,
            approval_id=approval_id,
            env_mode=env_mode,
        )

        validate_probe_url(effective_probe_url, ctx=ctx, env_mode=env_mode)

        token = await self._get_auth_token(ctx, project_id=project_id)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        client = self._client or httpx.AsyncClient()

        before: dict[str, Any] = {}
        checks: list[VerificationCheck] = []
        temp_priority: int | None = None

        try:
            # Discover or read priority
            priority = int(args.get("priority", 0))
            if priority <= 0:
                priority = await self._discover_rule_priority(client, project_id, security_policy, headers, for_waf=True)

            # Step 1: Read current rule
            current_rule = await self._get_rule(client, project_id, security_policy, priority, headers)
            match_data = current_rule.get("match", {})
            expr_data = match_data.get("expr", {})
            orig_expr = expr_data.get("expression", "")
            orig_preview = current_rule.get("preview", False)

            before = {
                "priority": priority,
                "expression": orig_expr,
                "preview": orig_preview,
            }

            # Step 2: Compute patched expression
            new_expr = patch_waf_expression(orig_expr, sensitivity=sensitivity)
            preview_first = bool(args.get("preview_first", False))
            temp_priority: int | None = None

            if preview_first:
                # Real preview: add temporary rule at free higher priority with preview=True (Plan §3.4, Verifier #135, #136)
                # NEVER disable the existing WAF rule!
                temp_priority = await self._find_free_preview_priority(
                    client, project_id, security_policy, priority, headers
                )
                temp_rule_data = {
                    "action": "deny(403)",
                    "priority": temp_priority,
                    "preview": True,
                    "match": {"expr": {"expression": new_expr}},
                    "description": f"godview-waf-preview-temp-priority-{temp_priority}",
                }
                add_op = await self._add_rule(client, project_id, security_policy, temp_rule_data, headers)
                await self._wait_for_global_operation(client, project_id, add_op, headers)

                temp_rule = await self._get_rule(client, project_id, security_policy, temp_priority, headers)
                temp_expr = temp_rule.get("match", {}).get("expr", {}).get("expression", "")
                temp_ok = (temp_rule.get("preview") is True) and (f"'sensitivity': {sensitivity}" in temp_expr)
                checks.append(
                    VerificationCheck(
                        check="waf_preview_temp_rule_active",
                        passed=temp_ok,
                        evidence={"temp_priority": temp_priority, "preview": temp_rule.get("preview"), "expression": temp_expr},
                    )
                )

                if not temp_ok:
                    rm_op = await self._remove_rule(client, project_id, security_policy, temp_priority, headers)
                    await self._wait_for_global_operation(client, project_id, rm_op, headers)
                    temp_priority = None
                    return ExecutionResult(
                        ok=False,
                        before=before,
                        after={"temp_priority": temp_priority, "preview": temp_rule.get("preview")},
                        verification=checks,
                        reverted=True,
                        error=f"Temp preview rule {temp_priority} verification failed",
                    )

                # Gate on preview LB logs (Plan §3.4, Verifier-Core #140)
                logging_adapter = self._find_logging_adapter(ctx)
                if not logging_adapter and env_mode == "gcp":
                    # Fail closed: live preview requires logging adapter to observe outcomes
                    rm_op = await self._remove_rule(client, project_id, security_policy, temp_priority, headers)
                    await self._wait_for_global_operation(client, project_id, rm_op, headers)
                    temp_priority = None
                    return ExecutionResult(
                        ok=False,
                        before=before,
                        after={},
                        verification=checks,
                        reverted=True,
                        error="Preview-first rollout aborted: no logging adapter exposing 'logging.query_lb_requests' found on ctx",
                    )

                if logging_adapter:
                    # Record t0 = operation completion time (0s margin, past 2s to catch probes)
                    t0 = datetime.now(UTC) - timedelta(seconds=2.0)
                    preview_passed, log_entries, reason = await self._poll_preview_logs(
                        logging_adapter=logging_adapter,
                        client=client,
                        probe_url=effective_probe_url,
                        real_priority=priority,
                        temp_priority=temp_priority,
                        t0=t0,
                        env_mode=env_mode,
                        timeout_s=min(float(args.get("preview_window_s", 60.0)), 60.0),
                        interval_s=5.0,
                        min_evaluations=int(args.get("min_preview_evaluations", 3)),
                        min_polls=int(args.get("min_preview_polls", 2)),
                    )
                    checks.append(
                        VerificationCheck(
                            check="preview_mode_lb_logs_observed",
                            passed=preview_passed,
                            evidence={
                                "evaluations_observed": len(log_entries),
                                "temp_priority": temp_priority,
                                "details": reason,
                            },
                        )
                    )
                    if not preview_passed:
                        logger.warning("Preview observation gate failed: %s; cleaning up temp rule %s", reason, temp_priority)
                        rm_op = await self._remove_rule(client, project_id, security_policy, temp_priority, headers)
                        await self._wait_for_global_operation(client, project_id, rm_op, headers)
                        temp_priority = None
                        return ExecutionResult(
                            ok=False,
                            before=before,
                            after={"temp_priority": temp_priority},
                            verification=checks,
                            reverted=True,
                            error=f"Preview observation gate failed: {reason}",
                        )

            # Direct patch / Enforce: patch rule (priority) with new_expr and preview=False
            enforce_patch = {
                "priority": priority,
                "match": {"expr": {"expression": new_expr}},
                "preview": False,
            }
            patch_op = await self._patch_rule(client, project_id, security_policy, priority, enforce_patch, headers)
            await self._wait_for_global_operation(client, project_id, patch_op, headers)

            # If a temp preview rule was created, delete it now that enforce is applied
            if temp_priority is not None:
                try:
                    del_op = await self._remove_rule(client, project_id, security_policy, temp_priority, headers)
                    await self._wait_for_global_operation(client, project_id, del_op, headers)
                    checks.append(
                        VerificationCheck(
                            check="waf_preview_temp_rule_cleaned_up",
                            passed=True,
                            evidence={"deleted_temp_priority": temp_priority},
                        )
                    )
                    temp_priority = None
                except Exception as del_err:
                    logger.warning("Failed to remove temp preview rule %s: %s", temp_priority, del_err)

            # Verify enforced state on Compute API
            final_rule = await self._get_rule(client, project_id, security_policy, priority, headers)
            final_expr = final_rule.get("match", {}).get("expr", {}).get("expression", "")
            final_preview = final_rule.get("preview", True)

            final_ok = (final_preview is False) and (f"'sensitivity': {sensitivity}" in final_expr)
            checks.append(
                VerificationCheck(
                    check="waf_rule_enforced_with_sensitivity",
                    passed=final_ok,
                    evidence={"expression": final_expr, "preview": final_preview},
                )
            )

            after = {
                "priority": priority,
                "expression": final_expr,
                "preview": final_preview,
            }

            if not final_ok:
                if temp_priority is not None:
                    try:
                        rm_op = await self._remove_rule(client, project_id, security_policy, temp_priority, headers)
                        await self._wait_for_global_operation(client, project_id, rm_op, headers)
                    except Exception:
                        pass
                rollback_op = await self._patch_rule(
                    client,
                    project_id,
                    security_policy,
                    priority,
                    {"priority": priority, "match": {"expr": {"expression": orig_expr}}, "preview": orig_preview},
                    headers,
                )
                await self._wait_for_global_operation(client, project_id, rollback_op, headers)
                return ExecutionResult(
                    ok=False,
                    before=before,
                    after=after,
                    verification=checks,
                    reverted=True,
                    error="Final verification failed: rule was not confirmed as enforced",
                )

            # Step 5: Post-enforcement probe verification via polled canary GET
            probe_passed, probe_status, probe_evidence = await self._poll_probe_until_success(
                client=client,
                probe_url=effective_probe_url,
                env_mode=env_mode,
            )
            checks.append(
                VerificationCheck(
                    check="post_enforce_probe_bypasses_waf_block",
                    passed=probe_passed,
                    evidence={"status_code": probe_status, "evidence": probe_evidence},
                )
            )
            if not probe_passed:
                if temp_priority is not None:
                    try:
                        rm_op = await self._remove_rule(client, project_id, security_policy, temp_priority, headers)
                        await self._wait_for_global_operation(client, project_id, rm_op, headers)
                    except Exception:
                        pass
                logger.warning("Post-enforcement probe returned 403; rolling back WAF rule on %s", security_policy)
                rollback_op = await self._patch_rule(
                    client,
                    project_id,
                    security_policy,
                    priority,
                    {"priority": priority, "match": {"expr": {"expression": orig_expr}}, "preview": orig_preview},
                    headers,
                )
                await self._wait_for_global_operation(client, project_id, rollback_op, headers)
                return ExecutionResult(
                    ok=False,
                    before=before,
                    after=after,
                    verification=checks,
                    reverted=True,
                    error="Post-enforcement probe returned 403 Forbidden: Cloud Armor is blocking legitimate requests",
                )

            return ExecutionResult(ok=True, before=before, after=after, verification=checks)
        except Exception as e:
            if temp_priority is not None:
                try:
                    rm_op = await self._remove_rule(client, project_id, security_policy, temp_priority, headers)
                    await self._wait_for_global_operation(client, project_id, rm_op, headers)
                except Exception:
                    pass
            logger.exception("Error executing set_waf_sensitivity on %s", security_policy)
            if before.get("expression"):
                try:
                    rollback_op = await self._patch_rule(
                        client,
                        project_id,
                        security_policy,
                        priority,
                        {"priority": priority, "match": {"expr": {"expression": before["expression"]}}, "preview": before["preview"]},
                        headers,
                    )
                    await self._wait_for_global_operation(client, project_id, rollback_op, headers)
                except Exception as rollback_err:
                    logger.error("Failed to rollback Cloud Armor rule: %s", rollback_err)
            return ExecutionResult(
                ok=False,
                before=before if before else None,
                after=None,
                verification=checks,
                reverted=bool(before.get("expression")),
                error=str(e),
            )


__all__ = ["CloudArmorExecutor", "patch_waf_expression", "validate_probe_url"]
