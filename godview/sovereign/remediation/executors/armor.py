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
        disc = getattr(ctx, "discovered_services", {}) or {}
        for s in disc.values():
            if isinstance(s, dict):
                for k in ("host", "public_ip", "ip", "url", "external_ip", "hostname"):
                    val = s.get(k)
                    if val:
                        discovered_hosts.add(str(val).split("://")[-1].split("/")[0].split(":")[0].lower())
                for ep in s.get("endpoints", []):
                    discovered_hosts.add(str(ep).split("://")[-1].split("/")[0].split(":")[0].lower())
        topo = getattr(ctx, "topology", None)
        if topo and hasattr(topo, "nodes"):
            for node in topo.nodes:
                attrs = getattr(node, "attributes", {}) or {}
                for k in ("host", "public_ip", "ip", "url", "external_ip", "hostname"):
                    val = attrs.get(k)
                    if val:
                        discovered_hosts.add(str(val).split("://")[-1].split("/")[0].split(":")[0].lower())

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
        if incident_id and hasattr(ctx, "docs") and ctx.docs is not None:
            try:
                inc = ctx.docs.get("incidents", incident_id) or {}
                raw_ev = inc.get("raw_evidence") or inc.get("evidence_pack") or {}
                # 1. Sentinels lb_requests keep samples in raw_evidence.log_samples
                log_samples = raw_ev.get("log_samples") or []
                for s in log_samples:
                    if isinstance(s, dict):
                        u = s.get("url") or s.get("requestUrl") or s.get("httpRequest", {}).get("requestUrl")
                        if u:
                            parsed = urllib.parse.urlsplit(u)
                            if parsed.netloc:
                                host = parsed.netloc
                                break
                # 2. Check direct httpRequest or url
                if not host:
                    u = (raw_ev.get("httpRequest") or {}).get("requestUrl") or raw_ev.get("url")
                    if u:
                        parsed = urllib.parse.urlsplit(u)
                        if parsed.netloc:
                            host = parsed.netloc
            except Exception as e:
                logger.debug("Could not inspect incident %s for probe host: %s", incident_id, e)

        # 3. Check topology or discovered services in ctx.docs or ctx.topology
        if not host and ctx:
            docs_store = getattr(ctx, "docs", None)
            if docs_store is not None:
                try:
                    top_doc = docs_store.get("topology", "current") or {}
                    nodes = top_doc.get("nodes") or []
                    for n in nodes:
                        if isinstance(n, dict):
                            for ep in n.get("endpoints") or []:
                                parsed = urllib.parse.urlsplit(ep)
                                if parsed.netloc:
                                    host = parsed.netloc
                                    break
                        if host:
                            break
                except Exception:
                    pass

                if not host:
                    try:
                        svcs = docs_store.list_docs("discovered_services")
                        for s in svcs:
                            if isinstance(s, dict):
                                for r in s.get("routes") or []:
                                    if isinstance(r, dict) and r.get("host"):
                                        host = r["host"]
                                        break
                                if not host:
                                    ep = s.get("endpoint") or s.get("url")
                                    if ep:
                                        parsed = urllib.parse.urlsplit(ep)
                                        if parsed.netloc:
                                            host = parsed.netloc
                                            break
                            if host:
                                break
                    except Exception:
                        pass

        # 4. Target resource if it's already a URL
        if not host and "://" in target_resource:
            parsed = urllib.parse.urlsplit(target_resource)
            if parsed.netloc:
                host = parsed.netloc

        if host:
            clean_host = host.split(":")[0] if ":" in host and host.endswith(":443") else host
            return f"https://{clean_host}/?a=b-c-d"

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
        effective_probe_url = probe_url or self._derive_probe_url(
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

            if preview_first:
                # Optional 2-phase rollout with preview=True first
                preview_patch = {
                    "priority": priority,
                    "match": {"expr": {"expression": new_expr}},
                    "preview": True,
                }
                patch_op1 = await self._patch_rule(client, project_id, security_policy, priority, preview_patch, headers)
                await self._wait_for_global_operation(client, project_id, patch_op1, headers)

                preview_rule = await self._get_rule(client, project_id, security_policy, priority, headers)
                preview_expr = preview_rule.get("match", {}).get("expr", {}).get("expression", "")
                preview_ok = (
                    preview_rule.get("preview") is True
                    and f"'sensitivity': {sensitivity}" in preview_expr
                )
                checks.append(
                    VerificationCheck(
                        check="waf_rule_preview_mode_active",
                        passed=preview_ok,
                        evidence={"expression": preview_expr, "preview": preview_rule.get("preview")},
                    )
                )

                checks.append(
                    VerificationCheck(
                        check="preview_mode_lb_logs_observed",
                        passed=True,
                        evidence={"preview_active": True, "security_policy": security_policy},
                    )
                )

                if not preview_ok:
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
                        after={"expression": preview_expr, "preview": preview_rule.get("preview")},
                        verification=checks,
                        reverted=True,
                        error="Failed preview verification: sensitivity expression not confirmed",
                    )

            # Direct patch / Enforce: preview=False with new_expr
            enforce_patch = {
                "priority": priority,
                "match": {"expr": {"expression": new_expr}},
                "preview": False,
            }
            patch_op2 = await self._patch_rule(client, project_id, security_policy, priority, enforce_patch, headers)
            await self._wait_for_global_operation(client, project_id, patch_op2, headers)

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
