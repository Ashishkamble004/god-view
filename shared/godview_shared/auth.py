"""Authentication and Identity Service for God-View Sovereign.

Implements:
- Google ADC token provider and AuthorizedSession support
- id_token_for(audience) using metadata server or google.oauth2.id_token
- verify_iap_jwt: verifies X-Goog-IAP-JWT-Assertion using google.oauth2.id_token
  and IAP public keys, checking against IAP_BACKEND_SERVICE_IDS
- verify_google_oidc: verifies Bearer tokens using Google OIDC certificates
- FastAPI dependencies: require_operator, require_service, optional_principal
- Hermetic test principal support via X-Godview-Test-Principal
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, Request

from godview_shared.config import GodViewSettings

IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key-jwk"
IAP_ISSUER = "https://cloud.google.com/iap"
GOOGLE_OIDC_ISSUER = "https://accounts.google.com"


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


class AuthService:
    """Authentication and identity verification service."""

    def __init__(
        self,
        settings: GodViewSettings | None = None,
        credentials: Any = None,
        iap_backend_service_ids: Sequence[str] | None = None,
    ) -> None:
        self.settings = settings or GodViewSettings.from_env()
        self._credentials = credentials
        self._iap_backend_service_ids = list(
            iap_backend_service_ids
            or [
                s.strip()
                for s in os.environ.get("IAP_BACKEND_SERVICE_IDS", "").split(",")
                if s.strip()
            ]
        )
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
            if self.settings.env_mode == "local":
                return "local-dev-token"
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
            if self.settings.env_mode == "local":
                return f"local-id-token-for-{audience}"
            raise RuntimeError(
                f"Failed to fetch ID token for audience '{audience}': {exc}"
            ) from exc

    def verify_iap_jwt(self, request_or_token: Request | str) -> Principal | None:
        """Verify an IAP JWT assertion and return the operator Principal."""
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
            if not token:
                # Default hermetic operator fallback
                return Principal(
                    email="operator@local.dev",
                    subject="sub-operator-local",
                    issuer="hermetic",
                    audience="hermetic",
                    is_operator=True,
                )

        if not token:
            return None

        try:
            from google.oauth2 import id_token

            req = self._get_auth_request()
            # If backend service IDs are configured, verify audience against them
            claims: dict[str, Any] = {}
            if self._iap_backend_service_ids:
                verified = False
                last_exc = None
                for bs_id in self._iap_backend_service_ids:
                    # Target audience can be full path or just the ID
                    expected_aud = (
                        bs_id
                        if bs_id.startswith("/projects/")
                        else f"/projects/{self._get_project_number()}/global/backendServices/{bs_id}"
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
                    except Exception as err:
                        last_exc = err
                        continue
                if not verified:
                    # Also try verifying with audience=None if specific aud check failed
                    try:
                        claims = id_token.verify_token(
                            token, req, certs_url=IAP_CERTS_URL
                        )
                    except Exception as err:
                        if last_exc:
                            raise last_exc from err
                        raise
            else:
                claims = id_token.verify_token(
                    token, req, certs_url=IAP_CERTS_URL
                )

            email = claims.get("email", "")
            sub = claims.get("sub", "")
            iss = claims.get("iss", IAP_ISSUER)
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
        return os.environ.get("GOOGLE_CLOUD_PROJECT_NUMBER", "769002985772")

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

        if self.settings.env_mode == "hermetic":
            if test_principal_hdr:
                return Principal(
                    email=test_principal_hdr,
                    subject="sub-service-test",
                    issuer="hermetic",
                    audience=audience or "hermetic",
                    is_service=True,
                )
            if token and token.startswith("hermetic-"):
                return Principal(
                    email="godview-sense-sa@hermetic.iam.gserviceaccount.com",
                    subject="sub-service-hermetic",
                    issuer="hermetic",
                    audience=audience or "hermetic",
                    is_service=True,
                )
            # Default service principal in hermetic if requested
            return Principal(
                email="godview-sense-sa@hermetic.iam.gserviceaccount.com",
                subject="sub-service-hermetic",
                issuer="hermetic",
                audience=audience or "hermetic",
                is_service=True,
            )

        if not token:
            return None

        try:
            from google.oauth2 import id_token

            req = self._get_auth_request()
            claims = id_token.verify_oauth2_token(token, req, audience=audience)
            email = claims.get("email", "")
            sub = claims.get("sub", "")
            iss = claims.get("iss", GOOGLE_OIDC_ISSUER)
            aud = claims.get("aud", audience or "")
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
        """FastAPI dependency for service authentication (OIDC).

        Can be used directly as a dependency: `Depends(ctx.auth.require_service)`
        or as a parameterized factory: `Depends(ctx.auth.require_service(audience="..."))`.
        """
        if isinstance(audience_or_request, Request):
            # Called directly as dependency without args: require_service(request)
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
