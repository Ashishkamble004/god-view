"""Repository layer for core-banking-cibil-vm (Postgres & hermetic InMemory fake)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row

from cibil_cbs.env_sync import resolve_database_url
from cibil_cbs.models import (
    ChaosFaultClass,
    ChaosFaultStateRow,
    ChaosFaultType,
    DisbursementRow,
    DisbursementStatus,
)


class RepositoryError(RuntimeError):
    """Raised when a repository operation fails."""


class RepositoryConfigurationError(RepositoryError):
    """Raised when production environment configuration is missing DATABASE_URL."""


class CbsRepositoryProtocol(Protocol):
    def get_by_loan_id(self, loan_id: str) -> DisbursementRow | None: ...

    def save_disbursement(
        self, row: DisbursementRow, *, update_loan_status: str | None = None
    ) -> DisbursementRow: ...

    def list_disbursements(
        self, statuses: list[str] | None = None
    ) -> list[DisbursementRow]: ...

    def is_fault_active(self, fault_type: str) -> bool: ...

    def get_fault(self, fault_type: str) -> ChaosFaultStateRow | None: ...

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


class InMemoryCbsRepository:
    """Hermetic in-memory repository for unit tests (`GODVIEW_ENV=hermetic`)."""

    def __init__(self) -> None:
        self._disbursements_by_loan: dict[str, DisbursementRow] = {}
        self.status_history: dict[str, list[DisbursementStatus]] = {}
        self.loan_statuses: dict[str, str] = {}
        self._faults: dict[str, ChaosFaultStateRow] = {}

    def get_by_loan_id(self, loan_id: str) -> DisbursementRow | None:
        row = self._disbursements_by_loan.get(str(loan_id))
        return row.model_copy(deep=True) if row is not None else None

    def save_disbursement(
        self, row: DisbursementRow, *, update_loan_status: str | None = None
    ) -> DisbursementRow:
        key = str(row.loan_id)
        stored = row.model_copy(deep=True)
        self._disbursements_by_loan[key] = stored
        history = self.status_history.setdefault(key, [])
        if not history or history[-1] != stored.status:
            history.append(stored.status)
        if update_loan_status is not None:
            self.loan_statuses[key] = update_loan_status
        return stored.model_copy(deep=True)

    def list_disbursements(
        self, statuses: list[str] | None = None
    ) -> list[DisbursementRow]:
        rows = list(self._disbursements_by_loan.values())
        if statuses:
            allowed = {str(s).upper() for s in statuses}
            rows = [r for r in rows if str(r.status).upper() in allowed]
        return [r.model_copy(deep=True) for r in rows]

    def is_fault_active(self, fault_type: str) -> bool:
        fault = self._faults.get(fault_type)
        if fault is None or not fault.is_active:
            return False
        if fault.expires_at is not None and fault.expires_at <= datetime.now(UTC):
            return False
        return True

    def get_fault(self, fault_type: str) -> ChaosFaultStateRow | None:
        fault = self._faults.get(fault_type)
        if fault is None or not fault.is_active:
            return None
        return fault.model_copy(deep=True)

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
        fc = (
            ChaosFaultClass(fault_class)
            if fault_class
            else (
                ChaosFaultClass.INFRA
                if ft == ChaosFaultType.GCE_VM_LATENCY_504
                else ChaosFaultClass.BUSINESS
            )
        )
        row = ChaosFaultStateRow(
            fault_id=fault_id or f"fault-{ft.value}",
            fault_class=fc,
            fault_type=ft,
            target_service="core-banking-cibil-vm",
            is_active=active,
            parameters_json=parameters or {},
            triggered_by=triggered_by or "test",
            activated_at=datetime.now(UTC) if active else None,
        )
        self._faults[ft.value] = row
        return row.model_copy(deep=True)

    def list_chaos_faults(self) -> list[ChaosFaultStateRow]:
        return [f.model_copy(deep=True) for f in self._faults.values() if f.is_active]


class PostgresCbsRepository:
    """PostgreSQL repository targeting Cloud SQL `god-view-pg` (`godview_lms`)."""

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise RepositoryConfigurationError("PostgresCbsRepository requires a non-empty DSN")
        self._dsn = dsn

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        try:
            return psycopg.connect(self._dsn, row_factory=dict_row)
        except Exception as exc:
            raise RepositoryError(f"Failed to connect to PostgreSQL: {exc}") from exc

    @staticmethod
    def _to_row(record: dict[str, Any]) -> DisbursementRow:
        return DisbursementRow(
            disbursement_id=str(record["disbursement_id"]),
            loan_id=str(record["loan_id"]),
            sanction_letter_no=record["sanction_letter_no"],
            cbs_utr_number=record.get("cbs_utr_number"),
            nach_mandate_id=record["nach_mandate_id"],
            disbursed_amount_inr=Decimal(str(record["disbursed_amount_inr"])),
            dealer_account_no=record["dealer_account_no"],
            status=DisbursementStatus(record["status"]),
            attempts=int(record.get("attempts") or 0),
            replay_of=str(record["replay_of"]) if record.get("replay_of") else None,
            disbursed_at=record.get("disbursed_at"),
        )

    def get_by_loan_id(self, loan_id: str) -> DisbursementRow | None:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT disbursement_id, loan_id, sanction_letter_no, cbs_utr_number,
                           nach_mandate_id, disbursed_amount_inr, dealer_account_no,
                           status, attempts, replay_of, disbursed_at
                    FROM disbursements
                    WHERE loan_id = %s
                    ORDER BY disbursed_at DESC NULLS LAST, attempts DESC
                    LIMIT 1
                    """,
                    (str(loan_id),),
                )
                row = cur.fetchone()
                return self._to_row(row) if row else None
        except RepositoryError:
            raise
        except Exception as exc:
            raise RepositoryError(f"Failed to query disbursements for loan_id={loan_id}: {exc}") from exc

    def save_disbursement(
        self, row: DisbursementRow, *, update_loan_status: str | None = None
    ) -> DisbursementRow:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO disbursements (
                        disbursement_id, loan_id, sanction_letter_no, cbs_utr_number,
                        nach_mandate_id, disbursed_amount_inr, dealer_account_no,
                        status, attempts, replay_of, disbursed_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (disbursement_id) DO UPDATE SET
                        cbs_utr_number = EXCLUDED.cbs_utr_number,
                        status = EXCLUDED.status,
                        attempts = EXCLUDED.attempts,
                        replay_of = EXCLUDED.replay_of,
                        disbursed_at = EXCLUDED.disbursed_at
                    """,
                    (
                        str(row.disbursement_id),
                        str(row.loan_id),
                        row.sanction_letter_no,
                        row.cbs_utr_number,
                        row.nach_mandate_id,
                        row.disbursed_amount_inr,
                        row.dealer_account_no,
                        str(row.status.value if isinstance(row.status, DisbursementStatus) else row.status),
                        row.attempts,
                        str(row.replay_of) if row.replay_of else None,
                        row.disbursed_at,
                    ),
                )
                if update_loan_status is not None:
                    cur.execute(
                        """
                        UPDATE loan_applications
                        SET status = %s, updated_at = NOW()
                        WHERE loan_id = %s
                        """,
                        (update_loan_status, str(row.loan_id)),
                    )
                conn.commit()
            return row.model_copy(deep=True)
        except RepositoryError:
            raise
        except Exception as exc:
            raise RepositoryError(f"Failed to persist disbursement for loan_id={row.loan_id}: {exc}") from exc

    def list_disbursements(
        self, statuses: list[str] | None = None
    ) -> list[DisbursementRow]:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                if statuses:
                    cur.execute(
                        """
                        SELECT disbursement_id, loan_id, sanction_letter_no, cbs_utr_number,
                               nach_mandate_id, disbursed_amount_inr, dealer_account_no,
                               status, attempts, replay_of, disbursed_at
                        FROM disbursements
                        WHERE status = ANY(%s)
                        ORDER BY disbursed_at DESC NULLS LAST
                        """,
                        (statuses,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT disbursement_id, loan_id, sanction_letter_no, cbs_utr_number,
                               nach_mandate_id, disbursed_amount_inr, dealer_account_no,
                               status, attempts, replay_of, disbursed_at
                        FROM disbursements
                        ORDER BY disbursed_at DESC NULLS LAST
                        """
                    )
                return [self._to_row(r) for r in cur.fetchall()]
        except RepositoryError:
            raise
        except Exception as exc:
            raise RepositoryError(f"Failed to list disbursements: {exc}") from exc

    def get_fault(self, fault_type: str) -> ChaosFaultStateRow | None:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT fault_id, fault_class, fault_type, target_service,
                           is_active, parameters_json, triggered_by, activated_at, expires_at
                    FROM chaos_fault_states
                    WHERE fault_type = %s
                      AND is_active = TRUE
                      AND (expires_at IS NULL OR expires_at > NOW())
                    ORDER BY activated_at DESC NULLS LAST
                    LIMIT 1
                    """,
                    (fault_type,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return ChaosFaultStateRow(
                    fault_id=row["fault_id"],
                    fault_class=ChaosFaultClass(row["fault_class"]),
                    fault_type=ChaosFaultType(row["fault_type"]),
                    target_service=row["target_service"],
                    is_active=bool(row["is_active"]),
                    parameters_json=row.get("parameters_json") or {},
                    triggered_by=row.get("triggered_by"),
                    activated_at=row.get("activated_at"),
                    expires_at=row.get("expires_at"),
                )
        except RepositoryError:
            raise
        except Exception as exc:
            raise RepositoryError(f"Failed to query chaos_fault_states for {fault_type}: {exc}") from exc

    def is_fault_active(self, fault_type: str) -> bool:
        return self.get_fault(fault_type) is not None

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
        fc = (
            ChaosFaultClass(fault_class)
            if fault_class
            else (
                ChaosFaultClass.INFRA
                if ft == ChaosFaultType.GCE_VM_LATENCY_504
                else ChaosFaultClass.BUSINESS
            )
        )
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
                            "core-banking-cibil-vm",
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
                target_service="core-banking-cibil-vm",
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


def create_repository_from_env() -> CbsRepositoryProtocol:
    """Create the appropriate repository based on GODVIEW_ENV and DB configuration.

    Enforces Rule B6: GODVIEW_ENV has no default; raises when unset, and allows
    in-memory fakes ONLY for an explicit `GODVIEW_ENV=hermetic`.
    """
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
        return InMemoryCbsRepository()
    try:
        dsn = resolve_database_url(check_vm_metadata=(env_mode != "hermetic"))
    except RuntimeError as exc:
        raise RepositoryConfigurationError(
            f"Database configuration required when GODVIEW_ENV={env_mode!r}: {exc}"
        ) from exc
    return PostgresCbsRepository(dsn)
