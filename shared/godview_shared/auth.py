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


_SA_UNIQUE_ID_CACHE: dict[str, str] = {}  # email -> uniqueId
_UNIQUE_ID_TO_EMAIL_CACHE: dict[str, str] = {}  # uniqueId -> email


def _resolve_sa_unique_id(sa_email: str) -> str | None:
    """Resolve a service account email to its numeric uniqueId via IAM serviceAccounts.get."""
    if not sa_email or "@" not in sa_email:
        return None
    if sa_email in _SA_UNIQUE_ID_CACHE:
        return _SA_UNIQUE_ID_CACHE[sa_email]

    # Check environment variable mappings first if configured
    # Format: GODVIEW_PRINCIPAL_UNIQUE_IDS="105657632617296969198=godview-runtime-sa@...,..."
    env_ids = os.environ.get("GODVIEW_PRINCIPAL_UNIQUE_IDS", "")
    if env_ids:
        for pair in env_ids.split(","):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                k, v = k.strip(), v.strip()
                if "@" in v:
                    _UNIQUE_ID_TO_EMAIL_CACHE[k] = v
                    _SA_UNIQUE_ID_CACHE[v] = k
                elif "@" in k:
                    _SA_UNIQUE_ID_CACHE[k] = v
                    _UNIQUE_ID_TO_EMAIL_CACHE[v] = k
        if sa_email in _SA_UNIQUE_ID_CACHE:
            return _SA_UNIQUE_ID_CACHE[sa_email]

    try:
        import httpx
        from google.auth import default as google_auth_default
        from google.auth.transport.requests import Request as GoogleAuthRequest

        credentials, _ = google_auth_default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        auth_req = GoogleAuthRequest()
        credentials.refresh(auth_req)
        token = credentials.token
        if not token:
            return None

        url = f"https://iam.googleapis.com/v1/projects/-/serviceAccounts/{sa_email}"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                unique_id = str(data.get("uniqueId") or data.get("oauth2ClientId") or "")
                if unique_id:
                    _SA_UNIQUE_ID_CACHE[sa_email] = unique_id
                    _UNIQUE_ID_TO_EMAIL_CACHE[unique_id] = sa_email
                    return unique_id
    except Exception as exc:
        logger.debug("Could not resolve SA uniqueId for %s via IAM: %s", sa_email, exc)
    return None


