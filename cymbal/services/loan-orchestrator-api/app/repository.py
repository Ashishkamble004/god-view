"""Async repository layer for loan-orchestrator-api (PostgreSQL 16 via psycopg 3 + InMemory test double)."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import psycopg
import sqlglot
from psycopg.rows import dict_row

from app.models import (
    ChaosFaultStateRow,
    CommercialCreditLineRow,
    CustomerRow,
    DisbursementRow,
    InsurancePolicyRow,
    KycDocumentRow,
    LoanApplicationRow,
    LoanStatus,
    TraceSpanViewModel,
    UnderwritingAuditRow,
    VehicleRow,
    utc_now,
)

logger = logging.getLogger(__name__)

DEFAULT_SCHEMA_SQL_PATH = Path(__file__).resolve().parent / "schema.sql"


def _parse_seeded_vehicles_from_schema_sql(schema_path: Path) -> list[VehicleRow]:
    """Extract the 8 seeded vehicles directly from app/schema.sql via sqlglot (postgres dialect)."""
    sql_text = schema_path.read_text(encoding="utf-8")
    expressions = sqlglot.parse(
        sql_text, read="postgres", error_level=sqlglot.ErrorLevel.IGNORE
    )
    vehicles: list[VehicleRow] = []
    for expr in expressions:
        if isinstance(expr, sqlglot.exp.Insert):
            table = expr.find(sqlglot.exp.Table)
            if table and table.name == "vehicles":
                for tup in expr.find_all(sqlglot.exp.Tuple):
                    vals = [lit.name for lit in tup.expressions]
                    if len(vals) == 7:
                        vehicles.append(
                            VehicleRow(
                                vehicle_id=vals[0],
                                make=vals[1],
                                model=vals[2],
                                category=vals[3],  # type: ignore[arg-type]
                                ex_showroom_inr=Decimal(vals[4]),
                                cdn_image_url=vals[5],
                                min_down_payment_pct=Decimal(vals[6]),
                            )
                        )
    return vehicles


class OrchestratorRepository(Protocol):
    """Async repository contract for the 9 tenant business tables of TDD §2.1."""

    async def initialize_schema(self) -> None: ...

    async def list_vehicles(self) -> list[VehicleRow]: ...

    async def get_vehicle(self, vehicle_id: str) -> VehicleRow | None: ...

    async def upsert_customer(self, customer: CustomerRow) -> CustomerRow: ...

    async def get_customer(self, customer_id: UUID) -> CustomerRow | None: ...

    async def upsert_kyc_document(self, doc: KycDocumentRow) -> KycDocumentRow: ...

    async def get_kyc_document_by_loan(self, loan_id: UUID) -> KycDocumentRow | None: ...

    async def get_latest_kyc_document_for_customer(
        self, customer_id: UUID
    ) -> KycDocumentRow | None: ...

    async def create_loan_application(self, loan: LoanApplicationRow) -> LoanApplicationRow: ...

    async def update_loan_status(
        self,
        loan_id: UUID,
        status: LoanStatus,
        *,
        emi_inr: Decimal | None = None,
        total_latency_ms: int | None = None,
        trace_id: str | None = None,
    ) -> LoanApplicationRow | None: ...

    async def get_loan_application(self, loan_id: UUID) -> LoanApplicationRow | None: ...

    async def get_loan_by_journey_id(self, journey_id: str) -> LoanApplicationRow | None: ...

    async def get_loan_by_trace_id(self, trace_id: str) -> LoanApplicationRow | None: ...

    async def list_loan_applications(
        self,
        *,
        status: str | None = None,
        utility_type: str | None = None,
        synthetic: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[LoanApplicationRow], int]: ...

    async def save_underwriting_audit(
        self, audit: UnderwritingAuditRow
    ) -> UnderwritingAuditRow: ...

    async def get_underwriting_audit_by_loan(
        self, loan_id: UUID
    ) -> UnderwritingAuditRow | None: ...

    async def save_commercial_credit_line(
        self, line: CommercialCreditLineRow
    ) -> CommercialCreditLineRow: ...

    async def get_commercial_credit_line_by_loan(
        self, loan_id: UUID
    ) -> CommercialCreditLineRow | None: ...

    async def save_insurance_policy(self, policy: InsurancePolicyRow) -> InsurancePolicyRow: ...

    async def get_insurance_policy_by_loan(self, loan_id: UUID) -> InsurancePolicyRow | None: ...

    async def cancel_insurance_policy_by_loan(
        self, loan_id: UUID
    ) -> InsurancePolicyRow | None: ...

    async def save_disbursement(self, disbursement: DisbursementRow) -> DisbursementRow: ...

    async def get_disbursement_by_loan(self, loan_id: UUID) -> DisbursementRow | None: ...

    async def list_disbursements_by_statuses(
        self, statuses: list[str]
    ) -> list[DisbursementRow]: ...

    async def upsert_chaos_fault(self, fault: ChaosFaultStateRow) -> ChaosFaultStateRow: ...

    async def clear_chaos_fault(self, fault_id: str) -> ChaosFaultStateRow | None: ...

    async def list_active_chaos_faults(self) -> list[ChaosFaultStateRow]: ...

    def record_trace_span(self, trace_id: str, span: TraceSpanViewModel) -> None: ...

    def get_recorded_trace_spans(self, trace_id: str) -> list[TraceSpanViewModel]: ...

    def get_pause_state(self) -> tuple[bool | None, str | None]: ...

    def set_pause_state(self, paused: bool, reason: str | None) -> None: ...


class InMemoryOrchestratorRepository:
    """In-memory repository seeded from app/schema.sql for fast, deterministic L1/L2 testing."""

    def __init__(self, vehicles: list[VehicleRow] | None = None) -> None:
        self.vehicles: dict[str, VehicleRow] = {v.vehicle_id: v for v in (vehicles or [])}
        self.customers: dict[UUID, CustomerRow] = {}
        self.kyc_documents: dict[UUID, KycDocumentRow] = {}
        self.loan_applications: dict[UUID, LoanApplicationRow] = {}
        self.underwriting_audits: dict[UUID, UnderwritingAuditRow] = {}
        self.commercial_credit_lines: dict[UUID, CommercialCreditLineRow] = {}
        self.insurance_policies: dict[UUID, InsurancePolicyRow] = {}
        self.disbursements: dict[UUID, DisbursementRow] = {}
        self.chaos_fault_states: dict[str, ChaosFaultStateRow] = {}
        self._trace_spans: dict[str, list[TraceSpanViewModel]] = {}
        self._paused: bool | None = None
        self._pause_reason: str | None = None

    @classmethod
    def from_schema_sql(
        cls, schema_path: Path = DEFAULT_SCHEMA_SQL_PATH
    ) -> InMemoryOrchestratorRepository:
        vehicles = _parse_seeded_vehicles_from_schema_sql(schema_path)
        return cls(vehicles=vehicles)

    async def initialize_schema(self) -> None:
        if not self.vehicles and DEFAULT_SCHEMA_SQL_PATH.is_file():
            for v in _parse_seeded_vehicles_from_schema_sql(DEFAULT_SCHEMA_SQL_PATH):
                self.vehicles[v.vehicle_id] = v

    async def list_vehicles(self) -> list[VehicleRow]:
        return list(self.vehicles.values())

    async def get_vehicle(self, vehicle_id: str) -> VehicleRow | None:
        return self.vehicles.get(vehicle_id)

    async def upsert_customer(self, customer: CustomerRow) -> CustomerRow:
        self.customers[customer.customer_id] = customer
        return customer

    async def get_customer(self, customer_id: UUID) -> CustomerRow | None:
        return self.customers.get(customer_id)

    async def upsert_kyc_document(self, doc: KycDocumentRow) -> KycDocumentRow:
        self.kyc_documents[doc.kyc_id] = doc
        return doc

    async def get_kyc_document_by_loan(self, loan_id: UUID) -> KycDocumentRow | None:
        for doc in reversed(list(self.kyc_documents.values())):
            if doc.loan_id == loan_id:
                return doc
        return None

    async def get_latest_kyc_document_for_customer(
        self, customer_id: UUID
    ) -> KycDocumentRow | None:
        for doc in reversed(list(self.kyc_documents.values())):
            if doc.customer_id == customer_id:
                return doc
        return None

    async def create_loan_application(self, loan: LoanApplicationRow) -> LoanApplicationRow:
        self.loan_applications[loan.loan_id] = loan
        return loan

    async def update_loan_status(
        self,
        loan_id: UUID,
        status: LoanStatus,
        *,
        emi_inr: Decimal | None = None,
        total_latency_ms: int | None = None,
        trace_id: str | None = None,
    ) -> LoanApplicationRow | None:
        existing = self.loan_applications.get(loan_id)
        if existing is None:
            return None
        updates: dict[str, Any] = {"status": status, "updated_at": utc_now()}
        if emi_inr is not None:
            updates["emi_inr"] = emi_inr
        if total_latency_ms is not None:
            updates["total_latency_ms"] = total_latency_ms
        if trace_id is not None:
            updates["trace_id"] = trace_id
        updated = existing.model_copy(update=updates)
        self.loan_applications[loan_id] = updated
        return updated

    async def get_loan_application(self, loan_id: UUID) -> LoanApplicationRow | None:
        return self.loan_applications.get(loan_id)

    async def get_loan_by_journey_id(self, journey_id: str) -> LoanApplicationRow | None:
        for loan in reversed(list(self.loan_applications.values())):
            if loan.journey_id == journey_id:
                return loan
        return None

    async def get_loan_by_trace_id(self, trace_id: str) -> LoanApplicationRow | None:
        for loan in reversed(list(self.loan_applications.values())):
            if loan.trace_id == trace_id:
                return loan
        return None

    async def list_loan_applications(
        self,
        *,
        status: str | None = None,
        utility_type: str | None = None,
        synthetic: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[LoanApplicationRow], int]:
        rows = sorted(
            self.loan_applications.values(),
            key=lambda r: r.created_at,
            reverse=True,
        )
        filtered: list[LoanApplicationRow] = []
        for row in rows:
            if status and row.status != status:
                continue
            if utility_type and row.utility_type != utility_type:
                continue
            if synthetic is not None:
                cust = self.customers.get(row.customer_id)
                is_syn = bool(cust.is_ai_generated) if cust else False
                if is_syn != synthetic:
                    continue
            filtered.append(row)
        total = len(filtered)
        return filtered[offset : offset + limit], total

    async def save_underwriting_audit(
        self, audit: UnderwritingAuditRow
    ) -> UnderwritingAuditRow:
        self.underwriting_audits[audit.loan_id] = audit
        return audit

    async def get_underwriting_audit_by_loan(
        self, loan_id: UUID
    ) -> UnderwritingAuditRow | None:
        return self.underwriting_audits.get(loan_id)

    async def save_commercial_credit_line(
        self, line: CommercialCreditLineRow
    ) -> CommercialCreditLineRow:
        self.commercial_credit_lines[line.loan_id] = line
        return line

    async def get_commercial_credit_line_by_loan(
        self, loan_id: UUID
    ) -> CommercialCreditLineRow | None:
        return self.commercial_credit_lines.get(loan_id)

    async def save_insurance_policy(self, policy: InsurancePolicyRow) -> InsurancePolicyRow:
        self.insurance_policies[policy.loan_id] = policy
        return policy

    async def get_insurance_policy_by_loan(self, loan_id: UUID) -> InsurancePolicyRow | None:
        return self.insurance_policies.get(loan_id)

    async def cancel_insurance_policy_by_loan(
        self, loan_id: UUID
    ) -> InsurancePolicyRow | None:
        existing = self.insurance_policies.get(loan_id)
        if existing is None:
            return None
        cancelled = existing.model_copy(update={"status": "CANCELLED"})
        self.insurance_policies[loan_id] = cancelled
        return cancelled

    async def save_disbursement(self, disbursement: DisbursementRow) -> DisbursementRow:
        self.disbursements[disbursement.loan_id] = disbursement
        return disbursement

    async def get_disbursement_by_loan(self, loan_id: UUID) -> DisbursementRow | None:
        return self.disbursements.get(loan_id)

    async def list_disbursements_by_statuses(
        self, statuses: list[str]
    ) -> list[DisbursementRow]:
        wanted = {s.strip().upper() for s in statuses if s.strip()}
        return [d for d in self.disbursements.values() if d.status in wanted]

    async def upsert_chaos_fault(self, fault: ChaosFaultStateRow) -> ChaosFaultStateRow:
        self.chaos_fault_states[fault.fault_id] = fault
        return fault

    async def clear_chaos_fault(self, fault_id: str) -> ChaosFaultStateRow | None:
        existing = self.chaos_fault_states.get(fault_id)
        if existing is None:
            for f in self.chaos_fault_states.values():
                if f.fault_type == fault_id:
                    existing = f
                    break
        if existing is None:
            return None
        cleared = existing.model_copy(
            update={"is_active": False, "expires_at": utc_now()}
        )
        self.chaos_fault_states[existing.fault_id] = cleared
        return cleared

    async def list_active_chaos_faults(self) -> list[ChaosFaultStateRow]:
        now = utc_now()
        active: list[ChaosFaultStateRow] = []
        for fault in self.chaos_fault_states.values():
            if not fault.is_active:
                continue
            if fault.expires_at and fault.expires_at < now:
                continue
            active.append(fault)
        return active

    def record_trace_span(self, trace_id: str, span: TraceSpanViewModel) -> None:
        bucket = self._trace_spans.setdefault(trace_id, [])
        bucket.append(span)

    def get_recorded_trace_spans(self, trace_id: str) -> list[TraceSpanViewModel]:
        return list(self._trace_spans.get(trace_id, []))

    def get_pause_state(self) -> tuple[bool | None, str | None]:
        return self._paused, self._pause_reason

    def set_pause_state(self, paused: bool, reason: str | None) -> None:
        self._paused = paused
        self._pause_reason = reason


class PostgresOrchestratorRepository:
    """Async PostgreSQL 16 repository backed by psycopg 3 (`godview_lms`)."""

    def __init__(
        self,
        database_url: str,
        schema_path: Path = DEFAULT_SCHEMA_SQL_PATH,
        pause_state_file: Path | None = None,
    ) -> None:
        self.database_url = database_url
        self.schema_path = schema_path
        self._pause_state_file = pause_state_file or Path(
            os.getenv("ORCHESTRATOR_PAUSE_STATE_FILE", "/tmp/godview_tenant_pause.json")
        )
        self._trace_spans: dict[str, list[TraceSpanViewModel]] = {}
        self._paused: bool | None = None
        self._pause_reason: str | None = None

    async def _connect(self) -> psycopg.AsyncConnection[dict[str, Any]]:
        return await psycopg.AsyncConnection.connect(
            self.database_url,
            autocommit=True,
            row_factory=dict_row,
        )

    async def initialize_schema(self) -> None:
        """Apply app/schema.sql idempotently against PostgreSQL 16 without emitting PG ERROR logs."""
        if not self.schema_path.is_file():
            return
        sql_text = self.schema_path.read_text(encoding="utf-8")
        statements = [stmt.strip() for stmt in sql_text.split(";") if stmt.strip()]
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT pg_advisory_lock(84920174)")
                try:
                    await cur.execute("SET client_min_messages TO ERROR")
                    await cur.execute(
                        """
                        SELECT
                            (SELECT COUNT(*) FROM information_schema.tables
                             WHERE table_schema = 'public'
                               AND table_name IN (
                                   'vehicles', 'customers', 'kyc_documents', 'loan_applications',
                                   'underwriting_audits', 'commercial_credit_lines',
                                   'insurance_policies', 'disbursements', 'chaos_fault_states'
                               )) AS table_count,
                            (SELECT EXISTS (
                                SELECT 1 FROM pg_roles WHERE rolname = 'godview_readonly'
                            )) AS role_exists
                        """
                    )
                    init_row = await cur.fetchone()
                    if (
                        isinstance(init_row, dict)
                        and int(init_row.get("table_count") or 0) == 9
                        and bool(init_row.get("role_exists"))
                    ):
                        await cur.execute("SELECT COUNT(*) AS vcnt FROM vehicles")
                        vrow = await cur.fetchone()
                        if isinstance(vrow, dict) and int(vrow.get("vcnt") or 0) >= 8:
                            return

                    for stmt in statements:
                        upper = " ".join(stmt.upper().split())
                        if upper.startswith("CREATE TABLE IF NOT EXISTS ") or upper.startswith(
                            "CREATE INDEX IF NOT EXISTS "
                        ):
                            tokens = stmt.split()
                            if len(tokens) >= 6:
                                rel_name = tokens[5].split("(")[0].strip()
                                await cur.execute(
                                    "SELECT to_regclass(%s) IS NOT NULL AS rel_exists",
                                    (f"public.{rel_name}",),
                                )
                                rel_row = await cur.fetchone()
                                if isinstance(rel_row, dict) and bool(rel_row.get("rel_exists")):
                                    continue
                        elif "CREATE ROLE GODVIEW_READONLY" in upper:
                            await cur.execute(
                                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'godview_readonly') AS role_exists"
                            )
                            rrow = await cur.fetchone()
                            if isinstance(rrow, dict) and bool(rrow.get("role_exists")):
                                continue
                        elif "REVOKE CLOUDSQLSUPERUSER FROM GODVIEW_READONLY" in upper:
                            await cur.execute(
                                """
                                SELECT EXISTS (
                                    SELECT 1
                                    FROM pg_auth_members m
                                    JOIN pg_roles r ON m.roleid = r.oid
                                    JOIN pg_roles u ON m.member = u.oid
                                    WHERE r.rolname = 'cloudsqlsuperuser'
                                      AND u.rolname = 'godview_readonly'
                                ) AS has_member
                                """
                            )
                            mrow = await cur.fetchone()
                            if not (isinstance(mrow, dict) and bool(mrow.get("has_member"))):
                                continue
                        elif upper.startswith("ALTER ROLE GODVIEW_READONLY SET "):
                            await cur.execute(
                                "SELECT rolconfig FROM pg_roles WHERE rolname = 'godview_readonly'"
                            )
                            crow = await cur.fetchone()
                            rolconfig = crow.get("rolconfig") if isinstance(crow, dict) else None
                            if isinstance(rolconfig, list):
                                cfg_joined = " ".join(str(x).lower() for x in rolconfig)
                                if (
                                    "DEFAULT_TRANSACTION_READ_ONLY" in upper
                                    and "default_transaction_read_only=on" in cfg_joined
                                ):
                                    continue
                                if (
                                    "STATEMENT_TIMEOUT" in upper
                                    and "statement_timeout=5s" in cfg_joined
                                ):
                                    continue
                        try:
                            await cur.execute(stmt)
                        except psycopg.Error as exc:
                            if "GODVIEW_READONLY" in upper or "CLOUDSQLSUPERUSER" in upper:
                                logger.info("Non-fatal role DDL notice during schema init: %s", exc)
                                continue
                            raise
                finally:
                    try:
                        await cur.execute("SELECT pg_advisory_unlock(84920174)")
                    except Exception:
                        pass

    async def list_vehicles(self) -> list[VehicleRow]:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT vehicle_id, make, model, category, ex_showroom_inr, "
                    "cdn_image_url, min_down_payment_pct FROM vehicles ORDER BY vehicle_id"
                )
                rows = await cur.fetchall()
                return [VehicleRow.model_validate(r) for r in rows]

    async def get_vehicle(self, vehicle_id: str) -> VehicleRow | None:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT vehicle_id, make, model, category, ex_showroom_inr, "
                    "cdn_image_url, min_down_payment_pct FROM vehicles WHERE vehicle_id = %s",
                    (vehicle_id,),
                )
                row = await cur.fetchone()
                return VehicleRow.model_validate(row) if row else None

    async def upsert_customer(self, customer: CustomerRow) -> CustomerRow:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO customers (
                        customer_id, full_name, dob, email, phone, city,
                        employment_type, employer_name, monthly_income_inr,
                        pan_number, aadhaar_number, is_ai_generated, ai_persona_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (customer_id) DO UPDATE SET
                        full_name = EXCLUDED.full_name,
                        email = EXCLUDED.email,
                        phone = EXCLUDED.phone,
                        city = EXCLUDED.city,
                        monthly_income_inr = EXCLUDED.monthly_income_inr,
                        is_ai_generated = EXCLUDED.is_ai_generated
                    """,
                    (
                        customer.customer_id,
                        customer.full_name,
                        customer.dob,
                        customer.email,
                        customer.phone,
                        customer.city,
                        customer.employment_type,
                        customer.employer_name,
                        customer.monthly_income_inr,
                        customer.pan_number,
                        customer.aadhaar_number,
                        customer.is_ai_generated,
                        json.dumps(customer.ai_persona_json)
                        if customer.ai_persona_json is not None
                        else None,
                        customer.created_at,
                    ),
                )
        return customer

    async def get_customer(self, customer_id: UUID) -> CustomerRow | None:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM customers WHERE customer_id = %s",
                    (customer_id,),
                )
                row = await cur.fetchone()
                return CustomerRow.model_validate(row) if row else None

    async def upsert_kyc_document(self, doc: KycDocumentRow) -> KycDocumentRow:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO kyc_documents (
                        kyc_id, customer_id, loan_id, kyc_mode,
                        pan_gcs_uri, aadhaar_gcs_uri, pan_image_model, aadhaar_image_model,
                        pan_verified, aadhaar_verified, model_armor_text_status,
                        model_armor_findings_json, verified_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (kyc_id) DO UPDATE SET
                        pan_verified = EXCLUDED.pan_verified,
                        aadhaar_verified = EXCLUDED.aadhaar_verified,
                        model_armor_text_status = EXCLUDED.model_armor_text_status,
                        model_armor_findings_json = EXCLUDED.model_armor_findings_json,
                        verified_at = EXCLUDED.verified_at
                    """,
                    (
                        doc.kyc_id,
                        doc.customer_id,
                        doc.loan_id,
                        doc.kyc_mode,
                        doc.pan_gcs_uri,
                        doc.aadhaar_gcs_uri,
                        doc.pan_image_model,
                        doc.aadhaar_image_model,
                        doc.pan_verified,
                        doc.aadhaar_verified,
                        doc.model_armor_text_status,
                        json.dumps(doc.model_armor_findings_json)
                        if doc.model_armor_findings_json is not None
                        else None,
                        doc.verified_at,
                    ),
                )
        return doc

    async def get_kyc_document_by_loan(self, loan_id: UUID) -> KycDocumentRow | None:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM kyc_documents WHERE loan_id = %s ORDER BY verified_at DESC NULLS LAST LIMIT 1",
                    (loan_id,),
                )
                row = await cur.fetchone()
                return KycDocumentRow.model_validate(row) if row else None

    async def get_latest_kyc_document_for_customer(
        self, customer_id: UUID
    ) -> KycDocumentRow | None:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM kyc_documents WHERE customer_id = %s ORDER BY verified_at DESC NULLS LAST LIMIT 1",
                    (customer_id,),
                )
                row = await cur.fetchone()
                return KycDocumentRow.model_validate(row) if row else None

    async def create_loan_application(self, loan: LoanApplicationRow) -> LoanApplicationRow:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO loan_applications (
                        loan_id, customer_id, vehicle_id, utility_type,
                        loan_amount_inr, down_payment_inr, tenure_months,
                        interest_rate_pct, emi_inr, loan_purpose_text,
                        employment_remarks, applicant_notes, status,
                        journey_id, trace_id, total_latency_ms, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        loan.loan_id,
                        loan.customer_id,
                        loan.vehicle_id,
                        loan.utility_type,
                        loan.loan_amount_inr,
                        loan.down_payment_inr,
                        loan.tenure_months,
                        loan.interest_rate_pct,
                        loan.emi_inr,
                        loan.loan_purpose_text,
                        loan.employment_remarks,
                        loan.applicant_notes,
                        loan.status,
                        loan.journey_id,
                        loan.trace_id,
                        loan.total_latency_ms,
                        loan.created_at,
                        loan.updated_at,
                    ),
                )
        return loan

    async def update_loan_status(
        self,
        loan_id: UUID,
        status: LoanStatus,
        *,
        emi_inr: Decimal | None = None,
        total_latency_ms: int | None = None,
        trace_id: str | None = None,
    ) -> LoanApplicationRow | None:
        now = utc_now()
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE loan_applications
                    SET status = %s,
                        emi_inr = COALESCE(%s, emi_inr),
                        total_latency_ms = COALESCE(%s, total_latency_ms),
                        trace_id = COALESCE(%s, trace_id),
                        updated_at = %s
                    WHERE loan_id = %s
                    RETURNING *
                    """,
                    (status, emi_inr, total_latency_ms, trace_id, now, loan_id),
                )
                row = await cur.fetchone()
                return LoanApplicationRow.model_validate(row) if row else None

    async def get_loan_application(self, loan_id: UUID) -> LoanApplicationRow | None:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM loan_applications WHERE loan_id = %s",
                    (loan_id,),
                )
                row = await cur.fetchone()
                return LoanApplicationRow.model_validate(row) if row else None

    async def get_loan_by_journey_id(self, journey_id: str) -> LoanApplicationRow | None:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM loan_applications WHERE journey_id = %s ORDER BY created_at DESC LIMIT 1",
                    (journey_id,),
                )
                row = await cur.fetchone()
                return LoanApplicationRow.model_validate(row) if row else None

    async def get_loan_by_trace_id(self, trace_id: str) -> LoanApplicationRow | None:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM loan_applications WHERE trace_id = %s ORDER BY created_at DESC LIMIT 1",
                    (trace_id,),
                )
                row = await cur.fetchone()
                return LoanApplicationRow.model_validate(row) if row else None

    async def list_loan_applications(
        self,
        *,
        status: str | None = None,
        utility_type: str | None = None,
        synthetic: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[LoanApplicationRow], int]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("l.status = %s")
            params.append(status)
        if utility_type:
            clauses.append("l.utility_type = %s")
            params.append(utility_type)
        if synthetic is not None:
            clauses.append("c.is_ai_generated = %s")
            params.append(synthetic)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT COUNT(*) AS cnt
                    FROM loan_applications l
                    JOIN customers c ON c.customer_id = l.customer_id
                    {where_sql}
                    """,
                    tuple(params),
                )
                cnt_row = await cur.fetchone()
                total = int(cnt_row["cnt"]) if cnt_row else 0

                await cur.execute(
                    f"""
                    SELECT l.*
                    FROM loan_applications l
                    JOIN customers c ON c.customer_id = l.customer_id
                    {where_sql}
                    ORDER BY l.created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    tuple([*params, limit, offset]),
                )
                rows = await cur.fetchall()
                return [LoanApplicationRow.model_validate(r) for r in rows], total

    async def save_underwriting_audit(
        self, audit: UnderwritingAuditRow
    ) -> UnderwritingAuditRow:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO underwriting_audits (
                        audit_id, loan_id, cibil_score, cibil_bureau_ref,
                        foir_pct, ltv_pct, rules_version, gke_pod_name,
                        gce_vm_name, decision, rejection_reason, evaluated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (audit_id) DO UPDATE SET
                        cibil_score = EXCLUDED.cibil_score,
                        foir_pct = EXCLUDED.foir_pct,
                        ltv_pct = EXCLUDED.ltv_pct,
                        rules_version = EXCLUDED.rules_version,
                        decision = EXCLUDED.decision,
                        rejection_reason = EXCLUDED.rejection_reason,
                        evaluated_at = EXCLUDED.evaluated_at
                    """,
                    (
                        audit.audit_id,
                        audit.loan_id,
                        audit.cibil_score,
                        audit.cibil_bureau_ref,
                        audit.foir_pct,
                        audit.ltv_pct,
                        audit.rules_version,
                        audit.gke_pod_name,
                        audit.gce_vm_name,
                        audit.decision,
                        audit.rejection_reason,
                        audit.evaluated_at,
                    ),
                )
        return audit

    async def get_underwriting_audit_by_loan(
        self, loan_id: UUID
    ) -> UnderwritingAuditRow | None:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM underwriting_audits WHERE loan_id = %s ORDER BY evaluated_at DESC LIMIT 1",
                    (loan_id,),
                )
                row = await cur.fetchone()
                return UnderwritingAuditRow.model_validate(row) if row else None

    async def save_commercial_credit_line(
        self, line: CommercialCreditLineRow
    ) -> CommercialCreditLineRow:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO commercial_credit_lines (
                        credit_line_id, customer_id, loan_id, vehicle_category,
                        fleet_size, requested_limit_inr, sanctioned_limit_inr,
                        utilisation_pct, decision, status, trace_id, evaluated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (credit_line_id) DO UPDATE SET
                        sanctioned_limit_inr = EXCLUDED.sanctioned_limit_inr,
                        decision = EXCLUDED.decision,
                        status = EXCLUDED.status,
                        evaluated_at = EXCLUDED.evaluated_at
                    """,
                    (
                        line.credit_line_id,
                        line.customer_id,
                        line.loan_id,
                        line.vehicle_category,
                        line.fleet_size,
                        line.requested_limit_inr,
                        line.sanctioned_limit_inr,
                        line.utilisation_pct,
                        line.decision,
                        line.status,
                        line.trace_id,
                        line.evaluated_at,
                    ),
                )
        return line

    async def get_commercial_credit_line_by_loan(
        self, loan_id: UUID
    ) -> CommercialCreditLineRow | None:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM commercial_credit_lines WHERE loan_id = %s ORDER BY evaluated_at DESC LIMIT 1",
                    (loan_id,),
                )
                row = await cur.fetchone()
                return CommercialCreditLineRow.model_validate(row) if row else None

    async def save_insurance_policy(self, policy: InsurancePolicyRow) -> InsurancePolicyRow:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO insurance_policies (
                        policy_id, customer_id, loan_id, policy_type,
                        fasttag_id, premium_inr, coverage_inr, status, trace_id, issued_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (policy_id) DO UPDATE SET
                        status = EXCLUDED.status
                    """,
                    (
                        policy.policy_id,
                        policy.customer_id,
                        policy.loan_id,
                        policy.policy_type,
                        policy.fasttag_id,
                        policy.premium_inr,
                        policy.coverage_inr,
                        policy.status,
                        policy.trace_id,
                        policy.issued_at,
                    ),
                )
        return policy

    async def get_insurance_policy_by_loan(self, loan_id: UUID) -> InsurancePolicyRow | None:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM insurance_policies WHERE loan_id = %s ORDER BY issued_at DESC LIMIT 1",
                    (loan_id,),
                )
                row = await cur.fetchone()
                return InsurancePolicyRow.model_validate(row) if row else None

    async def cancel_insurance_policy_by_loan(
        self, loan_id: UUID
    ) -> InsurancePolicyRow | None:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE insurance_policies SET status = 'CANCELLED' WHERE loan_id = %s RETURNING *",
                    (loan_id,),
                )
                row = await cur.fetchone()
                return InsurancePolicyRow.model_validate(row) if row else None

    async def save_disbursement(self, disbursement: DisbursementRow) -> DisbursementRow:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT disbursement_id FROM disbursements WHERE loan_id = %s ORDER BY attempts DESC LIMIT 1",
                    (disbursement.loan_id,),
                )
                existing_row = await cur.fetchone()
                target_disb_id = (
                    UUID(str(existing_row["disbursement_id"]))
                    if existing_row and existing_row.get("disbursement_id")
                    else disbursement.disbursement_id
                )
                effective = (
                    disbursement.model_copy(update={"disbursement_id": target_disb_id})
                    if target_disb_id != disbursement.disbursement_id
                    else disbursement
                )
                await cur.execute(
                    """
                    INSERT INTO disbursements (
                        disbursement_id, loan_id, sanction_letter_no, cbs_utr_number,
                        nach_mandate_id, disbursed_amount_inr, dealer_account_no,
                        status, attempts, replay_of, disbursed_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (disbursement_id) DO UPDATE SET
                        sanction_letter_no = EXCLUDED.sanction_letter_no,
                        cbs_utr_number = EXCLUDED.cbs_utr_number,
                        nach_mandate_id = EXCLUDED.nach_mandate_id,
                        status = EXCLUDED.status,
                        attempts = EXCLUDED.attempts,
                        replay_of = EXCLUDED.replay_of,
                        disbursed_at = EXCLUDED.disbursed_at
                    """,
                    (
                        effective.disbursement_id,
                        effective.loan_id,
                        effective.sanction_letter_no,
                        effective.cbs_utr_number,
                        effective.nach_mandate_id,
                        effective.disbursed_amount_inr,
                        effective.dealer_account_no,
                        effective.status,
                        effective.attempts,
                        effective.replay_of,
                        effective.disbursed_at,
                    ),
                )
        return effective

    async def get_disbursement_by_loan(self, loan_id: UUID) -> DisbursementRow | None:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM disbursements WHERE loan_id = %s ORDER BY attempts DESC LIMIT 1",
                    (loan_id,),
                )
                row = await cur.fetchone()
                return DisbursementRow.model_validate(row) if row else None

    async def list_disbursements_by_statuses(
        self, statuses: list[str]
    ) -> list[DisbursementRow]:
        wanted = [s.strip().upper() for s in statuses if s.strip()]
        if not wanted:
            return []
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM disbursements WHERE status = ANY(%s) ORDER BY attempts DESC",
                    (wanted,),
                )
                rows = await cur.fetchall()
                return [DisbursementRow.model_validate(r) for r in rows]

    async def upsert_chaos_fault(self, fault: ChaosFaultStateRow) -> ChaosFaultStateRow:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO chaos_fault_states (
                        fault_id, fault_class, fault_type, target_service,
                        is_active, parameters_json, triggered_by, activated_at, expires_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (fault_id) DO UPDATE SET
                        fault_class = EXCLUDED.fault_class,
                        fault_type = EXCLUDED.fault_type,
                        target_service = EXCLUDED.target_service,
                        is_active = EXCLUDED.is_active,
                        parameters_json = EXCLUDED.parameters_json,
                        triggered_by = EXCLUDED.triggered_by,
                        activated_at = EXCLUDED.activated_at,
                        expires_at = EXCLUDED.expires_at
                    """,
                    (
                        fault.fault_id,
                        fault.fault_class,
                        fault.fault_type,
                        fault.target_service,
                        fault.is_active,
                        json.dumps(fault.parameters_json)
                        if fault.parameters_json is not None
                        else None,
                        fault.triggered_by,
                        fault.activated_at,
                        fault.expires_at,
                    ),
                )
        return fault

    async def clear_chaos_fault(self, fault_id: str) -> ChaosFaultStateRow | None:
        now = datetime.now(UTC)
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE chaos_fault_states
                    SET is_active = false, expires_at = %s
                    WHERE fault_id = %s OR fault_type = %s
                    RETURNING *
                    """,
                    (now, fault_id, fault_id),
                )
                row = await cur.fetchone()
                return ChaosFaultStateRow.model_validate(row) if row else None

    async def list_active_chaos_faults(self) -> list[ChaosFaultStateRow]:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT * FROM chaos_fault_states
                    WHERE is_active = true
                      AND (expires_at IS NULL OR expires_at > NOW())
                    ORDER BY activated_at DESC NULLS LAST
                    """
                )
                rows = await cur.fetchall()
                return [ChaosFaultStateRow.model_validate(r) for r in rows]

    def record_trace_span(self, trace_id: str, span: TraceSpanViewModel) -> None:
        bucket = self._trace_spans.setdefault(trace_id, [])
        bucket.append(span)

    def get_recorded_trace_spans(self, trace_id: str) -> list[TraceSpanViewModel]:
        return list(self._trace_spans.get(trace_id, []))

    def get_pause_state(self) -> tuple[bool | None, str | None]:
        try:
            with psycopg.connect(
                self.database_url,
                autocommit=True,
                row_factory=dict_row,
                connect_timeout=2,
            ) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT obj_description(to_regclass('public.loan_applications'), 'pg_class') AS comment_json"
                    )
                    row = cur.fetchone()
                    raw_comment = row.get("comment_json") if row else None
                    if isinstance(raw_comment, str) and raw_comment.strip().startswith("{"):
                        parsed = json.loads(raw_comment)
                        if "paused" in parsed:
                            self._paused = bool(parsed["paused"])
                            self._pause_reason = parsed.get("reason")
                            return self._paused, self._pause_reason
        except Exception as exc:
            logger.debug("Database comment pause lookup skipped: %s", exc)

        if self._pause_state_file.is_file():
            try:
                all_states = json.loads(self._pause_state_file.read_text(encoding="utf-8"))
                if isinstance(all_states, dict) and self.database_url in all_states:
                    entry = all_states[self.database_url]
                    if isinstance(entry, dict) and "paused" in entry:
                        self._paused = bool(entry["paused"])
                        self._pause_reason = entry.get("reason")
                        return self._paused, self._pause_reason
            except Exception as exc:
                logger.warning("Failed reading pause state file %s: %s", self._pause_state_file, exc)

        return self._paused, self._pause_reason

    def set_pause_state(self, paused: bool, reason: str | None) -> None:
        self._paused = paused
        self._pause_reason = reason
        payload_json = json.dumps({"paused": paused, "reason": reason})

        try:
            all_states: dict[str, Any] = {}
            if self._pause_state_file.is_file():
                existing = json.loads(self._pause_state_file.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    all_states = existing
            all_states[self.database_url] = {"paused": paused, "reason": reason}
            self._pause_state_file.parent.mkdir(parents=True, exist_ok=True)
            self._pause_state_file.write_text(json.dumps(all_states), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed writing pause state file %s: %s", self._pause_state_file, exc)

        try:
            escaped = payload_json.replace("'", "''")
            with psycopg.connect(
                self.database_url,
                autocommit=True,
                row_factory=dict_row,
                connect_timeout=2,
            ) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.loan_applications') AS reg")
                    reg_row = cur.fetchone()
                    if isinstance(reg_row, dict) and "reg" in reg_row and reg_row["reg"] is None:
                        return
                    cur.execute(f"COMMENT ON TABLE loan_applications IS '{escaped}'")
        except Exception as exc:
            logger.debug("Database comment pause persist skipped: %s", exc)
