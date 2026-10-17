"""Repository layer for commercial-fleet-credit-svc (Postgres & hermetic InMemory fake)."""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import quote_plus

import psycopg
from psycopg.rows import dict_row

from fleet_credit.models import CommercialCreditLineRow

METADATA_BASE_URL = "http://metadata.google.internal/computeMetadata/v1"
METADATA_HEADERS = {"Metadata-Flavor": "Google"}


class RepositoryError(RuntimeError):
    """Raised when a repository operation fails."""


class RepositoryConfigurationError(RepositoryError):
    """Raised when production environment configuration is missing DATABASE_URL."""


class FleetCreditRepositoryProtocol(Protocol):
    def save_credit_line(self, row: CommercialCreditLineRow) -> CommercialCreditLineRow: ...

    def get_by_loan_id(self, loan_id: str) -> CommercialCreditLineRow | None: ...

    def list_credit_lines(self) -> list[CommercialCreditLineRow]: ...


class InMemoryFleetCreditRepository:
    """Hermetic in-memory repository for unit tests (`GODVIEW_ENV=hermetic`)."""

    def __init__(self) -> None:
        self._by_loan_id: dict[str, CommercialCreditLineRow] = {}

    def save_credit_line(self, row: CommercialCreditLineRow) -> CommercialCreditLineRow:
        stored = row.model_copy(deep=True)
        self._by_loan_id[str(row.loan_id)] = stored
        return stored.model_copy(deep=True)

    def get_by_loan_id(self, loan_id: str) -> CommercialCreditLineRow | None:
        row = self._by_loan_id.get(str(loan_id))
        return row.model_copy(deep=True) if row is not None else None

    def list_credit_lines(self) -> list[CommercialCreditLineRow]:
        return [r.model_copy(deep=True) for r in self._by_loan_id.values()]


