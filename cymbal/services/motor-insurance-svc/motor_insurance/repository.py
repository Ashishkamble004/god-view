"""Repository layer for motor-insurance-svc (Postgres & hermetic InMemory fake)."""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import quote_plus

import psycopg
from psycopg.rows import dict_row

from motor_insurance.models import (
    ChaosFaultClass,
    ChaosFaultStateRow,
    ChaosFaultType,
    InsurancePolicyRow,
)

METADATA_BASE_URL = "http://metadata.google.internal/computeMetadata/v1"
METADATA_HEADERS = {"Metadata-Flavor": "Google"}


class RepositoryError(RuntimeError):
    """Raised when a repository operation fails."""


class RepositoryConfigurationError(RepositoryError):
    """Raised when production environment configuration is missing DATABASE_URL."""


class LoanNotFoundInRepositoryError(RepositoryError):
    """Raised when a referenced loan_id does not exist in loan_applications."""

    def __init__(self, loan_id: str) -> None:
        self.loan_id = loan_id
        super().__init__(f"LOAN_NOT_FOUND: loan_id={loan_id} does not exist in loan_applications")


class InsuranceRepositoryProtocol(Protocol):
    def get_loan_status(self, loan_id: str) -> str | None: ...

    def has_any_loans(self) -> bool: ...

    def requires_persisted_loan(self) -> bool: ...

    def save_policy(self, row: InsurancePolicyRow) -> InsurancePolicyRow: ...

    def get_by_loan_id(self, loan_id: str) -> InsurancePolicyRow | None: ...

    def list_policies(self) -> list[InsurancePolicyRow]: ...

    def is_fault_active(self, fault_type: str) -> bool: ...

    def set_chaos_fault(
        self,
        fault_type: str,
        *,
        active: bool,
        fault_id: str | None = None,
        fault_class: str | None = None,
        parameters: dict[str, Any] | None = None,
        triggered_by: str | None = None,
    ) -> ChaosFaultStateRow: ...

    def list_chaos_faults(self) -> list[ChaosFaultStateRow]: ...


class InMemoryInsuranceRepository:
    """Hermetic in-memory repository for unit tests (`GODVIEW_ENV=hermetic`)."""

    def __init__(self) -> None:
        self._policies_by_loan: dict[str, InsurancePolicyRow] = {}
        self._loan_statuses: dict[str, str] = {}
        self._faults: dict[str, ChaosFaultStateRow] = {}

    def seed_loan_status(self, loan_id: str, status: str) -> None:
        self._loan_statuses[str(loan_id)] = status

    def get_loan_status(self, loan_id: str) -> str | None:
        return self._loan_statuses.get(str(loan_id))

    def has_any_loans(self) -> bool:
        return len(self._loan_statuses) > 0

    def requires_persisted_loan(self) -> bool:
        return len(self._loan_statuses) > 0

    def save_policy(self, row: InsurancePolicyRow) -> InsurancePolicyRow:
        key = str(row.loan_id)
        stored = row.model_copy(deep=True)
        self._policies_by_loan[key] = stored
        return stored.model_copy(deep=True)

    def get_by_loan_id(self, loan_id: str) -> InsurancePolicyRow | None:
        row = self._policies_by_loan.get(str(loan_id))
        return row.model_copy(deep=True) if row is not None else None

    def list_policies(self) -> list[InsurancePolicyRow]:
        return [r.model_copy(deep=True) for r in self._policies_by_loan.values()]

    def is_fault_active(self, fault_type: str) -> bool:
        fault = self._faults.get(fault_type)
        if fault is None or not fault.is_active:
            return False
        if fault.expires_at is not None and fault.expires_at <= datetime.now(UTC):
            return False
        return True

    def set_chaos_fault(
        self,
        fault_type: str,
        *,
        active: bool,
        fault_id: str | None = None,
        fault_class: str | None = None,
        parameters: dict[str, Any] | None = None,
        triggered_by: str | None = None,
    ) -> ChaosFaultStateRow:
        ft = ChaosFaultType(fault_type)
        fc = ChaosFaultClass(fault_class) if fault_class else ChaosFaultClass.BUSINESS
        row = ChaosFaultStateRow(
            fault_id=fault_id or f"fault-{ft.value}",
            fault_class=fc,
            fault_type=ft,
            target_service="motor-insurance-svc",
            is_active=active,
            parameters_json=parameters or {},
            triggered_by=triggered_by or "test",
            activated_at=datetime.now(UTC) if active else None,
        )
        self._faults[ft.value] = row
        return row.model_copy(deep=True)

    def list_chaos_faults(self) -> list[ChaosFaultStateRow]:
        return [f.model_copy(deep=True) for f in self._faults.values() if f.is_active]


