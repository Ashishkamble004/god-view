"""Environment-backed runtime configuration for loan-orchestrator-api."""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import quote_plus


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _fetch_secret_from_gcp(secret_version_name: str) -> str:
    """Fetch secret payload from Google Secret Manager REST API using ADC."""
    import google.auth
    from google.auth.transport.requests import Request as GoogleAuthRequest

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    if not credentials.valid:
        credentials.refresh(GoogleAuthRequest())
    url = f"https://secretmanager.googleapis.com/v1/{secret_version_name}:access"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {credentials.token}"}
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        return base64.b64decode(body["payload"]["data"]).decode("utf-8").strip()


def _build_default_database_url() -> str:
    explicit = os.getenv("DATABASE_URL")
    if explicit and explicit.strip():
        return (
            explicit.strip()
            .replace("postgresql+psycopg://", "postgresql://")
            .replace("postgresql+asyncpg://", "postgresql://")
        )
    host = os.getenv("DB_HOST", "").strip()
    if not host:
        return ""
    port = os.getenv("DB_PORT", "5432").strip() or "5432"
    dbname = os.getenv("DB_NAME", "godview_lms").strip() or "godview_lms"
    user = os.getenv("DB_USER", "app_user").strip() or "app_user"
    password = (
        os.getenv("DB_PASSWORD")
        or os.getenv("CYMBAL_PG_APP_PASSWORD")
        or os.getenv("POSTGRES_PASSWORD")
        or ""
    ).strip()
    if not password:
        secret_id = os.getenv("DB_PASSWORD_SECRET_ID", "").strip()
        if not secret_id:
            project_id = os.getenv("GCP_PROJECT_ID", "general-ak").strip() or "general-ak"
            secret_name = (
                os.getenv("DB_PASSWORD_SECRET", "cymbal-pg-app-password").strip()
                or "cymbal-pg-app-password"
            )
            secret_id = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        try:
            password = _fetch_secret_from_gcp(secret_id)
        except Exception as exc:
            raise RuntimeError(
                f"DB_PASSWORD is unset and fetching secret '{secret_id}' failed: {exc}"
            ) from exc
    if not password:
        raise RuntimeError(
            "DB_PASSWORD or cymbal-pg-app-password secret resolved to empty string"
        )
    return f"postgresql://{user}:{quote_plus(password)}@{host}:{port}/{dbname}"


@dataclass(slots=True)
class OrchestratorSettings:
    """Configuration for Cymbal Auto Finance loan-orchestrator-api."""

    service_name: str = field(
        default_factory=lambda: os.getenv("SERVICE_NAME", "loan-orchestrator-api")
    )
    env_id: str = field(default_factory=lambda: os.getenv("ENV_ID", "local-compose"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8080")))
    database_url: str = field(default_factory=_build_default_database_url)
    use_in_memory_db: bool = field(
        default_factory=lambda: _env_bool("USE_IN_MEMORY_DB", default=False)
    )

    # Downstream internal utility service base URLs (TDD §3.2)
    kyc_svc_url: str = field(
        default_factory=lambda: os.getenv("KYC_SVC_URL", "http://kyc-model-armor-svc:8080")
    )
    underwriting_svc_url: str = field(
        default_factory=lambda: os.getenv(
            "UNDERWRITING_SVC_URL", "http://underwriting-los-svc:8080"
        )
    )
    fleet_credit_svc_url: str = field(
        default_factory=lambda: os.getenv(
            "FLEET_CREDIT_SVC_URL", "http://commercial-fleet-credit-svc:8080"
        )
    )
    insurance_svc_url: str = field(
        default_factory=lambda: os.getenv("INSURANCE_SVC_URL", "http://motor-insurance-svc:8080")
    )
    cbs_vm_url: str = field(
        default_factory=lambda: os.getenv("CBS_VM_URL", "http://core-banking-cibil-vm:8085")
    )

    kyc_vault_bucket: str = field(
        default_factory=lambda: os.getenv("KYC_VAULT_BUCKET", "general-ak-god-view-kyc-vault")
    )
    cdn_base_url: str = field(default_factory=lambda: os.getenv("CDN_BASE_URL", ""))
    tenant_paused: bool = field(default_factory=lambda: _env_bool("TENANT_PAUSED", default=False))
    tenant_pause_reason: str | None = field(
        default_factory=lambda: os.getenv("TENANT_PAUSE_REASON")
    )
    godview_managed: str = field(default_factory=lambda: os.getenv("GODVIEW_MANAGED", "true"))
    sse_heartbeat_seconds: float = field(
        default_factory=lambda: float(os.getenv("SSE_HEARTBEAT_SECONDS", "15.0"))
    )
    downstream_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("DOWNSTREAM_TIMEOUT_SECONDS", "15.0"))
    )
