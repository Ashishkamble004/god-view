"""GKE / Kubernetes probe remediation executor (Plan §3.4).

Executes:
- patch_probe: resolves GKE cluster endpoint and CA via Container API, then
  patches container probe configuration (e.g. startupProbe, livenessProbe)
  on a Kubernetes Deployment via Strategic Merge Patch, verifying live state.
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

CONTAINER_API_BASE: str = "https://container.googleapis.com/v1"

DEFAULT_STARTUP_PROBE: dict[str, Any] = {
    "httpGet": {"path": "/healthz", "port": 8080},
    "periodSeconds": 2,
    "failureThreshold": 45,
}


class GkeProbeExecutor(BaseExecutor):
    """Executor for Kubernetes Deployment container probe patches."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client

    async def _resolve_k8s_api_base(
        self,
        ctx: Any,
        project_id: str,
        location: str,
        cluster: str,
        client: httpx.AsyncClient,
    ) -> tuple[str, str | None]:
        """Resolve cluster API endpoint and CA certificate via GKE Container API."""
        env_mode = getattr(getattr(ctx, "settings", None), "env_mode", os.environ.get("GODVIEW_ENV", "gcp"))
        if env_mode == "hermetic":
            host = os.environ.get("KUBERNETES_SERVICE_HOST", "127.0.0.1")
            port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
            return f"https://{host}:{port}", None

        # Check Container API for cluster endpoint
        token = await get_act_sa_access_token(ctx, project_id=project_id, client=client)
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{CONTAINER_API_BASE}/projects/{project_id}/locations/{location}/clusters/{cluster}"
        try:
            resp = await client.get(url, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                endpoint = data.get("endpoint")
                ca_cert = data.get("masterAuth", {}).get("clusterCaCertificate")
                if endpoint:
                    return f"https://{endpoint}", ca_cert
        except Exception as e:
            logger.debug("Could not resolve cluster via Container API: %s", e)

        host = os.environ.get("KUBERNETES_SERVICE_HOST", "127.0.0.1")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        return f"https://{host}:{port}", None

    async def _get_auth_token(self, ctx: Any, project_id: str | None = None) -> str:
        token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
        if os.path.exists(token_path):
            try:
                with open(token_path) as f:
                    return f.read().strip()
            except OSError:
                pass
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
        """Patch a Kubernetes Deployment probe with verification and rollback."""
        project_id = args.get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        location = args.get("location") or os.environ.get("GODVIEW_REGIONS", "us-central1").split(",")[0].strip()
        cluster = args.get("cluster") or os.environ.get("GKE_CLUSTER", "main-cluster")

        namespace = args.get("namespace", "default")
        raw_deployment = args.get("deployment") or target_resource.split("/")[-1].split("?")[0].strip()
        deployment = raw_deployment.replace("deployment-", "").replace("workload-", "")
        container_name = args.get("container")
        probe_type = args.get("probe_type", "startupProbe")
        probe_spec = args.get("probe_spec") or DEFAULT_STARTUP_PROBE

        client = self._client or httpx.AsyncClient()
        base_url, _ca = await self._resolve_k8s_api_base(ctx, project_id, location, cluster, client)

        token = await self._get_auth_token(ctx, project_id=project_id)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/strategic-merge-patch+json",
        }

        deploy_url = f"{base_url}/apis/apps/v1/namespaces/{namespace}/deployments/{deployment}"
        before: dict[str, Any] = {}
        checks: list[VerificationCheck] = []

        try:
            # 1. Fetch current Deployment
            get_resp = await client.get(deploy_url, headers={"Authorization": f"Bearer {token}"})
            get_resp.raise_for_status()
            current_dep = get_resp.json()

            containers = current_dep.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
            target_container: dict[str, Any] | None = None
            for c in containers:
                if container_name is None or c.get("name") == container_name:
                    target_container = c
                    if container_name is None:
                        container_name = c.get("name")
                    break

            if target_container is None:
                return ExecutionResult(
                    ok=False,
                    before=None,
                    after=None,
                    verification=[],
                    reverted=False,
                    error=f"Container '{container_name}' not found in deployment '{deployment}'",
                )

            orig_probe = target_container.get(probe_type)
            before = {"deployment": deployment, "container": container_name, probe_type: orig_probe}

            # 2. Apply strategic merge patch
            patch_body = {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": container_name,
                                    probe_type: probe_spec,
                                }
                            ]
                        }
                    }
                }
            }

            patch_resp = await client.patch(deploy_url, headers=headers, json=patch_body)
            patch_resp.raise_for_status()

            # 3. Verify
            verify_resp = await client.get(deploy_url, headers={"Authorization": f"Bearer {token}"})
            verify_resp.raise_for_status()
            live_dep = verify_resp.json()
            live_containers = live_dep.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])

            live_probe = None
            for c in live_containers:
                if c.get("name") == container_name:
                    live_probe = c.get(probe_type)
                    break

            passed = (live_probe is not None)
            checks.append(
                VerificationCheck(
                    check="kubernetes_probe_patched_and_verified",
                    passed=passed,
                    evidence={"probe_type": probe_type, "live_probe": live_probe, "expected": probe_spec},
                )
            )

            after = {"deployment": deployment, "container": container_name, probe_type: live_probe}

            if not passed:
                # Rollback
                rollback_body = {
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [
                                    {
                                        "name": container_name,
                                        probe_type: orig_probe,
                                    }
                                ]
                            }
                        }
                    }
                }
                await client.patch(deploy_url, headers=headers, json=rollback_body)
                return ExecutionResult(
                    ok=False,
                    before=before,
                    after=after,
                    verification=checks,
                    reverted=True,
                    error="Verification failed: patched probe not found on Deployment",
                )

            return ExecutionResult(ok=True, before=before, after=after, verification=checks)
        except Exception as e:
            logger.exception("Error executing patch_probe on %s", deployment)
            return ExecutionResult(
                ok=False,
                before=before if before else None,
                after=None,
                verification=checks,
                reverted=False,
                error=str(e),
            )


__all__ = ["DEFAULT_STARTUP_PROBE", "GkeProbeExecutor"]
