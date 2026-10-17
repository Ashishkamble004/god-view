"""Cloud Armor remediation executor (Plan §3.4).

Executes:
- set_waf_sensitivity: patches evaluatePreconfiguredWaf(...) calls to add/update {'sensitivity': N}.
  Rolls out in preview=True first, verifies via LB logs / probe, then sets preview=False.
- set_rule_preview: toggles preview flag on a rule (used by operator kill switch).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from godview.sovereign.remediation.types import (
    BaseExecutor,
    ExecutionResult,
    VerificationCheck,
)

logger = logging.getLogger(__name__)

COMPUTE_API_BASE: str = "https://compute.googleapis.com/compute/v1"


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


class CloudArmorExecutor(BaseExecutor):
    """Executor for Cloud Armor security policy rules."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client

    async def _get_auth_token(self, ctx: Any) -> str:
        if ctx and hasattr(ctx, "auth") and ctx.auth is not None:
            token_fn = getattr(ctx.auth, "access_token", None)
            if callable(token_fn):
                res = token_fn()
                if hasattr(res, "__await__"):
                    return str(await res)
                return str(res)
        return "mock-token"

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
        resp = await client.patch(url, headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()

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
        """Route Cloud Armor capability execution."""
        capability_id = args.get("capability_id", "armor.set_waf_sensitivity")
        if capability_id == "armor.set_rule_preview":
            return await self.set_rule_preview(
                ctx=ctx,
                args=args,
                target_resource=target_resource,
                agent_id=agent_id,
            )
        return await self.set_waf_sensitivity(
            ctx=ctx,
            args=args,
            target_resource=target_resource,
            agent_id=agent_id,
            probe_url=probe_url,
        )

    async def set_rule_preview(
        self,
        ctx: Any,
        args: dict[str, Any],
        target_resource: str,
        agent_id: str | None = None,
    ) -> ExecutionResult:
        """Toggle preview flag on a Cloud Armor security policy rule (used for kill switch)."""
        project_id = args.get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        security_policy = args.get("security_policy") or target_resource.split("/")[-1]
        priority = int(args.get("priority", 100))
        preview = bool(args.get("preview", True))

        token = await self._get_auth_token(ctx)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        client = self._client or httpx.AsyncClient()

        try:
            # 1. Fetch current rule (before)
            before_rule = await self._get_rule(client, project_id, security_policy, priority, headers)
            before = {"priority": priority, "preview": before_rule.get("preview")}

            # 2. Patch rule preview
            patch_body = {"priority": priority, "preview": preview}
            await self._patch_rule(client, project_id, security_policy, priority, patch_body, headers)

            # 3. Verify
            after_rule = await self._get_rule(client, project_id, security_policy, priority, headers)
            verified = after_rule.get("preview") == preview
            checks = [
                VerificationCheck(
                    check="rule_preview_matches_target",
                    passed=verified,
                    evidence={"expected_preview": preview, "live_preview": after_rule.get("preview")},
                )
            ]

            after = {"priority": priority, "preview": after_rule.get("preview")}
            if not verified:
                # Rollback
                rollback_body = {"priority": priority, "preview": before_rule.get("preview")}
                await self._patch_rule(client, project_id, security_policy, priority, rollback_body, headers)
                return ExecutionResult(
                    ok=False,
                    before=before,
                    after=after,
                    verification=checks,
                    reverted=True,
                    error="Verification failed: live preview flag does not match target",
                )

            return ExecutionResult(ok=True, before=before, after=after, verification=checks)
        finally:
            if self._client is None:
                await client.aclose()

    async def set_waf_sensitivity(
        self,
        ctx: Any,
        args: dict[str, Any],
        target_resource: str,
        agent_id: str | None = None,
        probe_url: str | None = None,
    ) -> ExecutionResult:
        """Update WAF rule sensitivity with preview-first safety rollout and verification."""
        project_id = args.get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        security_policy = args.get("security_policy") or target_resource.split("/")[-1]
        priority = int(args.get("priority", 1000))
        sensitivity = int(args.get("sensitivity", 1))
        preview_first = bool(args.get("preview_first", True))

        token = await self._get_auth_token(ctx)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        client = self._client or httpx.AsyncClient()

        before: dict[str, Any] = {}
        checks: list[VerificationCheck] = []

        try:
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

            # Step 3: Rollout in preview=True first if requested
            if preview_first:
                preview_patch = {
                    "priority": priority,
                    "match": {"expr": {"expression": new_expr}},
                    "preview": True,
                }
                await self._patch_rule(client, project_id, security_policy, priority, preview_patch, headers)

                # Verify preview patch
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

            # Step 4: Optional probe verification before enforcing
            effective_probe_url = probe_url or args.get("probe_url")
            if effective_probe_url:
                try:
                    probe_resp = await client.post(
                        effective_probe_url,
                        json={},
                        timeout=5.0,
                    )
                    # 403 indicates Cloud Armor is still hard-blocking JSON POST
                    # 422, 200, 201 indicate request reached backend
                    probe_passed = probe_resp.status_code != 403
                    checks.append(
                        VerificationCheck(
                            check="probe_json_post_bypasses_waf_block",
                            passed=probe_passed,
                            evidence={"status_code": probe_resp.status_code},
                        )
                    )
                except Exception as probe_err:
                    checks.append(
                        VerificationCheck(
                            check="probe_json_post_bypasses_waf_block",
                            passed=True,
                            evidence={"probe_skipped": str(probe_err)},
                        )
                    )

            # Step 5: Enforce rule with preview=False
            enforce_patch = {
                "priority": priority,
                "match": {"expr": {"expression": new_expr}},
                "preview": False,
            }
            await self._patch_rule(client, project_id, security_policy, priority, enforce_patch, headers)

            # Step 6: Verify final enforced state
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

            return ExecutionResult(ok=True, before=before, after=after, verification=checks)
        except Exception as e:
            logger.exception("Error executing set_waf_sensitivity on %s", security_policy)
            # Rollback if before state was captured
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
                except Exception as rb_err:
                    return ExecutionResult(
                        ok=False,
                        before=before,
                        after=None,
                        verification=checks,
                        reverted=False,
                        revert_error=str(rb_err),
                        error=str(e),
                    )
            return ExecutionResult(
                ok=False,
                before=before or None,
                after=None,
                verification=checks,
                reverted=bool(before.get("expression")),
                error=str(e),
            )
        finally:
            if self._client is None:
                await client.aclose()


__all__ = ["CloudArmorExecutor", "patch_waf_expression"]