_BUNDLED_GOOGLE_CERTS_BYTES = b"""{
  "f10f87405a979c1df36df26606734f33cd85c271": "-----BEGIN CERTIFICATE-----\\nMIIDJjCCAg6gAwIBAgIIdM/oBOCyKewwDQYJKoZIhvcNAQEFBQAwNjE0MDIGA1UE\\nAwwrZmVkZXJhdGVkLXNpZ25vbi5zeXN0ZW0uZ3NlcnZpY2VhY2NvdW50LmNvbTAe\\nFw0yNjAzMzEyMjQ5MTBaFw0yNzAzMzEyMjQ5MTBaMDYxNDAyBgNVBAMMK2ZlZGVy\\nYXRlZC1zaWdub24uc3lzdGVtLmdzZXJ2aWNlYWNjb3VudC5jb20wggEiMA0GCSqG\\nSIb3DQEBAQUAA4IBDwAwggEKAoIBAQDitjm7BkrV1D5RWAHmzg0vIL67ksLYxPtv\\nwZZnO2I2AyynQ2erHSkzs5/HTCzfY3ZplzHXT4nzJ1DGc1v24Bx/7svKHI4hy/9Y\\nTLNpJtQAw6CgX6dCYl61yTE2Dp6SE19oVxNBLRP7b2qXb8IEsgwvoK9KH6vR+BZG\\nyo3ZrIHvAKXvga/o45Vu9qBRaGPnPYvqj+mWaw8PAtW68DujdYMQOOir4hdp9FCj\\nCP3aRBR6es4oPhgKim4sgGF6XpUfx2gVE1noohJCExw6K9ZotT9S0ooAyWgYmJ5N\\n//ev7+cSuzaHnAHtPsWswJJyY7ua8VBcqHz/g/4yVt+BL0JS9CSzAgMBAAGjODA2\\nMAwGA1UdEwEB/wQCMAAwDgYDVR0PAQH/BAQDAgeAMBYGA1UdJQEB/wQMMAoGCCsG\\nAQUFBwMCMA0GCSqGSIb3DQEBBQUAA4IBAQAhBvKdlNwFJdLgScW1v2DbnG2wreu1\\nGaHHQAIjq03fb5kJpFreqsavYsCRpFq27jV33O+GB+r4rvi8iB5uHAtmfxAxE1xv\\nbYYkg/LOgYH/BJS8mYS3aFgUp817e4qFuXBNReJ/yAbKuXQiZVyaeV0WQeokzIz9\\n0HSoPigQp0oMjDxqMWs97ILS3hbrvIAkPWl6CujVRdxRV6w0jbowdZjwoZ2byB7O\\nfuNo/dTKQ7G8aqNdVwy6WT2kX5AE/XKim4ITGzlc5xNkHkSijZBXm7fV2QU85Mtd\\nmWW1mBGwD3uFSk/7OlxFr1EPtmAzs2W1H1N5S6Ev/7gBhB+/UwF+ptDa\\n-----END CERTIFICATE-----\\n",
  "943a3a5d7d919625a454e489b75c29adab57acba": "-----BEGIN CERTIFICATE-----\\nMIIDJzCCAg+gAwIBAgIJAJv8YZctwxPMMA0GCSqGSIb3DQEBBQUAMDYxNDAyBgNV\\nBAMMK2ZlZGVyYXRlZC1zaWdub24uc3lzdGVtLmdzZXJ2aWNlYWNjb3VudC5jb20w\\nHhcNMjYwMzMxMjI0OTEwWhcNMjcwMzMxMjI0OTEwWjA2MTQwMgYDVQQDDCtmZWRl\\ncmF0ZWQtc2lnbm9uLnN5c3RlbS5nc2VydmljZWFjY291bnQuY29tMIIBIjANBgkq\\nhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEApIpnzA2ezyEERJSxiqpLBmMeIqATH+V6\\niuBtKIibXEyYovujrx8niqTeO6RIyXT6uDUUv0V2kJ8V/iWYFxzXY1BqK9IfcAmj\\ng0XUDoyTVkoyLsF0gj299LH+zw5vCvy8jmamFIZKAbKcQ5hpHvSittM1vl+6vVL+\\ni2GxyGbMA9aY6Hq15NylS1t7ELTYfQimlnvxcb7/DM0cuS5U1SfbCZMCpKhh0nrS\\nlYds240oxpCJOV2rBahs/Ea5c7tezS1nwVC9W/E+bR9TF6BHkC/fv+E8DcWfkI/6\\ngeaJzBhINNxBfjx+w1+WUp2Jz3YYFWEfeQjxMqu+Fg6cGxwk7V16uQIDAQABozgw\\nNjAMBgNVHRMBAf8EAjAAMA4GA1UdDwEB/wQEAwIHgDAWBgNVHSUBAf8EDDAKBggr\\nBgEFBQcDAjANBgkqhkiG9w0BAQUFAAOCAQEAM775NpEtQ5iTVgLNZmzkwy1fgi9d\\nCAkZdJs0K14rR+4VBGhrua0HOSS7dZv+3MBkLIP/2B6s+enQgW26nuNx3xlJKQmk\\nD82sGZ0eGmXSS0U6Lzwr31zRKVm0wFbJU7Q/0LDpQ9gfnheZ7K9hrkuHOlr+EQtG\\njFJRHidxK7m+e/ogbEWgqzLNSAv60USs8mcZmBNLRWTKuAfisVm3v6FKALCv3bSC\\nNc1d1kzytBRaDsgZgy5IxOpbvy79vD9FbMjB6ZcjRrIKaU8TFAkLY1D14EZSB5oQ\\nGVqAoYmgQ6xs6Im9HRFfLPTyL54D/narTMqIGR6G6jcV4/Y0MQqJP1M7yQ==\\n-----END CERTIFICATE-----\\n"
}"""