class PostgresInsuranceRepository:
    """PostgreSQL repository targeting Cloud SQL `god-view-pg` (`godview_lms`)."""

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise RepositoryConfigurationError("PostgresInsuranceRepository requires a non-empty DSN")
        self._dsn = dsn

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        try:
            return psycopg.connect(self._dsn, row_factory=dict_row)
        except Exception as exc:
            raise RepositoryError(f"Failed to connect to PostgreSQL: {exc}") from exc

    @staticmethod
    def _to_row(record: dict[str, Any]) -> InsurancePolicyRow:
        return InsurancePolicyRow(
            policy_id=str(record["policy_id"]),
            customer_id=str(record["customer_id"]),
            loan_id=str(record["loan_id"]),
            policy_type=record["policy_type"],
            fasttag_id=record["fasttag_id"],
            premium_inr=Decimal(str(record["premium_inr"])),
            coverage_inr=Decimal(str(record["coverage_inr"])),
            status=record["status"],
            trace_id=record.get("trace_id"),
            issued_at=record.get("issued_at"),
        )

    def get_loan_status(self, loan_id: str) -> str | None:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM loan_applications WHERE loan_id = %s",
                    (str(loan_id),),
                )
                row = cur.fetchone()
                return str(row["status"]) if row else None
        except RepositoryError:
            raise
        except Exception as exc:
            raise RepositoryError(f"Failed to query loan_applications for loan_id={loan_id}: {exc}") from exc

    def has_any_loans(self) -> bool:
        return True

    def requires_persisted_loan(self) -> bool:
        return True

    def save_policy(self, row: InsurancePolicyRow) -> InsurancePolicyRow:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO insurance_policies (
                        policy_id, customer_id, loan_id, policy_type, fasttag_id,
                        premium_inr, coverage_inr, status, trace_id, issued_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (policy_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        trace_id = EXCLUDED.trace_id,
                        issued_at = EXCLUDED.issued_at
                    """,
                    (
                        str(row.policy_id),
                        str(row.customer_id),
                        str(row.loan_id),
                        row.policy_type,
                        row.fasttag_id,
                        row.premium_inr,
                        row.coverage_inr,
                        row.status,
                        row.trace_id,
                        row.issued_at,
                    ),
                )
                conn.commit()
            return row.model_copy(deep=True)
        except RepositoryError:
            raise
        except Exception as exc:
            if getattr(exc, "sqlstate", None) == "23503" or isinstance(
                exc, psycopg.errors.ForeignKeyViolation
            ):
                raise LoanNotFoundInRepositoryError(str(row.loan_id)) from exc
            raise RepositoryError(f"Failed to persist insurance_policies row: {exc}") from exc

    def get_by_loan_id(self, loan_id: str) -> InsurancePolicyRow | None:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT policy_id, customer_id, loan_id, policy_type, fasttag_id,
                           premium_inr, coverage_inr, status, trace_id, issued_at
                    FROM insurance_policies
                    WHERE loan_id = %s
                    ORDER BY issued_at DESC NULLS LAST
                    LIMIT 1
                    """,
                    (str(loan_id),),
                )
                row = cur.fetchone()
                return self._to_row(row) if row else None
        except RepositoryError:
            raise
        except Exception as exc:
            raise RepositoryError(f"Failed to query insurance_policies for loan_id={loan_id}: {exc}") from exc

    def list_policies(self) -> list[InsurancePolicyRow]:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT policy_id, customer_id, loan_id, policy_type, fasttag_id,
                           premium_inr, coverage_inr, status, trace_id, issued_at
                    FROM insurance_policies
                    ORDER BY issued_at DESC NULLS LAST
                    """
                )
                return [self._to_row(r) for r in cur.fetchall()]
        except RepositoryError:
            raise
        except Exception as exc:
            raise RepositoryError(f"Failed to list insurance_policies: {exc}") from exc

    def is_fault_active(self, fault_type: str) -> bool:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 FROM chaos_fault_states
                    WHERE fault_type = %s
                      AND is_active = TRUE
                      AND (expires_at IS NULL OR expires_at > NOW())
                    LIMIT 1
                    """,
                    (fault_type,),
                )
                return cur.fetchone() is not None
        except RepositoryError:
            raise
        except Exception as exc:
            raise RepositoryError(f"Failed to query chaos_fault_states for {fault_type}: {exc}") from exc

    def set_chaos_fault(
        self,
        fault_type: str,
        *,
        active: bool,
        fault_id: str | None = None,
        fault_class: str | None = None,
        parameters: dict[str, Any] | None = None,
        triggered_by: str | None = None,
    ) -> ChaosFaultStateRow:
        ft = ChaosFaultType(fault_type)
        fc = ChaosFaultClass(fault_class) if fault_class else ChaosFaultClass.BUSINESS
        fid = fault_id or f"fault-{ft.value}"
        now = datetime.now(UTC) if active else None
        try:
            with self._connect() as conn, conn.cursor() as cur:
                if not active:
                    cur.execute(
                        "UPDATE chaos_fault_states SET is_active = FALSE WHERE fault_type = %s OR fault_id = %s",
                        (ft.value, fault_type),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO chaos_fault_states (
                            fault_id, fault_class, fault_type, target_service,
                            is_active, parameters_json, triggered_by, activated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                        ON CONFLICT (fault_id) DO UPDATE SET
                            is_active = EXCLUDED.is_active,
                            parameters_json = EXCLUDED.parameters_json,
                            triggered_by = EXCLUDED.triggered_by,
                            activated_at = EXCLUDED.activated_at
                        """,
                        (
                            fid,
                            fc.value,
                            ft.value,
                            "motor-insurance-svc",
                            active,
                            json.dumps(parameters or {}),
                            triggered_by or "operator",
                            now,
                        ),
                    )
                conn.commit()
            return ChaosFaultStateRow(
                fault_id=fid,
                fault_class=fc,
                fault_type=ft,
                target_service="motor-insurance-svc",
                is_active=active,
                parameters_json=parameters or {},
                triggered_by=triggered_by or "operator",
                activated_at=now,
            )
        except RepositoryError:
            raise
        except Exception as exc:
            raise RepositoryError(f"Failed to update chaos_fault_states for {fault_type}: {exc}") from exc

    def list_chaos_faults(self) -> list[ChaosFaultStateRow]:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT fault_id, fault_class, fault_type, target_service,
                           is_active, parameters_json, triggered_by, activated_at, expires_at
                    FROM chaos_fault_states
                    WHERE is_active = TRUE
                    """
                )
                return [
                    ChaosFaultStateRow(
                        fault_id=r["fault_id"],
                        fault_class=ChaosFaultClass(r["fault_class"]),
                        fault_type=ChaosFaultType(r["fault_type"]),
                        target_service=r["target_service"],
                        is_active=bool(r["is_active"]),
                        parameters_json=r.get("parameters_json") or {},
                        triggered_by=r.get("triggered_by"),
                        activated_at=r.get("activated_at"),
                        expires_at=r.get("expires_at"),
                    )
                    for r in cur.fetchall()
                ]
        except RepositoryError:
            raise
        except Exception as exc:
            raise RepositoryError(f"Failed to list active chaos faults: {exc}") from exc


def is_hermetic_env() -> bool:
    """Return True only when GODVIEW_ENV is explicitly set to 'hermetic'."""
    return os.environ.get("GODVIEW_ENV", "").strip().lower() == "hermetic"


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
            "Neither DATABASE_URL nor DB_HOST is configured for motor-insurance-svc"
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


def create_repository_from_env() -> InsuranceRepositoryProtocol:
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
        return InMemoryInsuranceRepository()
    return PostgresInsuranceRepository(resolve_database_url())