class PostgresFleetCreditRepository:
    """PostgreSQL repository targeting Cloud SQL `god-view-pg` (`godview_lms`)."""

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise RepositoryConfigurationError("PostgresFleetCreditRepository requires a non-empty DSN")
        self._dsn = dsn

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        try:
            return psycopg.connect(self._dsn, row_factory=dict_row)
        except Exception as exc:
            raise RepositoryError(f"Failed to connect to PostgreSQL: {exc}") from exc

    @staticmethod
    def _to_row(record: dict[str, Any]) -> CommercialCreditLineRow:
        return CommercialCreditLineRow(
            credit_line_id=str(record["credit_line_id"]),
            customer_id=str(record["customer_id"]),
            loan_id=str(record["loan_id"]),
            vehicle_category=record["vehicle_category"],
            fleet_size=int(record["fleet_size"]),
            requested_limit_inr=Decimal(str(record["requested_limit_inr"])),
            sanctioned_limit_inr=Decimal(str(record["sanctioned_limit_inr"])),
            utilisation_pct=Decimal(str(record["utilisation_pct"])),
            decision=record["decision"],
            status=record["status"],
            trace_id=record.get("trace_id"),
            evaluated_at=record.get("evaluated_at"),
        )

    def save_credit_line(self, row: CommercialCreditLineRow) -> CommercialCreditLineRow:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO commercial_credit_lines (
                        credit_line_id, customer_id, loan_id, vehicle_category,
                        fleet_size, requested_limit_inr, sanctioned_limit_inr,
                        utilisation_pct, decision, status, trace_id, evaluated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (credit_line_id) DO UPDATE SET
                        sanctioned_limit_inr = EXCLUDED.sanctioned_limit_inr,
                        utilisation_pct = EXCLUDED.utilisation_pct,
                        decision = EXCLUDED.decision,
                        status = EXCLUDED.status,
                        trace_id = EXCLUDED.trace_id,
                        evaluated_at = EXCLUDED.evaluated_at
                    """,
                    (
                        str(row.credit_line_id),
                        str(row.customer_id),
                        str(row.loan_id),
                        row.vehicle_category,
                        row.fleet_size,
                        row.requested_limit_inr,
                        row.sanctioned_limit_inr,
                        row.utilisation_pct,
                        row.decision,
                        row.status,
                        row.trace_id,
                        row.evaluated_at,
                    ),
                )
                conn.commit()
            return row.model_copy(deep=True)
        except RepositoryError:
            raise
        except Exception as exc:
            raise RepositoryError(f"Failed to persist commercial_credit_lines row: {exc}") from exc

    def get_by_loan_id(self, loan_id: str) -> CommercialCreditLineRow | None:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT credit_line_id, customer_id, loan_id, vehicle_category,
                           fleet_size, requested_limit_inr, sanctioned_limit_inr,
                           utilisation_pct, decision, status, trace_id, evaluated_at
                    FROM commercial_credit_lines
                    WHERE loan_id = %s
                    ORDER BY evaluated_at DESC NULLS LAST
                    LIMIT 1
                    """,
                    (str(loan_id),),
                )
                row = cur.fetchone()
                return self._to_row(row) if row else None
        except RepositoryError:
            raise
        except Exception as exc:
            raise RepositoryError(f"Failed to query commercial_credit_lines for loan_id={loan_id}: {exc}") from exc

    def list_credit_lines(self) -> list[CommercialCreditLineRow]:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT credit_line_id, customer_id, loan_id, vehicle_category,
                           fleet_size, requested_limit_inr, sanctioned_limit_inr,
                           utilisation_pct, decision, status, trace_id, evaluated_at
                    FROM commercial_credit_lines
                    ORDER BY evaluated_at DESC NULLS LAST
                    """
                )
                return [self._to_row(r) for r in cur.fetchall()]
        except RepositoryError:
            raise
        except Exception as exc:
            raise RepositoryError(f"Failed to list commercial_credit_lines: {exc}") from exc


def _fetch_metadata(path: str, *, timeout: float = 1.5) -> str | None:
    url = f"{METADATA_BASE_URL}/{path.lstrip('/')}"
    req = urllib.request.Request(url, headers=METADATA_HEADERS, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return resp.read().decode().strip()
    except Exception:
        return None
    return None


def _fetch_secret_from_gcp(secret_version_name: str, *, timeout: float = 5.0) -> str:
    token = ""
    raw = _fetch_metadata("instance/service-accounts/default/token", timeout=2.0)
    if raw:
        data = json.loads(raw)
        token = str(data.get("access_token", "")).strip()
    if not token:
        import google.auth
        from google.auth.transport.requests import Request

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        if not credentials.valid:
            credentials.refresh(Request())
        token = str(credentials.token or "").strip()
    if not token:
        raise RepositoryConfigurationError("Unable to obtain GCP access token for Secret Manager")

    url = f"https://secretmanager.googleapis.com/v1/{secret_version_name.lstrip('/')}:access"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
            return base64.b64decode(body["payload"]["data"]).decode().strip()
    except Exception as exc:
        raise RepositoryConfigurationError(
            f"Failed to fetch secret {secret_version_name!r} from Secret Manager: {exc}"
        ) from exc


def resolve_database_url() -> str:
    """Resolve PostgreSQL DSN from DATABASE_URL or DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD."""
    explicit = os.environ.get("DATABASE_URL", "").strip()
    if explicit:
        return explicit.replace("postgresql+psycopg://", "postgresql://").replace(
            "postgresql+asyncpg://", "postgresql://"
        )

    host = os.environ.get("DB_HOST", "").strip()
    if not host:
        raise RepositoryConfigurationError(
            "Neither DATABASE_URL nor DB_HOST is configured for commercial-fleet-credit-svc"
        )
    port = os.environ.get("DB_PORT", "5432").strip() or "5432"
    dbname = os.environ.get("DB_NAME", "godview_lms").strip() or "godview_lms"
    user = os.environ.get("DB_USER", "app_user").strip() or "app_user"
    password = (
        os.environ.get("DB_PASSWORD") or os.environ.get("CYMBAL_PG_APP_PASSWORD") or ""
    ).strip()

    if not password:
        secret_id = os.environ.get("DB_PASSWORD_SECRET_ID", "").strip()
        if not secret_id:
            project_id = (
                os.environ.get("GCP_PROJECT_ID", "").strip()
                or _fetch_metadata("project/project-id")
                or "general-ak"
            )
            secret_name = (
                os.environ.get("DB_PASSWORD_SECRET", "cymbal-pg-app-password").strip()
                or "cymbal-pg-app-password"
            )
            secret_id = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        password = _fetch_secret_from_gcp(secret_id)

    if not password:
        raise RepositoryConfigurationError("Database password resolved to empty string")

    return f"postgresql://{user}:{quote_plus(password)}@{host}:{port}/{dbname}"


def create_repository_from_env() -> FleetCreditRepositoryProtocol:
    raw_env = os.environ.get("GODVIEW_ENV")
    if raw_env is None or not raw_env.strip():
        raise RepositoryConfigurationError(
            "GODVIEW_ENV must be explicitly set (e.g. 'gcp', 'production', 'local', or 'hermetic'); refusing to default to in-memory fake."
        )
    env_mode = raw_env.strip().lower()
    has_db_config = bool(
        os.environ.get("DATABASE_URL", "").strip() or os.environ.get("DB_HOST", "").strip()
    )
    if env_mode == "hermetic" and not has_db_config:
        return InMemoryFleetCreditRepository()
    return PostgresFleetCreditRepository(resolve_database_url())
