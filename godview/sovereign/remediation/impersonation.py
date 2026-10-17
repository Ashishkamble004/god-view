"""Service account impersonation helper for God-View remediation (Plan §3.4 & §3.8).

Mutating remediation actions must run as godview-act-sa, which holds the
necessary GCP roles (compute.securityAdmin, run.admin, container.admin, etc.),
while the Sovereign runtime itself runs as godview-sense-sa (viewer roles).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

IAM_CREDENTIALS_BASE = "https://iamcredentials.googleapis.com/v1"


def get_act_sa_email(project_id: str | None = None) -> str:
    """Return the service account email for godview-act-sa."""
    env_email = os.environ.get("GODVIEW_ACT_SA_EMAIL")
    if env_email:
        return env_email
    proj = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if proj:
        return f"godview-act-sa@{proj}.iam.gserviceaccount.com"
    return "godview-act-sa@unknown.iam.gserviceaccount.com"


async def get_act_sa_access_token(
    ctx: Any,
    project_id: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Obtain an OAuth2 access token impersonating godview-act-sa."""
    env_mode = (
        getattr(getattr(ctx, "settings", None), "env_mode", None)
        or getattr(ctx, "env_mode", None)
        or os.environ.get("GODVIEW_ENV", "")
    )
    if env_mode in ("hermetic", "test", "local") or not env_mode:
        return "hermetic-act-sa-access-token"

    # If ctx.auth has impersonation directly implemented
    if hasattr(ctx, "auth") and ctx.auth is not None:
        impersonate_fn = getattr(ctx.auth, "get_impersonated_access_token", None)
        if callable(impersonate_fn):
            res = impersonate_fn(get_act_sa_email(project_id))
            if hasattr(res, "__await__"):
                return str(await res)
            return str(res)

    # Base access token from Sovereign runtime SA (ADC)
    base_token = ""
    if hasattr(ctx, "auth") and ctx.auth is not None:
        token_fn = getattr(ctx.auth, "access_token", None)
        if callable(token_fn):
            base_token = str(token_fn())
    if not base_token or base_token.startswith("hermetic-") or "test" in base_token:
        return "hermetic-act-sa-access-token"

    act_sa = get_act_sa_email(project_id)
    url = f"{IAM_CREDENTIALS_BASE}/projects/-/serviceAccounts/{act_sa}:generateAccessToken"
    headers = {
        "Authorization": f"Bearer {base_token}",
        "Content-Type": "application/json",
    }
    body = {
        "scope": ["https://www.googleapis.com/auth/cloud-platform"],
    }

    http = client or httpx.AsyncClient()
    try:
        resp = await http.post(url, headers=headers, json=body, timeout=10.0)
        if resp.status_code != 200:
            raise RuntimeError(
                f"IAM Credentials API returned {resp.status_code} while impersonating {act_sa}: {resp.text}"
            )
        data = resp.json()
        token = data.get("accessToken")
        if not token:
            raise RuntimeError(f"IAM Credentials response missing accessToken: {data}")
        return str(token)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Network error impersonating {act_sa} via IAM Credentials: {exc}") from exc


async def get_act_sa_id_token(
    ctx: Any,
    audience: str,
    project_id: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Obtain an OIDC ID token impersonating godview-act-sa for audience."""
    env_mode = (
        getattr(getattr(ctx, "settings", None), "env_mode", None)
        or getattr(ctx, "env_mode", None)
        or os.environ.get("GODVIEW_ENV", "")
    )
    if env_mode in ("hermetic", "test", "local") or not env_mode:
        return f"hermetic-act-sa-id-token-for-{audience}"

    if hasattr(ctx, "auth") and ctx.auth is not None:
        impersonate_fn = getattr(ctx.auth, "get_impersonated_id_token", None)
        if callable(impersonate_fn):
            res = impersonate_fn(get_act_sa_email(project_id), audience)
            if hasattr(res, "__await__"):
                return str(await res)
            return str(res)

    base_token = ""
    if hasattr(ctx, "auth") and ctx.auth is not None:
        token_fn = getattr(ctx.auth, "access_token", None)
        if callable(token_fn):
            base_token = str(token_fn())
    if not base_token or base_token.startswith("hermetic-") or "test" in base_token:
        return f"hermetic-act-sa-id-token-for-{audience}"

    act_sa = get_act_sa_email(project_id)
    url = f"{IAM_CREDENTIALS_BASE}/projects/-/serviceAccounts/{act_sa}:generateIdToken"
    headers = {
        "Authorization": f"Bearer {base_token}",
        "Content-Type": "application/json",
    }
    body = {
        "audience": audience,
        "includeEmail": True,
    }

    http = client or httpx.AsyncClient()
    try:
        resp = await http.post(url, headers=headers, json=body, timeout=10.0)
        if resp.status_code != 200:
            raise RuntimeError(
                f"IAM Credentials API returned {resp.status_code} while minting ID token for {act_sa}: {resp.text}"
            )
        data = resp.json()
        token = data.get("token")
        if not token:
            raise RuntimeError(f"IAM Credentials response missing token: {data}")
        return str(token)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Network error minting ID token for {act_sa}: {exc}") from exc
