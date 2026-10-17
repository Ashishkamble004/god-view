"""Cloud Run posture and revision remediation executor (Plan §3.4).

Executes:
- set_ingress: Cloud Run v2 PATCH with updateMask=ingress
- set_invoker_iam: Cloud Run v2 PATCH with updateMask=invokerIamDisabled, setting invokerIamDisabled=false,
  and removing allUsers from roles/run.invoker (never adding allUsers).
- restore_revision_config: Cloud Run v2 PATCH body with updateMask for revision template attributes,
  re-reading and verifying live template matches baseline.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from godview.sovereign.remediation.impersonation import get_act_sa_access_token
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

    async def _get_auth_token(self, ctx: Any, project_id: str | None = None) -> str:
        return await get_act_sa_access_token(ctx, project_id=project_id, client=self._client)

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
        service_name = args.get("service") or target_resource.split("/")[-1].split("?")[0].strip()
        svc_path = _build_service_path(project_id, region, service_name)

        target_ingress_raw = args.get("ingress", "internal-and-cloud-load-balancing")
        target_ingress = INGRESS_MAP.get(target_ingress_raw, target_ingress_raw)

        token = await self._get_auth_token(ctx, project_id=project_id)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        client = self._client or httpx.AsyncClient()

        before: dict[str, Any] = {}
        checks: list[VerificationCheck] = []

        try:
            # 1. Fetch current service
            get_resp = await client.get(f"{RUN_API_BASE}/{svc_path}", headers=headers)
            get_resp.raise_for_status()
            current_svc = get_resp.json()
            orig_ingress = current_svc.get("ingress", "")
            before = {"ingress": orig_ingress}

            # 2. PATCH service ingress with updateMask=ingress
            patch_url = f"{RUN_API_BASE}/{svc_path}?updateMask=ingress"
            patch_body = {"ingress": target_ingress}
            patch_resp = await client.patch(patch_url, headers=headers, json=patch_body)
            patch_resp.raise_for_status()

            # 3. Verify
            verify_resp = await client.get(f"{RUN_API_BASE}/{svc_path}", headers=headers)
            verify_resp.raise_for_status()
            live_svc = verify_resp.json()
            live_ingress = live_svc.get("ingress", "")

            passed = (live_ingress == target_ingress)
            checks.append(
                VerificationCheck(
                    check="cloud_run_ingress_verified",
                    passed=passed,
                    evidence={"ingress": live_ingress, "expected": target_ingress},
                )
            )

            after = {"ingress": live_ingress}
            if not passed:
                # Rollback
                await client.patch(patch_url, headers=headers, json={"ingress": orig_ingress})
                return ExecutionResult(
                    ok=False,
                    before=before,
                    after=after,
                    verification=checks,
                    reverted=True,
                    error=f"Cloud Run ingress mismatch: expected {target_ingress}, got {live_ingress}",
                )

            return ExecutionResult(ok=True, before=before, after=after, verification=checks)
        except Exception as e:
            logger.exception("Error executing set_ingress on %s", svc_path)
            return ExecutionResult(
                ok=False,
                before=before if before else None,
                after=None,
                verification=checks,
                reverted=False,
                error=str(e),
            )

    async def set_invoker_iam(
        self,
        ctx: Any,
        args: dict[str, Any],
        target_resource: str,
        agent_id: str | None = None,
    ) -> ExecutionResult:
        """Remediate Cloud Run invoker posture by disabling invokerIamDisabled and removing public bindings."""
        project_id = args.get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        region = args.get("region") or os.environ.get("GODVIEW_REGIONS", "us-central1").split(",")[0].strip()
        service_name = args.get("service") or target_resource.split("/")[-1].split("?")[0].strip()
        svc_path = _build_service_path(project_id, region, service_name)

        token = await self._get_auth_token(ctx, project_id=project_id)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        client = self._client or httpx.AsyncClient()

        before: dict[str, Any] = {}
        checks: list[VerificationCheck] = []

        try:
            # 1. Fetch current service to inspect invokerIamDisabled
            svc_resp = await client.get(f"{RUN_API_BASE}/{svc_path}", headers=headers)
            svc_resp.raise_for_status()
            current_svc = svc_resp.json()
            orig_disabled = current_svc.get("invokerIamDisabled", False)

            # 2. Fetch current IAM policy
            policy_url = f"{RUN_API_BASE}/{svc_path}:getIamPolicy"
            get_resp = await client.get(policy_url, headers=headers)
            get_resp.raise_for_status()
            orig_policy = get_resp.json()
            before = {"invokerIamDisabled": orig_disabled, "policy": orig_policy}

            # 3. Remediate T5-class drift: PATCH invokerIamDisabled=false with updateMask
            patch_url = f"{RUN_API_BASE}/{svc_path}?updateMask=invokerIamDisabled"
            patch_resp = await client.patch(patch_url, headers=headers, json={"invokerIamDisabled": False})
            patch_resp.raise_for_status()

            # 4. Remove allUsers and allAuthenticatedUsers from roles/run.invoker
            bindings: list[dict[str, Any]] = [
                b.copy() for b in orig_policy.get("bindings", [])
            ]
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

            new_policy = {**orig_policy, "bindings": new_bindings}
            set_url = f"{RUN_API_BASE}/{svc_path}:setIamPolicy"
            set_resp = await client.post(set_url, headers=headers, json={"policy": new_policy})
            set_resp.raise_for_status()

            # 5. Verify live state
            verify_svc_resp = await client.get(f"{RUN_API_BASE}/{svc_path}", headers=headers)
            verify_svc_resp.raise_for_status()
            live_svc = verify_svc_resp.json()
            live_disabled = live_svc.get("invokerIamDisabled", False)

            verify_iam_resp = await client.get(policy_url, headers=headers)
            verify_iam_resp.raise_for_status()
            live_policy = verify_iam_resp.json()

            has_public_invoker = False
            for b in live_policy.get("bindings", []):
                if b.get("role") == "roles/run.invoker":
                    if any(m in ("allUsers", "allAuthenticatedUsers") for m in b.get("members", [])):
                        has_public_invoker = True

            passed = (not live_disabled) and (not has_public_invoker)
            checks.append(
                VerificationCheck(
                    check="cloud_run_invoker_iam_enforced",
                    passed=passed,
                    evidence={
                        "invokerIamDisabled": live_disabled,
                        "has_public_invoker": has_public_invoker,
                    },
                )
            )

            after = {"invokerIamDisabled": live_disabled, "policy": live_policy}
            if not passed:
                # Rollback
                if orig_disabled:
                    await client.patch(patch_url, headers=headers, json={"invokerIamDisabled": orig_disabled})
                await client.post(set_url, headers=headers, json={"policy": orig_policy})
                return ExecutionResult(
                    ok=False,
                    before=before,
                    after=after,
                    verification=checks,
                    reverted=True,
                    error="Verification failed: public invoker or invokerIamDisabled still present",
                )

            return ExecutionResult(ok=True, before=before, after=after, verification=checks)
        except Exception as e:
            logger.exception("Error executing set_invoker_iam on %s", svc_path)
            return ExecutionResult(
                ok=False,
                before=before if before else None,
                after=None,
                verification=checks,
                reverted=False,
                error=str(e),
            )

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
        service_name = args.get("service") or target_resource.split("/")[-1].split("?")[0].strip()
        svc_path = _build_service_path(project_id, region, service_name)

        template_patch = args.get("template", {})
        update_masks: list[str] = args.get("update_mask", [])
        if not update_masks:
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

        token = await self._get_auth_token(ctx, project_id=project_id)
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

            # 3. Verify: re-read service and check patched fields match live template
            verify_resp = await client.get(f"{RUN_API_BASE}/{svc_path}", headers=headers)
            verify_resp.raise_for_status()
            live_svc = verify_resp.json()
            live_template = live_svc.get("template", {})

            # Check matching keys
            matches = True
            for k, v in template_patch.items():
                if isinstance(v, dict):
                    live_sub = live_template.get(k, {})
                    for sub_k, sub_v in v.items():
                        if live_sub.get(sub_k) != sub_v:
                            matches = False
                elif live_template.get(k) != v:
                    matches = False

            checks.append(
                VerificationCheck(
                    check="cloud_run_revision_template_restored",
                    passed=matches,
                    evidence={"update_mask": mask_param, "applied_template": template_patch, "live_matches": matches},
                )
            )

            after = {"template": live_template}
            if not matches:
                # Rollback
                await client.patch(f"{RUN_API_BASE}/{svc_path}?updateMask={mask_param}", headers=headers, json={"template": orig_template})
                return ExecutionResult(
                    ok=False,
                    before=before,
                    after=after,
                    verification=checks,
                    reverted=True,
                    error="Verification failed: live Cloud Run revision template did not match restored configuration",
                )

            return ExecutionResult(ok=True, before=before, after=after, verification=checks)
        except Exception as e:
            logger.exception("Error executing restore_revision_config on %s", svc_path)
            return ExecutionResult(
                ok=False,
                before=before if before else None,
                after=None,
                verification=checks,
                reverted=False,
                error=str(e),
            )


__all__ = ["CloudRunPostureExecutor", "INGRESS_MAP"]
