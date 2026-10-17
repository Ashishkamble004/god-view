"""Tenant administrative action remediation executor (Plan §3.4).

Executes:
- admin_action: invokes an OpenAPI operation tagged with vendor extension
  'x-godview-admin-action', with an OIDC token minted by ctx.auth as the act SA.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from godview.sovereign.remediation.types import (
    BaseExecutor,
    ExecutionResult,
    VerificationCheck,
)

logger = logging.getLogger(__name__)


class TenantAdminExecutor(BaseExecutor):
    """Executor for vendor-extended administrative operations discovered from OpenAPI."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client

    async def _get_oidc_token(self, ctx: Any, audience: str) -> str:
        if ctx and hasattr(ctx, "auth") and ctx.auth is not None:
            id_token_fn = getattr(ctx.auth, "id_token_for", None)
            if callable(id_token_fn):
                res = id_token_fn(audience)
                if hasattr(res, "__await__"):
                    return str(await res)
                return str(res)
        return "mock-oidc-token"

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
        """Execute discovered admin action with minted OIDC token."""
        url = args.get("url") or args.get("endpoint_url") or target_resource
        method = args.get("method", "POST").upper()
        body = args.get("body")
        headers = dict(args.get("headers", {}))

        # Mint OIDC token for audience
        audience = args.get("audience") or url.split("?")[0]
        token = await self._get_oidc_token(ctx, audience)
        headers["Authorization"] = f"Bearer {token}"
        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

        client = self._client or httpx.AsyncClient()
        checks: list[VerificationCheck] = []

        try:
            resp = await client.request(
                method=method,
                url=url,
                headers=headers,
                json=body if body is not None else None,
                timeout=15.0,
            )

            passed = 200 <= resp.status_code < 300
            resp_content: Any = None
            try:
                resp_content = resp.json()
            except Exception:
                resp_content = resp.text

            checks.append(
                VerificationCheck(
                    check="admin_action_status_code_2xx",
                    passed=passed,
                    evidence={"status_code": resp.status_code, "response": resp_content},
                )
            )

            if not passed:
                return ExecutionResult(
                    ok=False,
                    before=None,
                    after={"status_code": resp.status_code, "response": resp_content},
                    verification=checks,
                    reverted=False,
                    error=f"Administrative operation returned non-2xx status: {resp.status_code}",
                )

            return ExecutionResult(
                ok=True,
                before=None,
                after={"status_code": resp.status_code, "response": resp_content},
                verification=checks,
            )
        except Exception as e:
            logger.exception("Error executing admin_action against %s", url)
            return ExecutionResult(
                ok=False,
                before=None,
                after=None,
                verification=checks,
                reverted=False,
                error=str(e),
            )
        finally:
            if self._client is None:
                await client.aclose()


__all__ = ["TenantAdminExecutor"]
