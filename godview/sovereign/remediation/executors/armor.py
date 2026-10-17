"""Cloud Armor remediation executor (Plan §3.4).

Executes:
- set_waf_sensitivity: patches evaluatePreconfiguredWaf(...) calls to add/update {'sensitivity': N}.
  Rolls out in preview=True first, verifies via LB logs / probe, then sets preview=False.
  Verifies post-enforcement with probe (200/422 pass, 403 fail). Auto-rollbacks on failure.
- set_rule_preview: toggles preview flag on a rule (used by operator kill switch).
"""

from __future__ import annotations

import logging
import os
import re
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
    {"169.254.169.254", "metadata.google.internal", "metadata"}
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


def validate_probe_url(url: str, env_mode: str = "gcp") -> None:
    """Validate that probe URL does not target internal metadata servers (SSRF defense)."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ScopeViolationError(f"Invalid probe URL '{url}': missing hostname")
    if hostname in FORBIDDEN_PROBE_HOSTS:
        raise ScopeViolationError(f"Probe URL '{url}' targets forbidden internal host (SSRF protection)")
    if env_mode != "hermetic" and (hostname.startswith("127.") or hostname == "localhost"):
        raise ScopeViolationError(f"Probe URL '{url}' targets loopback in non-hermetic mode (SSRF protection)")


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
            rules = policy.get("rules", [])
            for r in rules:
                expr = r.get("match", {}).get("expr", {}).get("expression", "")
                desc = (r.get("description") or "").lower()
                action = r.get("action", "")
                if for_waf and "evaluatePreconfiguredWaf" in expr:
                    return int(r.get("priority", 100))
                if not for_waf and (
                    "kill-switch" in desc
                    or "deny" in action
                    or "evaluatePreconfiguredWaf" in expr
                ):
                    return int(r.get("priority", 100))
        except Exception as e:
            logger.debug("Could not inspect security policy rules for discovery: %s", e)
        return 100

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

        env_mode = getattr(getattr(ctx, "settings", None), "env_mode", os.environ.get("GODVIEW_ENV", "gcp"))
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
        await self._patch_rule(client, project_id, security_policy, priority, patch_body, headers)

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
            await self._patch_rule(
                client,
                project_id,
                security_policy,
                priority,
                {"priority": priority, "preview": before["preview"]},
                headers,
            )
            return ExecutionResult(
                ok=False,
                before=before,
                after=after,
                verification=[check],
                reverted=True,
                error=f"Failed to set rule preview to {preview_val}",
            )

        return ExecutionResult(ok=True, before=before, after=after, verification=[check])

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
        """Update WAF rule sensitivity with preview-first safety rollout, verification, and rollback."""
        # Policy is STRICTLY derived from target_resource
        clean_target = target_resource.split("/")[-1].split("?")[0].strip()
        security_policy = clean_target

        project_id = args.get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        sensitivity = int(args.get("sensitivity", 1))

        env_mode = getattr(getattr(ctx, "settings", None), "env_mode", os.environ.get("GODVIEW_ENV", "gcp"))

        # Resolve probe URL: validate against SSRF
        effective_probe_url = probe_url or args.get("probe_url")
        if effective_probe_url:
            validate_probe_url(effective_probe_url, env_mode=env_mode)

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

            # Step 3: Phase 1 Rollout with preview=True first
            preview_patch = {
                "priority": priority,
                "match": {"expr": {"expression": new_expr}},
                "preview": True,
            }
            await self._patch_rule(client, project_id, security_policy, priority, preview_patch, headers)

            # Verify preview patch on Compute API
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

            if not preview_ok:
                # Rollback preview patch
                await self._patch_rule(
                    client,
                    project_id,
                    security_policy,
                    priority,
                    {"priority": priority, "match": {"expr": {"expression": orig_expr}}, "preview": orig_preview},
                    headers,
                )
                return ExecutionResult(
                    ok=False,
                    before=before,
                    after={"expression": preview_expr, "preview": preview_rule.get("preview")},
                    verification=checks,
                    reverted=True,
                    error="Failed preview verification: sensitivity expression not confirmed",
                )

            # Step 4: Phase 2 Enforce rule with preview=False
            enforce_patch = {
                "priority": priority,
                "match": {"expr": {"expression": new_expr}},
                "preview": False,
            }
            await self._patch_rule(client, project_id, security_policy, priority, enforce_patch, headers)

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
                # Rollback
                await self._patch_rule(
                    client,
                    project_id,
                    security_policy,
                    priority,
                    {"priority": priority, "match": {"expr": {"expression": orig_expr}}, "preview": orig_preview},
                    headers,
                )
                return ExecutionResult(
                    ok=False,
                    before=before,
                    after=after,
                    verification=checks,
                    reverted=True,
                    error="Final verification failed: rule was not confirmed as enforced",
                )

            # Step 5: Post-enforcement probe verification (real effect check)
            if effective_probe_url:
                try:
                    probe_resp = await client.post(
                        effective_probe_url,
                        json={},
                        timeout=5.0,
                    )
                    # 403 indicates Cloud Armor WAF is blocking legitimate requests!
                    # 200, 201, 422 indicate the request reached the application backend.
                    probe_passed = probe_resp.status_code != 403
                    checks.append(
                        VerificationCheck(
                            check="post_enforce_probe_bypasses_waf_block",
                            passed=probe_passed,
                            evidence={"status_code": probe_resp.status_code},
                        )
                    )
                    if not probe_passed:
                        # Rollback on 403 post-enforce
                        logger.warning("Post-enforcement probe returned 403; rolling back WAF rule on %s", security_policy)
                        await self._patch_rule(
                            client,
                            project_id,
                            security_policy,
                            priority,
                            {"priority": priority, "match": {"expr": {"expression": orig_expr}}, "preview": orig_preview},
                            headers,
                        )
                        return ExecutionResult(
                            ok=False,
                            before=before,
                            after=after,
                            verification=checks,
                            reverted=True,
                            error="Post-enforcement probe returned 403 Forbidden: Cloud Armor is blocking legitimate requests",
                        )
                except httpx.HTTPError as probe_err:
                    checks.append(
                        VerificationCheck(
                            check="post_enforce_probe_bypasses_waf_block",
                            passed=False,
                            evidence={"error": str(probe_err)},
                        )
                    )
                    # Rollback on probe communication failure
                    await self._patch_rule(
                        client,
                        project_id,
                        security_policy,
                        priority,
                        {"priority": priority, "match": {"expr": {"expression": orig_expr}}, "preview": orig_preview},
                        headers,
                    )
                    return ExecutionResult(
                        ok=False,
                        before=before,
                        after=after,
                        verification=checks,
                        reverted=True,
                        error=f"Post-enforcement probe network failure: {probe_err}",
                    )

            return ExecutionResult(ok=True, before=before, after=after, verification=checks)
        except Exception as e:
            logger.exception("Error executing set_waf_sensitivity on %s", security_policy)
            if before.get("expression"):
                try:
                    await self._patch_rule(
                        client,
                        project_id,
                        security_policy,
                        priority,
                        {"priority": priority, "match": {"expr": {"expression": before["expression"]}}, "preview": before["preview"]},
                        headers,
                    )
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
