"""Authentication and Identity Service for God-View Sovereign.

Implements:
- Google ADC token provider and AuthorizedSession support
- id_token_for(audience) using metadata server or google.oauth2.id_token
- verify_iap_jwt: verifies X-Goog-IAP-JWT-Assertion using google.oauth2.id_token
  and IAP public keys, strictly validating audience against IAP_BACKEND_SERVICE_IDS
  and issuer == IAP_ISSUER
- verify_google_oidc: verifies Bearer tokens using Google OIDC certificates,
  validating audience and allowed service account principals
- FastAPI dependencies: require_operator, require_service, optional_principal
- Hermetic test principal support via X-Godview-Test-Principal and X-Goog-Authenticated-User-Email
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, Request

from godview_shared.config import GodViewSettings

logger = logging.getLogger("godview.auth")

IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key-jwk"
IAP_ISSUER = "https://cloud.google.com/iap"
GOOGLE_OIDC_ISSUER = "https://accounts.google.com"
METADATA_HEADER = {"Metadata-Flavor": "Google"}


@dataclass
class Principal:
    """Authenticated identity representing a human operator or service account."""

    email: str
    subject: str = ""
    issuer: str = ""
    audience: str = ""
    claims: dict[str, Any] = field(default_factory=dict)
    is_operator: bool = False
    is_service: bool = False


def _fetch_metadata(path: str) -> str | None:
    """Fetch metadata from the Google Cloud metadata server."""
    import urllib.error
    import urllib.request

    url = f"http://metadata.google.internal/computeMetadata/v1/{path.lstrip('/')}"
    try:
        req = urllib.request.Request(url, headers=METADATA_HEADER)
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.read().decode("utf-8").strip()
    except Exception:
        return None


class AuthService:
    """Authentication and identity verification service."""

    def __init__(
        self,
        settings: GodViewSettings | None = None,
        credentials: Any = None,
        iap_backend_service_ids: Sequence[str] | None = None,
        service_principals: Sequence[str] | None = None,
    ) -> None:
        self.settings = settings or GodViewSettings.from_env()
        self._credentials = credentials
        raw_iap_ids = (
            iap_backend_service_ids
            if iap_backend_service_ids is not None
            else [
                s.strip()
                for s in os.environ.get("IAP_BACKEND_SERVICE_IDS", "").split(",")
                if s.strip()
            ]
        )
        self._iap_backend_service_ids = list(raw_iap_ids)

        raw_principals = (
            service_principals
            if service_principals is not None
            else [
                s.strip()
                for s in os.environ.get("GODVIEW_SERVICE_PRINCIPALS", "").split(",")
                if s.strip()
            ]
        )
        act_sa = os.environ.get("GODVIEW_ACT_SA_EMAIL")
        if act_sa and act_sa not in raw_principals:
            raw_principals.append(act_sa)
        self._allowed_service_principals = set(raw_principals)
        self._auth_request: Any = None

    def _get_auth_request(self) -> Any:
        if self._auth_request is None:
            try:
                from google.auth.transport.requests import Request as GoogleAuthRequest

                self._auth_request = GoogleAuthRequest()
            except ImportError:
                self._auth_request = None
        return self._auth_request

    def access_token(self) -> str:
        """Return a valid Google Cloud OAuth2 access token via ADC."""
        if self.settings.env_mode == "hermetic":
            return "hermetic-access-token"

        try:
            import google.auth

            if self._credentials is None:
                self._credentials, _ = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
            if not self._credentials.valid:
                req = self._get_auth_request()
                self._credentials.refresh(req)
            return str(self._credentials.token)
        except Exception as exc:
            raise RuntimeError(f"Failed to obtain access token via ADC: {exc}") from exc

    def id_token_for(self, audience: str) -> str:
        """Fetch a Google OIDC ID token for the specified audience."""
        if self.settings.env_mode == "hermetic":
            return f"hermetic-id-token-for-{audience}"

        try:
            req = self._get_auth_request()
            from google.oauth2 import id_token

            return id_token.fetch_id_token(req, audience)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to fetch ID token for audience '{audience}': {exc}"
            ) from exc

    def verify_iap_jwt(self, request_or_token: Request | str) -> Principal | None:
        """Verify an IAP JWT assertion and return the operator Principal.

        Enforces:
        - Must be present
        - Signature verified against Google IAP JWKs
        - Issuer must equal https://cloud.google.com/iap
        - Audience must match one of IAP_BACKEND_SERVICE_IDS (fail closed if none match or none configured)
        """
        token: str | None = None
        test_principal_hdr: str | None = None
        legacy_email_hdr: str | None = None

        if isinstance(request_or_token, Request):
            token = request_or_token.headers.get("x-goog-iap-jwt-assertion")
            test_principal_hdr = request_or_token.headers.get("x-godview-test-principal")
            legacy_email_hdr = request_or_token.headers.get(
                "x-goog-authenticated-user-email"
            )
        else:
            token = request_or_token

        # In hermetic mode, allow test principal headers
        if self.settings.env_mode == "hermetic":
            if test_principal_hdr:
                try:
                    data = json.loads(test_principal_hdr)
                    return Principal(
                        email=data.get("email", "operator@hermetic.test"),
                        subject=data.get("sub", "sub-hermetic"),
                        issuer="hermetic",
                        audience="hermetic",
                        claims=data,
                        is_operator=True,
                    )
                except Exception:
                    return Principal(
                        email=test_principal_hdr,
                        subject="sub-hermetic",
                        issuer="hermetic",
                        audience="hermetic",
                        is_operator=True,
                    )
            if legacy_email_hdr:
                clean_email = legacy_email_hdr.split(":")[-1].strip()
                return Principal(
                    email=clean_email,
                    subject=f"sub-{clean_email}",
                    issuer="hermetic",
                    audience="hermetic",
                    is_operator=True,
                )
            if token and token.startswith("hermetic-"):
                return Principal(
                    email="operator@local.dev",
                    subject="sub-operator-local",
                    issuer="hermetic",
                    audience="hermetic",
                    is_operator=True,
                )
            return Principal(
                email="operator@hermetic.test",
                subject="sub-operator-hermetic",
                issuer="hermetic",
                audience="hermetic",
                is_operator=True,
            )

        # In non-hermetic modes, fail closed if token or backend IDs are missing
        if not token:
            return None

        if not self._iap_backend_service_ids:
            logger.error("IAP verification failed closed: IAP_BACKEND_SERVICE_IDS is empty")
            return None

        project_number = self._get_project_number()

        try:
            from google.oauth2 import id_token

            req = self._get_auth_request()
            claims: dict[str, Any] = {}
            verified = False

            for bs_id in self._iap_backend_service_ids:
                expected_aud = (
                    bs_id
                    if bs_id.startswith("/projects/")
                    else (
                        f"/projects/{project_number}/global/backendServices/{bs_id}"
                        if project_number
                        else bs_id
                    )
                )
                try:
                    claims = id_token.verify_token(
                        token,
                        req,
                        audience=expected_aud,
                        certs_url=IAP_CERTS_URL,
                    )
                    verified = True
                    break
                except Exception:
                    continue

            if not verified:
                return None

            iss = claims.get("iss", "")
            if iss != IAP_ISSUER:
                logger.warning("IAP token rejected: invalid issuer %s", iss)
                return None

            email = claims.get("email", "")
            sub = claims.get("sub", "")
            aud = claims.get("aud", "")
            return Principal(
                email=email,
                subject=sub,
                issuer=iss,
                audience=aud,
                claims=claims,
                is_operator=True,
            )
        except Exception:
            return None

    def _get_project_number(self) -> str:
        env_num = os.environ.get("GOOGLE_CLOUD_PROJECT_NUMBER", "")
        if env_num:
            return env_num
        meta_num = _fetch_metadata("project/numeric-project-id")
        return meta_num or ""

    def verify_google_oidc(
        self,
        request_or_token: Request | str,
        audience: str | None = None,
    ) -> Principal | None:
        """Verify a Google OIDC ID token and return the service Principal."""
        token: str | None = None
        test_principal_hdr: str | None = None

        if isinstance(request_or_token, Request):
            auth_header = request_or_token.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header.split(" ", 1)[1].strip()
            test_principal_hdr = request_or_token.headers.get("x-godview-test-principal")
        else:
            token = request_or_token

        effective_audience = (
            audience
            or os.environ.get("GODVIEW_SOVEREIGN_URL")
            or os.environ.get("SERVICE_URL")
        )

        if self.settings.env_mode == "hermetic":
            if test_principal_hdr:
                return Principal(
                    email=test_principal_hdr,
                    subject="sub-service-test",
                    issuer="hermetic",
                    audience=effective_audience or "hermetic",
                    is_service=True,
                )
            if token and token.startswith("hermetic-"):
                return Principal(
                    email="godview-sense-sa@hermetic.iam.gserviceaccount.com",
                    subject="sub-service-hermetic",
                    issuer="hermetic",
                    audience=effective_audience or "hermetic",
                    is_service=True,
                )
            return None

        if not token:
            return None

        if not effective_audience:
            logger.error("OIDC verification failed: audience is required")
            return None

        try:
            from google.oauth2 import id_token

            req = self._get_auth_request()
            claims = id_token.verify_oauth2_token(
                token, req, audience=effective_audience
            )

            iss = claims.get("iss", "")
            if iss not in (GOOGLE_OIDC_ISSUER, "accounts.google.com"):
                return None

            email = claims.get("email", "")
            # Verify against allowed service accounts
            if self._allowed_service_principals:
                if email not in self._allowed_service_principals:
                    # Also permit standard naming pattern: godview-*-sa@*.iam.gserviceaccount.com
                    if not (
                        email.startswith("godview-")
                        and email.endswith(".iam.gserviceaccount.com")
                    ):
                        logger.warning("OIDC rejected: email %s not in allowed principals", email)
                        return None
            else:
                if not (
                    email.startswith("godview-")
                    and email.endswith(".iam.gserviceaccount.com")
                ):
                    logger.warning(
                        "OIDC rejected: email %s does not match service principal pattern", email
                    )
                    return None

            sub = claims.get("sub", "")
            aud = claims.get("aud", effective_audience)
            return Principal(
                email=email,
                subject=sub,
                issuer=iss,
                audience=aud,
                claims=claims,
                is_service=True,
            )
        except Exception:
            return None

    async def require_operator(self, request: Request) -> Principal:
        """FastAPI dependency requiring an authenticated human operator (via IAP JWT)."""
        principal = self.verify_iap_jwt(request)
        if principal is None or not principal.email:
            raise HTTPException(
                status_code=401,
                detail="Unauthorized: valid operator identity (IAP JWT) required",
            )
        return principal

    def require_service(
        self,
        audience_or_request: Any = None,
    ) -> Any:
        """FastAPI dependency for service authentication (OIDC)."""
        if isinstance(audience_or_request, Request):
            return self._verify_service_dep(audience_or_request, audience=None)

        audience = audience_or_request if isinstance(audience_or_request, str) else None

        async def _dependency(request: Request) -> Principal:
            return await self._verify_service_dep(request, audience=audience)

        return _dependency

    async def _verify_service_dep(
        self,
        request: Request,
        audience: str | None = None,
    ) -> Principal:
        principal = self.verify_google_oidc(request, audience=audience)
        if principal is None:
            raise HTTPException(
                status_code=401,
                detail="Unauthorized: valid service OIDC token required",
            )
        return principal

    async def optional_principal(self, request: Request) -> Principal | None:
        """FastAPI dependency returning the authenticated principal if any, or None."""
        op = self.verify_iap_jwt(request)
        if op is not None:
            return op
        svc = self.verify_google_oidc(request)
        if svc is not None:
            return svc
        return None