class _HttpxAuthResponse:
    def __init__(self, r: Any) -> None:
        self.status = r.status_code
        self.headers = dict(r.headers)
        self.data = r.content


class _HttpxAuthRequest:
    """Robust transport request supporting both network and offline fallback Google cert validation."""

    def __init__(self) -> None:
        import httpx
        self._client = httpx.Client(timeout=3.0)

    def __call__(
        self,
        url: str,
        method: str = "GET",
        body: Any = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        if "certs" in url:
            try:
                resp = self._client.request(
                    method=method,
                    url=url,
                    content=body,
                    headers=headers,
                    timeout=timeout or 2.0,
                )
                if resp.status_code == 200 and resp.content:
                    return _HttpxAuthResponse(resp)
                logger.warning(
                    "Network certs fetch to %s returned %s; using bundled Google certs",
                    url,
                    resp.status_code,
                )
            except Exception as exc:
                logger.warning(
                    "Network certs fetch to %s failed (%s); using bundled Google certs",
                    url,
                    exc,
                )

            class _CertsResp:
                status = 200
                headers = {"content-type": "application/json"}
                data = _BUNDLED_GOOGLE_CERTS_BYTES

            return _CertsResp()

        resp = self._client.request(
            method=method,
            url=url,
            content=body,
            headers=headers,
            timeout=timeout or 5.0,
        )
        return _HttpxAuthResponse(resp)


def get_google_auth_request() -> Any:
    """Return a google.auth.transport.Request compatible object."""
    return _HttpxAuthRequest()


def decode_cloud_run_stripped_jwt(raw_tok: str, request: Any = None) -> dict[str, Any] | None:
    """If running in Cloud Run (K_SERVICE set) and token signature was stripped by GFE, decode payload."""
    if not os.environ.get("K_SERVICE"):
        return None
    if raw_tok.lower().startswith("bearer "):
        raw_tok = raw_tok.split(" ", 1)[1].strip()

    parts = raw_tok.split(".")
    if len(parts) != 3 or parts[2] != "SIGNATURE_REMOVED_BY_GOOGLE":
        return None

    try:
        import base64
        import json
        import time

        payload_b64 = parts[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload_bytes = base64.urlsafe_b64decode(payload_b64.encode("utf-8"))
        claims = json.loads(payload_bytes.decode("utf-8"))
        if not isinstance(claims, dict):
            return None
        # Enforce audience: Cloud Run only strips signatures for the Google Cloud SDK client ID
        aud = str(claims.get("aud") or "")
        if aud != "32555940559.apps.googleusercontent.com":
            logger.warning("Rejected Cloud Run stripped token with non-gcloud audience: %s", aud)
            return None
        exp = claims.get("exp")
        if exp is not None and float(exp) < time.time():
            logger.warning("Cloud Run stripped token has expired (exp=%s)", exp)
            return None
        return claims
    except Exception as exc:
        logger.warning("Failed to decode Cloud Run stripped JWT: %s", exc)
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
        raw_trace = [
            s.strip()
            for s in os.environ.get("GODVIEW_TRACE_PRINCIPALS", "").split(",")
            if s.strip()
        ]
        self._allowed_trace_principals = set(raw_trace)
        self._allowed_service_principals = set(raw_principals)
        raw_ops = [
            s.strip().lower()
            for s in (
                os.environ.get("GODVIEW_OPERATOR_EMAILS", "")
                or os.environ.get("GODVIEW_OPERATOR_EMAIL", "")
            ).split(",")
            if s.strip()
        ]
        self._allowed_operator_emails = set(raw_ops)
        self._auth_request: Any = None

    def _get_auth_request(self) -> Any:
        if self._auth_request is None:
            self._auth_request = get_google_auth_request()
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

        # In hermetic and local modes, allow test principal headers
        if self.settings.env_mode in ("hermetic", "local"):
            if test_principal_hdr:
                try:
                    data = json.loads(test_principal_hdr)
                    return Principal(
                        email=data.get("email", "operator@hermetic.test"),
                        subject=data.get("sub", "sub-hermetic"),
                        issuer=self.settings.env_mode,
                        audience=self.settings.env_mode,
                        claims=data,
                        is_operator=True,
                    )
                except Exception:
                    return Principal(
                        email=test_principal_hdr,
                        subject="sub-hermetic",
                        issuer=self.settings.env_mode,
                        audience=self.settings.env_mode,
                        is_operator=True,
                    )
            if legacy_email_hdr:
                clean_email = legacy_email_hdr.split(":")[-1].strip()
                return Principal(
                    email=clean_email,
                    subject=f"sub-{clean_email}",
                    issuer=self.settings.env_mode,
                    audience=self.settings.env_mode,
                    is_operator=True,
                )
            if token and token.startswith("hermetic-"):
                return Principal(
                    email="operator@local.dev",
                    subject="sub-operator-local",
                    issuer=self.settings.env_mode,
                    audience=self.settings.env_mode,
                    is_operator=True,
                )
            return Principal(
                email="operator@hermetic.test",
                subject="sub-operator-hermetic",
                issuer=self.settings.env_mode,
                audience=self.settings.env_mode,
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
        *,
        allowed_principals: set[str] | None = None,
    ) -> Principal | None:
        """Verify a Google OIDC ID token and return the service Principal."""
        token: str | None = None
        test_principal_hdr: str | None = None

        if isinstance(request_or_token, Request):
            auth_header = (
                request_or_token.headers.get("authorization", "")
                or request_or_token.headers.get("x-serverless-authorization", "")
            )
            if auth_header.lower().startswith("bearer "):
                token = auth_header.split(" ", 1)[1].strip()
            test_principal_hdr = request_or_token.headers.get("x-godview-test-principal")
        else:
            token = request_or_token

        effective_audience = (
            audience
            or os.environ.get("GODVIEW_SOVEREIGN_URL")
            or os.environ.get("SERVICE_URL")
            or getattr(self.settings, "service_url", "")
        )

        if self.settings.env_mode in ("hermetic", "local"):
            if test_principal_hdr:
                return Principal(
                    email=test_principal_hdr,
                    subject="sub-service-test",
                    issuer=self.settings.env_mode,
                    audience=effective_audience or self.settings.env_mode,
                    is_service=True,
                )
            if token and (token.startswith("hermetic-") or token.startswith("local-")):
                return Principal(
                    email=f"godview-sense-sa@{self.settings.env_mode}.iam.gserviceaccount.com",
                    subject=f"sub-service-{self.settings.env_mode}",
                    issuer=self.settings.env_mode,
                    audience=effective_audience or self.settings.env_mode,
                    is_service=True,
                )
            return Principal(
                email=f"godview-sense-sa@{self.settings.env_mode}.iam.gserviceaccount.com",
                subject=f"sub-service-{self.settings.env_mode}",
                issuer=self.settings.env_mode,
                audience=effective_audience or self.settings.env_mode,
                is_service=True,
            )

        if not token:
            return None

        candidate_audiences: list[str] = []
        if effective_audience:
            candidate_audiences.append(effective_audience.rstrip("/"))
        if isinstance(request_or_token, Request):
            req_base = str(request_or_token.base_url).rstrip("/")
            if req_base and req_base not in candidate_audiences:
                candidate_audiences.append(req_base)

        if not candidate_audiences:
            logger.error("OIDC verification failed: audience is required")
            return None

        try:
            from google.oauth2 import id_token

            req = self._get_auth_request()
            claims = None
            verified_aud = ""
            for aud in candidate_audiences:
                try:
                    claims = id_token.verify_oauth2_token(
                        token, req, audience=aud
                    )
                    verified_aud = aud
                    break
                except Exception:
                    continue

            if not claims and token:
                claims = decode_cloud_run_stripped_jwt(
                    token,
                    request=request_or_token if isinstance(request_or_token, Request) else None,
                )

            if not claims:
                return None

            iss = claims.get("iss", "")
            if iss not in (GOOGLE_OIDC_ISSUER, "accounts.google.com"):
                return None

            email = str(claims.get("email") or "")
            sub = str(claims.get("sub") or "")
            azp = str(claims.get("azp") or "")
            effective_subject = sub or azp

            effective_allowed = (
                allowed_principals
                if allowed_principals is not None
                else self._allowed_service_principals
            )
            # Strict service principal allowlist (Decision #89/#91/#95, Plan §0 fail closed)
            if not effective_allowed:
                logger.warning(
                    "OIDC rejected: no service principal allowlist configured (fail closed, no silent fallback)"
                )
                return None

            # Check email or uniqueId / sub / azp match against allowlist
            matched = False
            matched_email = email
            if email and email in effective_allowed:
                matched = True
                matched_email = email
                if effective_subject:
                    _UNIQUE_ID_TO_EMAIL_CACHE[effective_subject] = email
                    _SA_UNIQUE_ID_CACHE[email] = effective_subject
            elif effective_subject and effective_subject in effective_allowed:
                matched = True
                matched_email = _UNIQUE_ID_TO_EMAIL_CACHE.get(effective_subject) or email or effective_subject
            elif effective_subject:
                cached_email = _UNIQUE_ID_TO_EMAIL_CACHE.get(effective_subject)
                if cached_email and cached_email in effective_allowed:
                    matched = True
                    matched_email = cached_email
                else:
                    for allowed_p in effective_allowed:
                        if "@" in allowed_p:
                            uid = _resolve_sa_unique_id(allowed_p)
                            if uid and uid == effective_subject:
                                matched = True
                                matched_email = allowed_p
                                break

            if not matched:
                logger.warning(
                    "OIDC rejected: neither email '%s' nor subject '%s' in allowed principals %s",
                    email,
                    effective_subject,
                    effective_allowed,
                )
                return None

            aud = claims.get("aud", verified_aud)
            return Principal(
                email=matched_email,
                subject=sub,
                issuer=iss,
                audience=aud,
                claims=claims,
                is_service=True,
            )
        except Exception:
            return None

    async def require_operator(self, request: Request) -> Principal:
        """FastAPI dependency requiring an authenticated human operator (via IAP JWT or Bearer ID token)."""
        principal = self.verify_iap_jwt(request)
        if principal is not None and principal.email:
            email = str(principal.email).strip().lower()
            if email.endswith(".gserviceaccount.com"):
                logger.warning("Rejected service account '%s' in require_operator", email)
                raise HTTPException(
                    status_code=403,
                    detail="Forbidden: Service accounts cannot act as human operators",
                )
            if self._allowed_operator_emails and email not in self._allowed_operator_emails:
                logger.warning("Operator email '%s' from IAP not in allowlist", email)
                raise HTTPException(
                    status_code=403,
                    detail=f"Forbidden: Operator '{email}' is not in the operator allowlist",
                )
            return principal

        # If IAP header is absent, check Bearer ID token (e.g. gcloud auth print-identity-token)
        auth_header = (
            request.headers.get("authorization", "")
            or request.headers.get("x-serverless-authorization", "")
        )
        if auth_header.lower().startswith("bearer "):
            raw_tok = auth_header.split(" ", 1)[1].strip()
            claims = None
            try:
                from google.oauth2 import id_token as google_id_token

                claims = google_id_token.verify_oauth2_token(
                    raw_tok, get_google_auth_request()
                )
            except Exception as e:
                logger.error(
                    "Bearer token verification exception in require_operator (%s): %s",
                    type(e).__name__,
                    e,
                    exc_info=True,
                )
                claims = decode_cloud_run_stripped_jwt(raw_tok, request=request)
                if claims is None:
                    logger.error(
                        "Fallback decode_cloud_run_stripped_jwt also returned None in require_operator"
                    )

            try:
                if claims and claims.get("iss") in (
                    "https://accounts.google.com",
                    "accounts.google.com",
                ):
                    email = str(claims.get("email") or "").strip().lower()
                    # Reject service accounts: operators must be human identities
                    if email.endswith(".gserviceaccount.com"):
                        logger.warning("Rejected service account '%s' in require_operator", email)
                        raise HTTPException(
                            status_code=403,
                            detail="Forbidden: Service accounts cannot act as human operators",
                        )
                    # Check email verification
                    if not claims.get("email_verified", True):
                        logger.warning("Rejected unverified email '%s' in require_operator", email)
                        raise HTTPException(
                            status_code=401,
                            detail="Unauthorized: Email must be verified",
                        )
                    # Enforce operator allowlist: when allowlist is set, it alone decides
                    if self._allowed_operator_emails and email not in self._allowed_operator_emails:
                        logger.warning("Operator email '%s' not in allowlist", email)
                        raise HTTPException(
                            status_code=403,
                            detail=f"Forbidden: Operator '{email}' is not in the operator allowlist",
                        )

                    if email:
                        return Principal(
                            email=email,
                            subject=str(claims.get("sub") or ""),
                            issuer=str(claims.get("iss") or ""),
                            audience=str(claims.get("aud") or ""),
                            claims=claims,
                            is_operator=True,
                        )
            except HTTPException:
                raise

        if self.settings.env_mode in ("hermetic", "local"):
            email = _clean_iap_email(request.headers.get("x-goog-authenticated-user-email")) or "operator@local.dev"
            return Principal(
                email=email,
                subject="operator-local",
                issuer=self.settings.env_mode,
                audience="local",
            )

        raise HTTPException(
            status_code=401,
            detail="Unauthorized: valid operator identity (IAP JWT or Bearer ID token) required",
        )

    require_operator_identity = require_operator

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

    def require_trace_service(self) -> Any:
        """FastAPI dependency for trace ingestion (OIDC from trace principals)."""
        async def _dependency(request: Request) -> Principal:
            trace_allowed = self._allowed_trace_principals or self._allowed_service_principals
            principal = self.verify_google_oidc(request, allowed_principals=trace_allowed)
            if principal is None:
                raise HTTPException(
                    status_code=401,
                    detail="Unauthorized: valid trace service OIDC token required",
                )
            if self.settings.env_mode != "hermetic":
                allowed = (
                    principal.email in trace_allowed
                    or principal.subject in trace_allowed
                    or (principal.subject and _UNIQUE_ID_TO_EMAIL_CACHE.get(principal.subject) in trace_allowed)
                    or (principal.email and _SA_UNIQUE_ID_CACHE.get(principal.email) in trace_allowed)
                )
                if trace_allowed and not allowed:
                    raise HTTPException(
                        status_code=403,
                        detail=f"Forbidden: trace sender {principal.email} not in trace principals",
                    )
            return principal

        return _dependency

    async def optional_principal(self, request: Request) -> Principal | None:
        """FastAPI dependency returning the authenticated principal if any, or None."""
        op = self.verify_iap_jwt(request)
        if op is not None:
            return op
        svc = self.verify_google_oidc(request)
        if svc is not None:
            return svc
        return None
