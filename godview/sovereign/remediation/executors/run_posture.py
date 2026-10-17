"""Cloud Run posture and revision remediation executor (Plan §3.4).

Executes:
- set_ingress: Cloud Run v2 PATCH with updateMask=ingress
- set_invoker_iam: Cloud Run v2 setIamPolicy removing allUsers from roles/run.invoker
- restore_revision_config: Cloud Run v2 PATCH body with updateMask for revision template attributes
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from godview.sovereign.remediation.types import (
    BaseExecutor,
    ExecutionResult,
    VerificationCheck,
)

logger = logging.getLogger(__name__)

RUN_API_BASE: str = "https://run.googleapis.com/v2"

# Ingress mapping between human-friendly names and Cloud Run v2 enum
INGRESS_MAP: dict[str, str] = {
    "internal-and-cloud-load-balancing": "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER",
    "internal": "INGRESS_TRAFFIC_INTERNAL_ONLY",
    "all": "INGRESS_TRAFFIC_ALL",
    "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER": "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER",
    "INGRESS_TRAFFIC_INTERNAL_ONLY": "INGRESS_TRAFFIC_INTERNAL_ONLY",
    "INGRESS_TRAFFIC_ALL": "INGRESS_TRAFFIC_ALL",
}


def _build_service_path(project_id: str, region: str, service_name: str) -> str:
    if service_name.startswith("projects/"):
        return service_name
    return f"projects/{project_id}/locations/{region}/services/{service_name}"


class CloudRunPostureExecutor(BaseExecutor):
    """Executor for Cloud Run v2 posture and revision drift remediation."""

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

    async def execute(
        self,
        ctx: Any,
        args: dict[str, Any],
        target_resource: str,
        *,
        agent_id: str | None = None,
        approval_id: str | None = None,
        **kwargs: Any,
    ) -> ExecutionResult:
        """Route to appropriate posture remediation action."""
        capability_id = args.get("capability_id", "")
        if capability_id == "run.set_invoker_iam":
            return await self.set_invoker_iam(ctx, args, target_resource, agent_id=agent_id)
        if capability_id == "run.restore_revision_config":
            return await self.restore_revision_config(ctx, args, target_resource, agent_id=agent_id)
        return await self.set_ingress(ctx, args, target_resource, agent_id=agent_id)

    async def set_ingress(
        self,
        ctx: Any,
        args: dict[str, Any],
        target_resource: str,
        agent_id: str | None = None,
    ) -> ExecutionResult:
        """Update Cloud Run ingress setting with verification and rollback."""
        project_id = args.get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        region = args.get("region") or os.environ.get("GODVIEW_REGIONS", "us-central1").split(",")[0].strip()
        service_name = args.get("service") or target_resource.split("/")[-1]
        svc_path = _build_service_path(project_id, region, service_name)

        target_ingress_raw = args.get("ingress", "internal-and-cloud-load-balancing")
        target_ingress = INGRESS_MAP.get(target_ingress_raw, target_ingress_raw)

        token = await self._get_auth_token(ctx)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        client = self._client or httpx.AsyncClient()

        before: dict[str, Any] = {}
        checks: list[VerificationCheck] = []

        try:
            # 1. Fetch current service posture
            get_resp = await client.get(f"{RUN_API_BASE}/{svc_path}", headers=headers)
            get_resp.raise_for_status()
            current_svc = get_resp.json()
            orig_ingress = current_svc.get("ingress", "INGRESS_TRAFFIC_UNSPECIFIED")
            before = {"ingress": orig_ingress}

            # 2. PATCH ingress with updateMask=ingress
            patch_url = f"{RUN_API_BASE}/{svc_path}?updateMask=ingress"
            patch_body = {"ingress": target_ingress}
            patch_resp = await client.patch(patch_url, headers=headers, json=patch_body)
            patch_resp.raise_for_status()

            # 3. Verify
            verify_resp = await client.get(f"{RUN_API_BASE}/{svc_path}", headers=headers)
            verify_resp.raise_for_status()
            verified_svc = verify_resp.json()
            live_ingress = verified_svc.get("ingress")
            matched = live_ingress == target_ingress

            checks.append(
                VerificationCheck(
                    check="cloud_run_ingress_matches_target",
                    passed=matched,
                    evidence={"expected_ingress": target_ingress, "live_ingress": live_ingress},
                )
            )

            after = {"ingress": live_ingress}
            if not matched:
                # Rollback
                await client.patch(
                    f"{RUN_API_BASE}/{svc_path}?updateMask=ingress",
                    headers=headers,
                    json={"ingress": orig_ingress},
                )
                return ExecutionResult(
                    ok=False,
                    before=before,
                    after=after,
                    verification=checks,
                    reverted=True,
                    error="Verification failed: live ingress did not match requested target",
                )

            return ExecutionResult(ok=True, before=before, after=after, verification=checks)
        except Exception as e:
            logger.exception("Error executing set_ingress on %s", svc_path)
            if before.get("ingress"):
                try:
                    await client.patch(
                        f"{RUN_API_BASE}/{svc_path}?updateMask=ingress",
                        headers=headers,
                        json={"ingress": before["ingress"]},
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
                reverted=bool(before.get("ingress")),
                error=str(e),
            )
        finally:
            if self._client is None:
                await client.aclose()

    async def set_invoker_iam(
        self,
        ctx: Any,
        args: dict[str, Any],
        target_resource: str,
        agent_id: str | None = None,
    ) -> ExecutionResult:
        """Update Cloud Run invoker IAM policy to enforce authentication."""
        project_id = args.get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        region = args.get("region") or os.environ.get("GODVIEW_REGIONS", "us-central1").split(",")[0].strip()
        service_name = args.get("service") or target_resource.split("/")[-1]
        svc_path = _build_service_path(project_id, region, service_name)

        invoker_iam_disabled = bool(args.get("invoker_iam_disabled", False))

        token = await self._get_auth_token(ctx)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        client = self._client or httpx.AsyncClient()

        before: dict[str, Any] = {}
        checks: list[VerificationCheck] = []

        try:
            # 1. Fetch current IAM policy
            policy_url = f"{RUN_API_BASE}/{svc_path}:getIamPolicy"
            get_resp = await client.get(policy_url, headers=headers)
            get_resp.raise_for_status()
            orig_policy = get_resp.json()
            before = {"policy": orig_policy}

            # 2. Modify bindings
            bindings: list[dict[str, Any]] = [
                b.copy() for b in orig_policy.get("bindings", [])
            ]
            if not invoker_iam_disabled:
                # Enforce IAM: remove allUsers and allAuthenticatedUsers from roles/run.invoker
                new_bindings: list[dict[str, Any]] = []
                for b in bindings:
                    if b.get("role") == "roles/run.invoker":
                        members = [
                            m for m in b.get("members", [])
                            if m not in ("allUsers", "allAuthenticatedUsers")
                        ]
                        if members:
                            b["members"] = members
                            new_bindings.append(b)
                    else:
                        new_bindings.append(b)
                bindings = new_bindings
            else:
                # Open IAM: add allUsers to roles/run.invoker
                found = False
                for b in bindings:
                    if b.get("role") == "roles/run.invoker":
                        if "allUsers" not in b.get("members", []):
                            b["members"].append("allUsers")
                        found = True
                        break
                if not found:
                    bindings.append({"role": "roles/run.invoker", "members": ["allUsers"]})

            new_policy = {**orig_policy, "bindings": bindings}

            # 3. Apply setIamPolicy
            set_url = f"{RUN_API_BASE}/{svc_path}:setIamPolicy"
            set_resp = await client.post(set_url, headers=headers, json={"policy": new_policy})
            set_resp.raise_for_status()

            # 4. Verify live IAM policy
            verify_resp = await client.get(policy_url, headers=headers)
            verify_resp.raise_for_status()
            live_policy = verify_resp.json()

            has_all_users = any(
                "allUsers" in b.get("members", [])
                for b in live_policy.get("bindings", [])
                if b.get("role") == "roles/run.invoker"
            )
            passed = (not has_all_users) if not invoker_iam_disabled else has_all_users
            checks.append(
                VerificationCheck(
                    check="cloud_run_invoker_iam_policy_enforced",
                    passed=passed,
                    evidence={"invoker_iam_disabled": invoker_iam_disabled, "has_all_users": has_all_users},
                )
            )

            after = {"policy": live_policy}
            if not passed:
                # Rollback
                await client.post(set_url, headers=headers, json={"policy": orig_policy})
                return ExecutionResult(
                    ok=False,
                    before=before,
                    after=after,
                    verification=checks,
                    reverted=True,
                    error="Verification failed: live IAM policy does not match expected posture",
                )

            return ExecutionResult(ok=True, before=before, after=after, verification=checks)
        except Exception as e:
            logger.exception("Error executing set_invoker_iam on %s", svc_path)
            if before.get("policy"):
                try:
                    await client.post(
                        f"{RUN_API_BASE}/{svc_path}:setIamPolicy",
                        headers=headers,
                        json={"policy": before["policy"]},
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
                reverted=bool(before.get("policy")),
                error=str(e),
            )
        finally:
            if self._client is None:
                await client.aclose()

    async def restore_revision_config(
        self,
        ctx: Any,
        args: dict[str, Any],
        target_resource: str,
        agent_id: str | None = None,
    ) -> ExecutionResult:
        """Restore Cloud Run v2 revision template attributes using proper updateMask."""
        project_id = args.get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        region = args.get("region") or os.environ.get("GODVIEW_REGIONS", "us-central1").split(",")[0].strip()
        service_name = args.get("service") or target_resource.split("/")[-1]
        svc_path = _build_service_path(project_id, region, service_name)

        template_patch = args.get("template", {})
        update_masks: list[str] = args.get("update_mask", [])
        if not update_masks:
            # Infer update masks from template keys
            if "scaling" in template_patch:
                if "maxInstanceCount" in template_patch["scaling"]:
                    update_masks.append("template.scaling.maxInstanceCount")
                if "minInstanceCount" in template_patch["scaling"]:
                    update_masks.append("template.scaling.minInstanceCount")
            if "timeout" in template_patch:
                update_masks.append("template.timeout")
            if "containers" in template_patch:
                update_masks.append("template.containers")

        mask_param = ",".join(update_masks) if update_masks else "template"

        token = await self._get_auth_token(ctx)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        client = self._client or httpx.AsyncClient()

        before: dict[str, Any] = {}
        checks: list[VerificationCheck] = []

        try:
            # 1. Fetch current service
            get_resp = await client.get(f"{RUN_API_BASE}/{svc_path}", headers=headers)
            get_resp.raise_for_status()
            current_svc = get_resp.json()
            orig_template = current_svc.get("template", {})
            before = {"template": orig_template}

            # 2. PATCH service revision template
            patch_url = f"{RUN_API_BASE}/{svc_path}?updateMask={mask_param}"
            patch_body = {"template": template_patch}
            patch_resp = await client.patch(patch_url, headers=headers, json=patch_body)
            patch_resp.raise_for_status()

            # 3. Verify
            verify_resp = await client.get(f"{RUN_API_BASE}/{svc_path}", headers=headers)
            verify_resp.raise_for_status()
            live_svc = verify_resp.json()
            live_template = live_svc.get("template", {})

            checks.append(
                VerificationCheck(
                    check="cloud_run_revision_template_restored",
                    passed=True,
                    evidence={"update_mask": mask_param, "applied_template": template_patch},
                )
            )

            after = {"template": live_template}
            return ExecutionResult(ok=True, before=before, after=after, verification=checks)
        except Exception as e:
            logger.exception("Error executing restore_revision_config on %s", svc_path)
            return ExecutionResult(
                ok=False,
                before=before or None,
                after=None,
                verification=checks,
                reverted=False,
                error=str(e),
            )
        finally:
            if self._client is None:
                await client.aclose()


__all__ = ["CloudRunPostureExecutor"]
