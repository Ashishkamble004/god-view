"""GKE / Kubernetes probe remediation executor (Plan §3.4).

Executes:
- patch_probe: patches container probe configuration (e.g. startupProbe, livenessProbe)
  on a Kubernetes Deployment via the Kubernetes API.
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

DEFAULT_STARTUP_PROBE: dict[str, Any] = {
    "httpGet": {"path": "/healthz", "port": 8080},
    "periodSeconds": 2,
    "failureThreshold": 45,
}


class GkeProbeExecutor(BaseExecutor):
    """Executor for Kubernetes Deployment container probe patches."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client

    def _get_k8s_api_base(self) -> str:
        host = os.environ.get("KUBERNETES_SERVICE_HOST", "127.0.0.1")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        return f"https://{host}:{port}"

    async def _get_auth_token(self, ctx: Any) -> str:
        token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
        if os.path.exists(token_path):
            try:
                with open(token_path) as f:
                    return f.read().strip()
            except OSError:
                pass
        if ctx and hasattr(ctx, "auth") and ctx.auth is not None:
            token_fn = getattr(ctx.auth, "access_token", None)
            if callable(token_fn):
                res = token_fn()
                if hasattr(res, "__await__"):
                    return str(await res)
                return str(res)
        return "mock-k8s-token"

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
        namespace = args.get("namespace", "default")
        raw_deployment = args.get("deployment") or target_resource.split("/")[-1]
        deployment = raw_deployment.replace("deployment-", "").replace("workload-", "")
        container_name = args.get("container")
        probe_type = args.get("probe_type", "startupProbe")
        probe_spec = args.get("probe_spec") or DEFAULT_STARTUP_PROBE

        token = await self._get_auth_token(ctx)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/strategic-merge-patch+json",
        }
        client = self._client or httpx.AsyncClient(verify=False)

        api_base = self._get_k8s_api_base()
        dep_url = f"{api_base}/apis/apps/v1/namespaces/{namespace}/deployments/{deployment}"

        before: dict[str, Any] = {}
        checks: list[VerificationCheck] = []

        try:
            # 1. Fetch current Deployment
            get_resp = await client.get(dep_url, headers={"Authorization": f"Bearer {token}"})
            get_resp.raise_for_status()
            dep_data = get_resp.json()

            containers = dep_data.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
            target_container = None
            for c in containers:
                if container_name is None or c.get("name") == container_name:
                    target_container = c
                    container_name = c.get("name", "app")
                    break

            orig_probe = target_container.get(probe_type) if target_container else None
            before = {"probe": orig_probe, "container": container_name}

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
            patch_resp = await client.patch(dep_url, headers=headers, json=patch_body)
            patch_resp.raise_for_status()

            # 3. Verify
            verify_resp = await client.get(dep_url, headers={"Authorization": f"Bearer {token}"})
            verify_resp.raise_for_status()
            verify_data = verify_resp.json()

            live_containers = verify_data.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
            live_probe = None
            for c in live_containers:
                if c.get("name") == container_name:
                    live_probe = c.get(probe_type)
                    break

            verified = live_probe == probe_spec
            checks.append(
                VerificationCheck(
                    check="kubernetes_deployment_probe_configured",
                    passed=verified,
                    evidence={"probe_type": probe_type, "live_probe": live_probe},
                )
            )

            after = {"probe": live_probe, "container": container_name}
            if not verified:
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
                await client.patch(dep_url, headers=headers, json=rollback_body)
                return ExecutionResult(
                    ok=False,
                    before=before,
                    after=after,
                    verification=checks,
                    reverted=True,
                    error="Verification failed: live container probe does not match requested spec",
                )

            return ExecutionResult(ok=True, before=before, after=after, verification=checks)
        except Exception as e:
            logger.exception("Error executing patch_probe on deployment %s", deployment)
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


__all__ = ["DEFAULT_STARTUP_PROBE", "GkeProbeExecutor"]
