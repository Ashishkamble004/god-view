"""Bounded chaos fault state for kyc-model-armor-svc (`gke_oom_503` and `cloudsql_lock_contention`)."""

from __future__ import annotations

import base64
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)

ALLOWED_CHAOS_SA_PREFIXES: tuple[str, ...] = (
    "godview-runtime-sa@",
    "godview-act-sa@",
)


class ChaosLockAcquisitionError(RuntimeError):
    """Raised when `cloudsql_lock_contention` fails to acquire the PostgreSQL table lock."""


class ChaosAuthError(PermissionError):
    """Raised when `/internal/chaos/faults` is called without a valid runtime/act SA identity."""

    def __init__(self, message: str, *, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


def verify_chaos_caller(
    headers: Mapping[str, str],
    *,
    token_verifier: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Verify Google OIDC ID token from `godview-runtime-sa` or `godview-act-sa` on `/internal/chaos/faults`.

    In `GODVIEW_ENV=hermetic` without an explicit `Authorization` header (and without
    `CHAOS_REQUIRE_AUTH=true`), allows local hermetic unit-test calls while enforcing full
    OIDC verification in non-hermetic environments or whenever `Authorization` is supplied.
    """
    lower_headers = {k.lower(): v for k, v in headers.items()}
    auth_header = lower_headers.get("authorization", "").strip()
    env_mode = os.environ.get("GODVIEW_ENV", "").strip().lower()
    require_auth = os.environ.get("CHAOS_REQUIRE_AUTH", "").strip().lower() == "true"

    if env_mode == "hermetic" and not auth_header and not require_auth:
        return {"email": "hermetic-test-runner@local", "mode": "hermetic"}

    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise ChaosAuthError(
            "Missing Authorization: Bearer <id_token> header for /internal/chaos/faults",
            status_code=401,
        )

    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        raise ChaosAuthError("Empty bearer token in Authorization header", status_code=401)

    shared_token = os.environ.get("CHAOS_INTERNAL_TOKEN", "").strip()
    if shared_token and token == shared_token:
        return {"email": "godview-runtime-sa@internal", "mode": "shared_token"}

    try:
        if token_verifier is not None:
            claims = token_verifier(token)
        else:
            from google.auth.transport import requests as google_requests
            from google.oauth2 import id_token

            audience = os.environ.get("CHAOS_OIDC_AUDIENCE") or None
            claims = id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
                audience=audience,
            )
    except Exception as exc:
        raise ChaosAuthError(
            f"Invalid Google OIDC ID token: {type(exc).__name__}: {exc}",
            status_code=401,
        ) from exc

    email = str(claims.get("email") or "").strip().lower()
    email_verified = bool(claims.get("email_verified", True))
    if not email_verified or not any(
        email.startswith(prefix) and email.endswith(".iam.gserviceaccount.com")
        for prefix in ALLOWED_CHAOS_SA_PREFIXES
    ):
        raise ChaosAuthError(
            f"Caller '{email}' is not authorized to mutate chaos faults; expected godview-runtime-sa or godview-act-sa",
            status_code=403,
        )
    return dict(claims)


def _fetch_secret_from_gcp(secret_version_name: str) -> str:
    """Fetch secret payload from Google Secret Manager REST API using ADC."""
    import google.auth
    from google.auth.transport.requests import Request

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    if not credentials.valid:
        credentials.refresh(Request())
    url = f"https://secretmanager.googleapis.com/v1/{secret_version_name}:access"
    with httpx.Client(timeout=5.0) as client:
        resp = client.get(url, headers={"Authorization": f"Bearer {credentials.token}"})
        resp.raise_for_status()
        b64_data = resp.json()["payload"]["data"]
        return base64.b64decode(b64_data).decode("utf-8").strip()


def resolve_app_user_dsn() -> str:
    """Resolve PostgreSQL connection string for `app_user` on `godview_lms`."""
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if dsn:
        return dsn.replace("postgresql+psycopg://", "postgresql://").replace(
            "postgresql+asyncpg://", "postgresql://"
        )

    host = os.environ.get("DB_HOST", "").strip()
    if not host:
        raise RuntimeError(
            "Neither DATABASE_URL nor DB_HOST is configured for cloudsql_lock_contention."
        )
    port = os.environ.get("DB_PORT", "5432").strip() or "5432"
    dbname = os.environ.get("DB_NAME", "godview_lms").strip() or "godview_lms"
    user = os.environ.get("DB_USER", "app_user").strip() or "app_user"
    password = os.environ.get("DB_PASSWORD", "").strip()

    if not password:
        secret_id = os.environ.get("DB_PASSWORD_SECRET_ID", "").strip()
        if not secret_id:
            project_id = os.environ.get("GCP_PROJECT_ID", "general-ak").strip() or "general-ak"
            secret_name = (
                os.environ.get("DB_PASSWORD_SECRET", "cymbal-pg-app-password").strip()
                or "cymbal-pg-app-password"
            )
            secret_id = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        password = _fetch_secret_from_gcp(secret_id)

    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"


class _HermeticLockCursor:
    def execute(self, sql: str) -> None:
        pass

    def __enter__(self) -> _HermeticLockCursor:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _HermeticLockConnection:
    def cursor(self) -> _HermeticLockCursor:
        return _HermeticLockCursor()

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


def default_db_conn_factory() -> Any:
    """Open a raw psycopg connection as `app_user` with autocommit disabled (or hermetic stub)."""
    if os.environ.get("GODVIEW_ENV") == "hermetic":
        return _HermeticLockConnection()
    import psycopg

    dsn = resolve_app_user_dsn()
    return psycopg.connect(dsn, autocommit=False)


@dataclass
class ActiveChaosFault:
    """Represents an active bounded chaos fault."""

    fault_id: str
    fault_type: str
    activated_at: datetime
    expires_at: datetime
    monotonic_expiry: float
    status: str = "active"
    parameters_json: dict[str, Any] = field(default_factory=dict)
    triggered_by: str = "chaos-drill"


class ChaosFaultManager:
    """Thread-safe TTL manager for active chaos faults (`gke_oom_503`, `cloudsql_lock_contention`)."""

    def __init__(
        self,
        service_name: str = "kyc-model-armor-svc",
        db_conn_factory: Callable[[], Any] | None = None,
        lock_acquire_timeout: float = 3.0,
        token_verifier: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.service_name = service_name
        self._db_conn_factory = db_conn_factory or default_db_conn_factory
        self._lock_acquire_timeout = lock_acquire_timeout
        self.token_verifier = token_verifier
        self._lock = threading.Lock()
        self._faults: dict[str, ActiveChaosFault] = {}
        self._fault_status: dict[str, str] = {}
        self._lock_stop_event: threading.Event | None = None
        self._lock_thread: threading.Thread | None = None

    def activate(
        self,
        fault_type: str,
        duration_seconds: int = 120,
        *,
        parameters_json: dict[str, Any] | None = None,
        triggered_by: str = "chaos-drill",
        fault_id: str | None = None,
    ) -> ActiveChaosFault:
        """Activate a chaos fault with a bounded TTL in seconds."""
        bounded_ttl = max(1, min(int(duration_seconds), 3600))
        now_utc = datetime.now(UTC)
        now_mono = time.monotonic()
        fault = ActiveChaosFault(
            fault_id=fault_id or str(uuid4()),
            fault_type=fault_type,
            activated_at=now_utc,
            expires_at=now_utc + timedelta(seconds=bounded_ttl),
            monotonic_expiry=now_mono + bounded_ttl,
            status="active",
            parameters_json=dict(parameters_json or {}),
            triggered_by=triggered_by,
        )

        if fault_type == "cloudsql_lock_contention":
            self._start_lock_holder(bounded_ttl, fault)
        else:
            with self._lock:
                self._faults[fault_type] = fault
                self._fault_status[fault_type] = "active"
        return fault

    def _start_lock_holder(self, duration_seconds: int, fault: ActiveChaosFault) -> None:
        """Acquire an exclusive table lock on `loan_applications` and release on expiry/stop.

        Reports status='pending' while acquiring the lock and fails loudly with
        `ChaosLockAcquisitionError` if the DB connection or LOCK TABLE fails.
        """
        self._stop_lock_holder()

        stop_event = threading.Event()
        acquired_event = threading.Event()
        acquire_error: list[Exception] = []

        with self._lock:
            self._fault_status["cloudsql_lock_contention"] = "pending"
            self._faults.pop("cloudsql_lock_contention", None)

        def _hold_lock() -> None:
            conn: Any = None
            lock_held = False
            try:
                conn = self._db_conn_factory()
                with conn.cursor() as cur:
                    cur.execute("BEGIN")
                    cur.execute("LOCK TABLE loan_applications IN ACCESS EXCLUSIVE MODE")
                lock_held = True
                now_utc = datetime.now(UTC)
                fault.activated_at = now_utc
                fault.expires_at = now_utc + timedelta(seconds=duration_seconds)
                fault.monotonic_expiry = time.monotonic() + float(duration_seconds)
                fault.status = "active"
                with self._lock:
                    self._faults["cloudsql_lock_contention"] = fault
                    self._fault_status["cloudsql_lock_contention"] = "active"
                acquired_event.set()
                stop_event.wait(timeout=float(duration_seconds))
            except Exception as exc:
                logger.error(
                    "cloudsql_lock_contention lock acquisition failed (%s: %s)",
                    type(exc).__name__,
                    exc,
                )
                acquire_error.append(exc)
                with self._lock:
                    self._faults.pop("cloudsql_lock_contention", None)
                    self._fault_status.pop("cloudsql_lock_contention", None)
                acquired_event.set()
            finally:
                if conn is not None:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    try:
                        conn.close()
                    except Exception:
                        pass
                if lock_held:
                    with self._lock:
                        self._faults.pop("cloudsql_lock_contention", None)
                        self._fault_status.pop("cloudsql_lock_contention", None)

        worker = threading.Thread(
            target=_hold_lock,
            name="kyc-cloudsql-lock-holder",
            daemon=True,
        )
        with self._lock:
            self._lock_stop_event = stop_event
            self._lock_thread = worker

        worker.start()
        acquired = acquired_event.wait(timeout=self._lock_acquire_timeout)
        if acquire_error:
            self._stop_lock_holder()
            with self._lock:
                self._faults.pop("cloudsql_lock_contention", None)
                self._fault_status.pop("cloudsql_lock_contention", None)
            err = acquire_error[0]
            raise ChaosLockAcquisitionError(
                f"Failed to acquire ACCESS EXCLUSIVE lock on loan_applications: {type(err).__name__}: {err}"
            ) from err

        if not acquired:
            raise ChaosLockAcquisitionError(
                "Timed out waiting to acquire ACCESS EXCLUSIVE lock on loan_applications (status=pending)"
            )

    def _stop_lock_holder(self) -> None:
        """Signal any active lock-holding thread to rollback and close its connection."""
        with self._lock:
            stop_event = self._lock_stop_event
            worker = self._lock_thread
            self._lock_stop_event = None
            self._lock_thread = None

        if stop_event is not None:
            stop_event.set()
        if worker is not None and worker.is_alive():
            worker.join(timeout=2.0)

    def clear(self, fault_type: str) -> bool:
        """Immediately clear an active chaos fault and return whether it was present."""
        if fault_type == "cloudsql_lock_contention":
            self._stop_lock_holder()
        with self._lock:
            removed = self._faults.pop(fault_type, None)
            self._fault_status.pop(fault_type, None)
        return removed is not None

    def deactivate(self, fault_type: str) -> dict[str, Any]:
        """Alias for clearing a chaos fault."""
        self.clear(fault_type)
        return {
            "service": self.service_name,
            "fault_type": fault_type,
            "status": "inactive",
            "active": False,
        }

    def fault_status(self, fault_type: str) -> str:
        """Return 'pending', 'active', or 'inactive' for `fault_type`."""
        now = time.monotonic()
        with self._lock:
            state = self._fault_status.get(fault_type)
            if state == "pending":
                return "pending"
            fault = self._faults.get(fault_type)
            if fault is None:
                self._fault_status.pop(fault_type, None)
                return "inactive"
            if now >= fault.monotonic_expiry:
                del self._faults[fault_type]
                self._fault_status.pop(fault_type, None)
                return "inactive"
            return "active"

    def is_active(
        self,
        fault_type: str,
        *,
        header_fault: str | None = None,
        body_fault: str | None = None,
    ) -> bool:
        """Return True if `fault_type` is active via TTL state or (in hermetic mode) per-request override."""
        if os.environ.get("GODVIEW_ENV") == "hermetic":
            if header_fault and header_fault.strip().lower() == fault_type.lower():
                return True
            if body_fault and body_fault.strip().lower() == fault_type.lower():
                return True
        return self.fault_status(fault_type) == "active"

    def list_active(self) -> list[dict[str, Any]]:
        """Return list of currently active or pending faults."""
        now = time.monotonic()
        items: list[dict[str, Any]] = []
        with self._lock:
            for key, state in list(self._fault_status.items()):
                if state == "pending" and key not in self._faults:
                    items.append(
                        {
                            "fault_type": key,
                            "target_service": self.service_name,
                            "status": "pending",
                            "is_active": False,
                        }
                    )
            for key, fault in list(self._faults.items()):
                remaining = fault.monotonic_expiry - now
                if remaining <= 0:
                    del self._faults[key]
                    self._fault_status.pop(key, None)
                else:
                    items.append(
                        {
                            "fault_id": fault.fault_id,
                            "fault_type": fault.fault_type,
                            "target_service": self.service_name,
                            "status": "active",
                            "is_active": True,
                            "remaining_seconds": round(remaining, 2),
                            "activated_at": fault.activated_at.isoformat(),
                            "expires_at": fault.expires_at.isoformat(),
                        }
                    )
        return items
