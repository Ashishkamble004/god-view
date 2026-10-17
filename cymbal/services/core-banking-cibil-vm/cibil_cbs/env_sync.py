"""GCE metadata + Secret Manager environment resolver for core-banking-cibil-vm."""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path
from urllib.parse import quote_plus

METADATA_BASE_URL = "http://metadata.google.internal/computeMetadata/v1"
METADATA_HEADERS = {"Metadata-Flavor": "Google"}


def fetch_metadata(path: str, *, timeout: float = 1.5) -> str | None:
    """Read a single attribute from the GCE/Cloud Run metadata server."""
    url = f"{METADATA_BASE_URL}/{path.lstrip('/')}"
    req = urllib.request.Request(url, headers=METADATA_HEADERS, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return resp.read().decode().strip()
    except Exception:
        return None
    return None


def fetch_access_token(*, timeout: float = 2.0) -> str:
    """Fetch an OAuth2 access token from the GCE metadata server or ADC."""
    raw = fetch_metadata("instance/service-accounts/default/token", timeout=timeout)
    if raw:
        data = json.loads(raw)
        token = str(data.get("access_token", "")).strip()
        if token:
            return token

    try:
        import google.auth
        from google.auth.transport.requests import Request

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        if not credentials.valid:
            credentials.refresh(Request())
        if credentials.token:
            return str(credentials.token).strip()
    except Exception as exc:
        raise RuntimeError(
            f"Unable to obtain GCP access token from metadata server or ADC: {exc}"
        ) from exc

    raise RuntimeError("Unable to obtain GCP access token from metadata server or ADC")


def fetch_secret_from_gcp(secret_version_name: str, *, timeout: float = 5.0) -> str:
    """Fetch a secret payload from Secret Manager REST API."""
    token = fetch_access_token(timeout=timeout)
    url = f"https://secretmanager.googleapis.com/v1/{secret_version_name.lstrip('/')}:access"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
            b64_data = body["payload"]["data"]
            return base64.b64decode(b64_data).decode().strip()
    except Exception as exc:
        raise RuntimeError(
            f"Failed to fetch secret {secret_version_name!r} from Secret Manager: {exc}"
        ) from exc


def resolve_vm_metadata_db_host() -> str:
    """Try reading DB_HOST from GCE instance attributes under common key names."""
    for key in ("DB_HOST", "db-host", "sql_private_ip", "sql-private-ip"):
        val = fetch_metadata(f"instance/attributes/{key}")
        if val:
            return val
    return ""


def resolve_database_url(*, check_vm_metadata: bool = True) -> str:
    """Resolve PostgreSQL DSN from DATABASE_URL or DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD."""
    explicit = os.environ.get("DATABASE_URL", "").strip()
    if explicit:
        return explicit.replace("postgresql+psycopg://", "postgresql://").replace(
            "postgresql+asyncpg://", "postgresql://"
        )

    host = os.environ.get("DB_HOST", "").strip()
    if not host and check_vm_metadata:
        host = resolve_vm_metadata_db_host()
    if not host:
        raise RuntimeError(
            "Neither DATABASE_URL nor DB_HOST is configured; refusing to start without database."
        )

    port = os.environ.get("DB_PORT", "5432").strip() or "5432"
    dbname = os.environ.get("DB_NAME", "godview_lms").strip() or "godview_lms"
    user = os.environ.get("DB_USER", "app_user").strip() or "app_user"
    password = (
        os.environ.get("DB_PASSWORD") or os.environ.get("CYMBAL_PG_APP_PASSWORD") or ""
    ).strip()

    if not password:
        secret_id = os.environ.get("DB_PASSWORD_SECRET_ID", "").strip()
        if not secret_id and check_vm_metadata:
            secret_id = (
                fetch_metadata("instance/attributes/DB_PASSWORD_SECRET_ID")
                or fetch_metadata("instance/attributes/db-password-secret-id")
                or ""
            )
        if not secret_id:
            project_id = (
                os.environ.get("GCP_PROJECT_ID", "").strip()
                or (fetch_metadata("project/project-id") if check_vm_metadata else None)
                or "general-ak"
            )
            secret_name = (
                os.environ.get("DB_PASSWORD_SECRET", "cymbal-pg-app-password").strip()
                or "cymbal-pg-app-password"
            )
            secret_id = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        password = fetch_secret_from_gcp(secret_id)

    if not password:
        raise RuntimeError("Database password resolved to empty string")

    return f"postgresql://{user}:{quote_plus(password)}@{host}:{port}/{dbname}"


def sync_vm_env_file(env_file_path: str = "/etc/default/cibil-cbs") -> dict[str, str]:
    """Populate `/etc/default/cibil-cbs` (mode 0640) from GCE instance metadata & Secret Manager."""
    existing: dict[str, str] = {}
    path = Path(env_file_path)
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, v = stripped.split("=", 1)
                existing[k.strip()] = v.strip()

    project_id = (
        os.environ.get("GCP_PROJECT_ID")
        or existing.get("GCP_PROJECT_ID")
        or fetch_metadata("project/project-id")
        or "general-ak"
    )
    db_host = (
        os.environ.get("DB_HOST")
        or existing.get("DB_HOST")
        or resolve_vm_metadata_db_host()
    )
    db_port = (
        os.environ.get("DB_PORT")
        or existing.get("DB_PORT")
        or fetch_metadata("instance/attributes/DB_PORT")
        or fetch_metadata("instance/attributes/db-port")
        or "5432"
    )
    db_name = (
        os.environ.get("DB_NAME")
        or existing.get("DB_NAME")
        or fetch_metadata("instance/attributes/DB_NAME")
        or fetch_metadata("instance/attributes/db-name")
        or "godview_lms"
    )
    db_user = (
        os.environ.get("DB_USER")
        or existing.get("DB_USER")
        or fetch_metadata("instance/attributes/DB_USER")
        or fetch_metadata("instance/attributes/db-user")
        or "app_user"
    )
    db_secret = (
        os.environ.get("DB_PASSWORD_SECRET")
        or existing.get("DB_PASSWORD_SECRET")
        or fetch_metadata("instance/attributes/DB_PASSWORD_SECRET")
        or fetch_metadata("instance/attributes/db-password-secret")
        or "cymbal-pg-app-password"
    )
    db_secret_id = (
        os.environ.get("DB_PASSWORD_SECRET_ID")
        or existing.get("DB_PASSWORD_SECRET_ID")
        or fetch_metadata("instance/attributes/DB_PASSWORD_SECRET_ID")
        or fetch_metadata("instance/attributes/db-password-secret-id")
        or f"projects/{project_id}/secrets/{db_secret}/versions/latest"
    )

    db_password = os.environ.get("DB_PASSWORD") or existing.get("DB_PASSWORD") or ""
    if not db_password:
        try:
            db_password = fetch_secret_from_gcp(db_secret_id)
        except Exception:
            db_password = ""

    merged: dict[str, str] = {
        **existing,
        "SERVICE_NAME": existing.get("SERVICE_NAME", "core-banking-cibil-vm"),
        "PORT": existing.get("PORT", "8085"),
        "GODVIEW_ENV": existing.get("GODVIEW_ENV", "gcp"),
        "GCP_PROJECT_ID": project_id,
        "DB_PORT": db_port,
        "DB_NAME": db_name,
        "DB_USER": db_user,
        "DB_PASSWORD_SECRET": db_secret,
        "DB_PASSWORD_SECRET_ID": db_secret_id,
    }
    if db_host:
        merged["DB_HOST"] = db_host
    if db_password:
        merged["DB_PASSWORD"] = db_password
    otlp_endpoint = (
        os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        or existing.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        or fetch_metadata("instance/attributes/OTEL_EXPORTER_OTLP_ENDPOINT")
        or fetch_metadata("instance/attributes/otel-exporter-otlp-endpoint")
        or ""
    )
    if otlp_endpoint:
        merged["OTEL_EXPORTER_OTLP_ENDPOINT"] = otlp_endpoint

    lines = [f"{k}={v}" for k, v in merged.items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o640)
    return merged


if __name__ == "__main__":
    sync_vm_env_file()
