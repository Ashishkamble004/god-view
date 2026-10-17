"""Configuration and Google ADC token provider for Cymbal Auto Finance AI Customer Simulator."""

from __future__ import annotations

import os
import time
import urllib.parse
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal

import google.auth
import google.oauth2.id_token
import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from pydantic import BaseModel, Field

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class KycMode(StrEnum):
    """KYC verification mode (1 in 10 IMAGE, 9 in 10 TEXT_ONLY)."""

    IMAGE = "IMAGE"
    TEXT_ONLY = "TEXT_ONLY"


class UtilityType(StrEnum):
    """Cymbal Auto Finance loan utility type."""

    RETAIL_AUTO_LOAN = "RETAIL_AUTO_LOAN"
    COMMERCIAL_FLEET_TOPUP = "COMMERCIAL_FLEET_TOPUP"


class AdcTokenProvider:
    """Cached Application Default Credentials (ADC) access & OIDC ID token provider using google-auth."""

    def __init__(self, scopes: list[str] | None = None) -> None:
        self._scopes = scopes or [CLOUD_PLATFORM_SCOPE]
        self._credentials: Any = None
        self._auth_request = GoogleAuthRequest()
        self._cached_id_tokens: dict[str, tuple[str, float]] = {}

    def get_access_token(self) -> str:
        """Return a valid OAuth2 access token from ADC, refreshing only when expired/missing."""
        if self._credentials is None:
            self._credentials, _ = google.auth.default(scopes=self._scopes)
        if (
            not getattr(self._credentials, "valid", False)
            or getattr(self._credentials, "expired", False)
            or not getattr(self._credentials, "token", None)
        ):
            self._credentials.refresh(self._auth_request)
        token = getattr(self._credentials, "token", None)
        if not token or not isinstance(token, str):
            raise RuntimeError("Failed to obtain access token from google.auth ADC")
        return token

    def get_id_token(self, target_audience: str) -> str | None:
        """Fetch and cache a Google OIDC identity token for Cloud Run service-to-service calls."""
        parsed = urllib.parse.urlsplit(target_audience.strip())
        audience = (
            f"{parsed.scheme}://{parsed.netloc}"
            if parsed.scheme and parsed.netloc
            else target_audience.strip()
        )
        if not audience.startswith("https://"):
            return None

        now = time.monotonic()
        cached = self._cached_id_tokens.get(audience)
        if cached is not None and now < cached[1]:
            return cached[0] or None

        try:
            id_token = google.oauth2.id_token.fetch_id_token(self._auth_request, audience)
            if id_token and isinstance(id_token, str) and id_token.strip():
                clean_token = id_token.strip()
                self._cached_id_tokens[audience] = (clean_token, now + 3000.0)
                return clean_token
        except Exception:
            pass

        metadata_url = (
            "http://metadata.google.internal/computeMetadata/v1/instance/"
            f"service-accounts/default/identity?audience={urllib.parse.quote(audience, safe='')}"
        )
        try:
            with httpx.Client(timeout=2.0) as client:
                resp = client.get(metadata_url, headers={"Metadata-Flavor": "Google"})
                if resp.status_code == 200 and resp.text.strip():
                    clean_token = resp.text.strip()
                    self._cached_id_tokens[audience] = (clean_token, now + 3000.0)
                    return clean_token
        except httpx.HTTPError:
            pass

        self._cached_id_tokens[audience] = ("", now + 30.0)
        return None


def _normalize_api_base_url(raw_url: str) -> str:
    cleaned = raw_url.strip().rstrip("/")
    if cleaned.endswith("/api/v1"):
        cleaned = cleaned[: -len("/api/v1")]
    elif cleaned.endswith("/v1"):
        cleaned = cleaned[: -len("/v1")]
    return cleaned or "https://api.cymbal.ak-demos.com"


class SimulatorConfig(BaseModel):
    """Runtime configuration for ai-customer-simulator."""

    env_mode: Literal["gcp", "local", "hermetic"] = Field(default="gcp")
    api_base_url: str = Field(default="https://api.cymbal.ak-demos.com")
    kyc_vault_bucket: str = Field(default="general-ak-god-view-kyc-vault")
    state_object_name: str = Field(default="_simulator_state/state.json")
    gcp_project_id: str = Field(default="general-ak")
    enabled: bool = Field(default=True)
    weekdays_only: bool = Field(default=False)
    max_journeys_per_day: int = Field(default=320, ge=1)
    max_images_per_day: int = Field(default=40, ge=0)
    max_tick_jitter_seconds: float = Field(default=15.0, ge=0.0)
    circuit_breaker_threshold: int = Field(default=3, ge=1)
    circuit_breaker_base_seconds: float = Field(default=30.0, ge=1.0)
    port: int = Field(default=8080, ge=1, le=65535)

    @property
    def apply_loan_url(self) -> str:
        return f"{_normalize_api_base_url(self.api_base_url)}/v1/loans/apply"

    @property
    def vehicles_url(self) -> str:
        return f"{_normalize_api_base_url(self.api_base_url)}/v1/vehicles"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SimulatorConfig:
        source = env if env is not None else os.environ
        raw_env = source.get("GODVIEW_ENV", "gcp").strip().lower()
        if raw_env not in ("gcp", "local", "hermetic"):
            raise ValueError(
                f"Invalid GODVIEW_ENV={raw_env!r}; must be one of 'gcp', 'local', 'hermetic'"
            )
        env_mode: Literal["gcp", "local", "hermetic"] = raw_env  # type: ignore[assignment]
        raw_api_url = (
            source.get("API_BASE_URL")
            or source.get("TENANT_API_BASE_URL")
            or source.get("ORCHESTRATOR_URL")
            or "https://api.cymbal.ak-demos.com"
        )
        bucket = source.get("KYC_VAULT_BUCKET", "general-ak-god-view-kyc-vault").strip()
        state_object = source.get("SIMULATOR_STATE_OBJECT", "_simulator_state/state.json").strip()
        project_id = (
            source.get("GCP_PROJECT_ID") or source.get("GOOGLE_CLOUD_PROJECT") or "general-ak"
        ).strip()
        enabled_raw = source.get("SIMULATOR_ENABLED", "true").strip().lower()
        enabled = enabled_raw not in ("0", "false", "no", "off")
        weekdays_raw = source.get("TRAFFIC_WEEKDAYS_ONLY", "false").strip().lower()
        weekdays_only = weekdays_raw in ("1", "true", "yes", "on")
        max_journeys = int(source.get("DAILY_JOURNEY_HARD_CAP", "320"))
        max_images = int(source.get("DAILY_IMAGE_BUDGET_CAP", "40"))
        max_jitter = float(source.get("MAX_TICK_JITTER_SECONDS", "15.0"))
        cb_threshold = int(source.get("CIRCUIT_BREAKER_THRESHOLD", "3"))
        cb_base = float(source.get("CIRCUIT_BREAKER_BASE_SECONDS", "30.0"))
        port = int(source.get("PORT", "8080"))
        return cls(
            env_mode=env_mode,
            api_base_url=_normalize_api_base_url(raw_api_url),
            kyc_vault_bucket=bucket,
            state_object_name=state_object,
            gcp_project_id=project_id,
            enabled=enabled,
            weekdays_only=weekdays_only,
            max_journeys_per_day=max_journeys,
            max_images_per_day=max_images,
            max_tick_jitter_seconds=max_jitter,
            circuit_breaker_threshold=cb_threshold,
            circuit_breaker_base_seconds=cb_base,
            port=port,
        )
