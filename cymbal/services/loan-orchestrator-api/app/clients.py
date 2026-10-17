"""Downstream internal utility HTTP clients and L1/L2 mock bundle (TDD §3.2)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

import httpx
from godview_shared.config import MODEL_TABLE

from app.config import OrchestratorSettings
from app.models import (
    CommercialCreditLineRow,
    DisbursementRow,
    InsurancePolicyRow,
    KycDocumentRow,
    UnderwritingAuditRow,
)
from app.repository import OrchestratorRepository

logger = logging.getLogger(__name__)

ADVERSARIAL_KEYWORDS = (
    "system override",
    "ignore all underwriting",
    "ignore previous instructions",
    "bypass",
    "jailbreak",
    "approve immediately",
    "drop table",
)


@dataclass(slots=True)
class DownstreamCallRecord:
    """Recorded downstream call for test verification of ordering and W3C propagation."""

    step: str
    url: str
    payload: dict[str, Any]
    headers: dict[str, str]
    status_code: int = 200


class DownstreamServiceError(RuntimeError):
    """Raised when a downstream utility returns a non-2xx HTTP status or times out."""

    def __init__(self, step: str, status_code: int, detail: str) -> None:
        super().__init__(f"{step} failed with HTTP {status_code}: {detail}")
        self.step = step
        self.status_code = status_code
        self.detail = detail


class DownstreamServiceBundle(Protocol):
    """Protocol for calling the 5 Cymbal Auto Finance internal utilities."""

    call_log: list[DownstreamCallRecord]

    async def verify_kyc(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]: ...

    async def evaluate_underwriting(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]: ...

    async def evaluate_fleet_credit(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]: ...

    async def issue_insurance(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]: ...

    async def disburse_loan(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]: ...

    async def activate_chaos_fault(
        self,
        target_service: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...

    async def clear_chaos_fault(
        self,
        target_service: str,
        fault_type: str,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...


class HttpDownstreamServiceBundle:
    """Production async HTTP client bundle calling the 5 internal utility endpoints."""

    def __init__(self, settings: OrchestratorSettings) -> None:
        self.settings = settings
        self.call_log: list[DownstreamCallRecord] = []

    def _attach_oidc_token(
        self, base_url: str, headers: dict[str, str] | None = None
    ) -> dict[str, str]:
        out = dict(headers or {})
        env_mode = os.getenv("GODVIEW_ENV", "").strip().lower()
        if "Authorization" not in out and (env_mode == "gcp" or os.getenv("K_SERVICE")):
            try:
                from google.auth.transport.requests import Request as GoogleAuthRequest
                from google.oauth2 import id_token

                token = id_token.fetch_id_token(GoogleAuthRequest(), base_url.rstrip("/"))
                if token:
                    out["Authorization"] = f"Bearer {token}"
            except Exception as exc:
                logger.warning(
                    "Could not fetch OIDC ID token for outbound call to %s: %s",
                    base_url,
                    exc,
                )
        return out

    async def _post_json(
        self,
        step: str,
        base_url: str,
        path: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        url = f"{base_url.rstrip('/')}{path}"
        req_headers = self._attach_oidc_token(base_url, headers)
        timeout = httpx.Timeout(self.settings.downstream_timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=req_headers)
        except httpx.TimeoutException as exc:
            self.call_log.append(
                DownstreamCallRecord(
                    step=step,
                    url=url,
                    payload=payload,
                    headers=dict(req_headers),
                    status_code=504,
                )
            )
            raise DownstreamServiceError(step, 504, f"Downstream timeout calling {url}: {exc}") from exc
        except httpx.HTTPError as exc:
            self.call_log.append(
                DownstreamCallRecord(
                    step=step,
                    url=url,
                    payload=payload,
                    headers=dict(req_headers),
                    status_code=503,
                )
            )
            raise DownstreamServiceError(step, 503, f"Downstream error calling {url}: {exc}") from exc

        self.call_log.append(
            DownstreamCallRecord(
                step=step,
                url=url,
                payload=payload,
                headers=dict(req_headers),
                status_code=resp.status_code,
            )
        )
        if resp.status_code >= 400:
            raise DownstreamServiceError(step, resp.status_code, resp.text)
        data = resp.json()
        return data if isinstance(data, dict) else {"result": data}

    async def verify_kyc(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        return await self._post_json(
            "KYC",
            self.settings.kyc_svc_url,
            "/internal/kyc/verify",
            payload,
            headers,
        )

    async def evaluate_underwriting(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        return await self._post_json(
            "LOS",
            self.settings.underwriting_svc_url,
            "/internal/underwriting/evaluate",
            payload,
            headers,
        )

    async def evaluate_fleet_credit(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        return await self._post_json(
            "FLEET",
            self.settings.fleet_credit_svc_url,
            "/internal/fleet-credit/evaluate",
            payload,
            headers,
        )

    async def issue_insurance(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        return await self._post_json(
            "INS",
            self.settings.insurance_svc_url,
            "/internal/insurance/issue",
            payload,
            headers,
        )

    async def disburse_loan(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        return await self._post_json(
            "CBS",
            self.settings.cbs_vm_url,
            "/internal/cibil-cbs/disburse",
            payload,
            headers,
        )

    def _resolve_gke_target_url(self, target_service: str) -> str | None:
        normalized = target_service.strip().lower()
        if normalized == "kyc-model-armor-svc":
            return self.settings.kyc_svc_url
        if normalized == "underwriting-los-svc":
            return self.settings.underwriting_svc_url
        return None

    def _build_chaos_headers(
        self, target_url: str, headers: dict[str, str] | None = None
    ) -> dict[str, str]:
        out = self._attach_oidc_token(target_url, headers)
        out.setdefault("X-Chaos-Orchestrator-Service", self.settings.service_name)
        return out

    async def activate_chaos_fault(
        self,
        target_service: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        base_url = self._resolve_gke_target_url(target_service)
        if base_url is None:
            return {"target_service": target_service, "forwarded": False}
        hdrs = self._build_chaos_headers(base_url, headers)
        return await self._post_json(
            "CHAOS_ACTIVATE",
            base_url,
            "/internal/chaos/faults",
            payload,
            hdrs,
        )

    async def clear_chaos_fault(
        self,
        target_service: str,
        fault_type: str,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        base_url = self._resolve_gke_target_url(target_service)
        if base_url is None:
            return {"target_service": target_service, "forwarded": False}
        url = f"{base_url.rstrip('/')}/internal/chaos/faults/{fault_type}"
        hdrs = self._build_chaos_headers(base_url, headers)
        timeout = httpx.Timeout(self.settings.downstream_timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.delete(url, headers=hdrs)
        except httpx.TimeoutException as exc:
            self.call_log.append(
                DownstreamCallRecord(
                    step="CHAOS_CLEAR",
                    url=url,
                    payload={"fault_type": fault_type},
                    headers=dict(hdrs),
                    status_code=504,
                )
            )
            raise DownstreamServiceError(
                "CHAOS_CLEAR", 504, f"Downstream timeout calling {url}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            self.call_log.append(
                DownstreamCallRecord(
                    step="CHAOS_CLEAR",
                    url=url,
                    payload={"fault_type": fault_type},
                    headers=dict(hdrs),
                    status_code=503,
                )
            )
            raise DownstreamServiceError(
                "CHAOS_CLEAR", 503, f"Downstream error calling {url}: {exc}"
            ) from exc

        self.call_log.append(
            DownstreamCallRecord(
                step="CHAOS_CLEAR",
                url=url,
                payload={"fault_type": fault_type},
                headers=dict(hdrs),
                status_code=resp.status_code,
            )
        )
        if resp.status_code >= 400:
            raise DownstreamServiceError("CHAOS_CLEAR", resp.status_code, resp.text)
        data = resp.json()
        return data if isinstance(data, dict) else {"result": data}


@dataclass
class MockDownstreamServiceBundle:
    """Deterministic in-process mock of the 5 internal utilities for L1/L2 testing."""

    repo: OrchestratorRepository
    call_log: list[DownstreamCallRecord] = field(default_factory=list)
    simulate_cbs_504: bool = False
    foir_max_pct: Decimal = Decimal("60.00")
    rules_version: str = "git-sha-f81d4fae"

    async def verify_kyc(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        url = "http://kyc-model-armor-svc:8080/internal/kyc/verify"
        self.call_log.append(
            DownstreamCallRecord(
                step="KYC", url=url, payload=dict(payload), headers=dict(headers)
            )
        )
        combined_text = " ".join(
            str(payload.get(k) or "")
            for k in ("loan_purpose_text", "employment_remarks", "applicant_notes")
        ).lower()
        blocked = any(token in combined_text for token in ADVERSARIAL_KEYWORDS)
        customer_id = UUID(str(payload["customer_id"]))
        loan_id = UUID(str(payload["loan_id"]))
        kyc_mode = str(payload.get("kyc_mode", "TEXT_ONLY"))
        pan_uri = payload.get("pan_gcs_uri") or (
            f"gs://general-ak-god-view-kyc-vault/specimens/{customer_id}/pan.png"
            if kyc_mode == "IMAGE"
            else None
        )
        aadhaar_uri = payload.get("aadhaar_gcs_uri") or (
            f"gs://general-ak-god-view-kyc-vault/specimens/{customer_id}/aadhaar.png"
            if kyc_mode == "IMAGE"
            else None
        )

        default_img_model = MODEL_TABLE["specimen_image"].model_id
        doc = KycDocumentRow(
            kyc_id=uuid4(),
            customer_id=customer_id,
            loan_id=loan_id,
            kyc_mode=kyc_mode,  # type: ignore[arg-type]
            pan_gcs_uri=pan_uri,
            aadhaar_gcs_uri=aadhaar_uri,
            pan_image_model=(
                str(payload.get("pan_image_model") or default_img_model)
                if kyc_mode == "IMAGE"
                else None
            ),
            aadhaar_image_model=(
                str(payload.get("aadhaar_image_model") or default_img_model)
                if kyc_mode == "IMAGE"
                else None
            ),
            pan_verified=not blocked,
            aadhaar_verified=not blocked,
            model_armor_text_status="BLOCKED" if blocked else "SANITIZED",
            model_armor_findings_json={
                "template": "cymbal-text-guard",
                "blocked": blocked,
                "reason": "PROMPT_INJECTION_DETECTED" if blocked else "CLEAN",
            },
            verified_at=datetime.now(UTC),
        )
        await self.repo.upsert_kyc_document(doc)
        return {
            **doc.model_dump(mode="json"),
            "status": "MODEL_ARMOR_BLOCKED" if blocked else "KYC_VERIFIED",
            "event_type": "armor.blocked" if blocked else "kyc.verified",
            "blocked": blocked,
        }

    async def evaluate_underwriting(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        url = "http://underwriting-los-svc:8080/internal/underwriting/evaluate"
        self.call_log.append(
            DownstreamCallRecord(
                step="LOS", url=url, payload=dict(payload), headers=dict(headers)
            )
        )
        loan_id = UUID(str(payload["loan_id"]))
        monthly_income = Decimal(str(payload.get("monthly_income_inr", "145000")))
        existing_ob = Decimal(str(payload.get("existing_obligations_inr", "0")))
        emi_inr = Decimal(str(payload["emi_inr"]))
        loan_amount = Decimal(str(payload["loan_amount_inr"]))
        ex_showroom = Decimal(str(payload.get("ex_showroom_inr", loan_amount + Decimal("200000"))))

        foir_pct = (
            ((existing_ob + emi_inr) / monthly_income) * Decimal("100")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        ltv_pct = ((loan_amount / ex_showroom) * Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        cibil_score = int(payload.get("cibil_score", 762))
        approved = foir_pct <= self.foir_max_pct and cibil_score >= 680 and ltv_pct <= Decimal("95.00")
        decision = "APPROVED" if approved else "REJECTED"
        rejection_reason = (
            None
            if approved
            else f"FOIR {foir_pct}% > {self.foir_max_pct}% or CIBIL {cibil_score} < 680"
        )
        audit = UnderwritingAuditRow(
            audit_id=uuid4(),
            loan_id=loan_id,
            cibil_score=cibil_score,
            cibil_bureau_ref=f"CIBIL-REF-{str(loan_id)[:8].upper()}",
            foir_pct=foir_pct,
            ltv_pct=ltv_pct,
            rules_version=self.rules_version,
            gke_pod_name="underwriting-los-svc-7d9c8b-x4k2p",
            gce_vm_name="core-banking-cibil-vm",
            decision=decision,  # type: ignore[arg-type]
            rejection_reason=rejection_reason,
            evaluated_at=datetime.now(UTC),
        )
        await self.repo.save_underwriting_audit(audit)
        return {
            **audit.model_dump(mode="json"),
            "emi_inr": float(emi_inr),
            "status": decision,
            "event_type": "loan.decided",
        }

    async def evaluate_fleet_credit(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        url = "http://commercial-fleet-credit-svc:8080/internal/fleet-credit/evaluate"
        self.call_log.append(
            DownstreamCallRecord(
                step="FLEET", url=url, payload=dict(payload), headers=dict(headers)
            )
        )
        loan_id = UUID(str(payload["loan_id"]))
        customer_id = UUID(str(payload["customer_id"]))
        requested = Decimal(str(payload.get("requested_limit_inr") or payload["loan_amount_inr"]))
        sanctioned = requested
        line = CommercialCreditLineRow(
            credit_line_id=uuid4(),
            customer_id=customer_id,
            loan_id=loan_id,
            vehicle_category=payload.get("vehicle_category", "COMMERCIAL_FLEET"),
            fleet_size=int(payload.get("fleet_size") or 5),
            requested_limit_inr=requested,
            sanctioned_limit_inr=sanctioned,
            utilisation_pct=Decimal("68.50"),
            decision="APPROVED",
            status="SANCTIONED",
            trace_id=payload.get("trace_id"),
            evaluated_at=datetime.now(UTC),
        )
        await self.repo.save_commercial_credit_line(line)
        return {
            **line.model_dump(mode="json"),
            "event_type": "credit_line.decided",
        }

    async def issue_insurance(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        url = "http://motor-insurance-svc:8080/internal/insurance/issue"
        self.call_log.append(
            DownstreamCallRecord(
                step="INS", url=url, payload=dict(payload), headers=dict(headers)
            )
        )
        if payload.get("insurance_no_policy_write"):
            # UC-05 drill: HTTP 200 returned, but no policy row written and no insurance.issued event
            return {
                "status": "SKIPPED_NO_POLICY_WRITE",
                "policy_written": False,
                "loan_id": str(payload["loan_id"]),
            }

        loan_id = UUID(str(payload["loan_id"]))
        customer_id = UUID(str(payload["customer_id"]))
        coverage = Decimal(str(payload.get("coverage_inr") or payload["loan_amount_inr"]))
        premium = (coverage * Decimal("0.025")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        policy = InsurancePolicyRow(
            policy_id=uuid4(),
            customer_id=customer_id,
            loan_id=loan_id,
            policy_type="COMPREHENSIVE_MOTOR_3Y",
            fasttag_id=f"FT-CYM-{str(loan_id)[:8].upper()}",
            premium_inr=premium,
            coverage_inr=coverage,
            status="ACTIVE",
            trace_id=payload.get("trace_id"),
            issued_at=datetime.now(UTC),
        )
        await self.repo.save_insurance_policy(policy)
        return {
            **policy.model_dump(mode="json"),
            "policy_written": True,
            "event_type": "insurance.issued",
        }

    async def disburse_loan(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        url = "http://core-banking-cibil-vm:8085/internal/cibil-cbs/disburse"
        loan_id = UUID(str(payload["loan_id"]))

        if self.simulate_cbs_504:
            self.call_log.append(
                DownstreamCallRecord(
                    step="CBS",
                    url=url,
                    payload=dict(payload),
                    headers=dict(headers),
                    status_code=504,
                )
            )
            raise DownstreamServiceError(
                "CBS", 504, "Simulated gce_vm_latency_504 timeout on core-banking-cibil-vm"
            )

        self.call_log.append(
            DownstreamCallRecord(
                step="CBS", url=url, payload=dict(payload), headers=dict(headers)
            )
        )
        existing = await self.repo.get_disbursement_by_loan(loan_id)
        is_replay = bool(payload.get("is_replay", False))
        if existing and existing.status in ("DISBURSED", "REPLAYED"):
            return {
                **existing.model_dump(mode="json"),
                "cibil_score": 762,
                "cibil_bureau_ref": f"CIBIL-REF-{str(loan_id)[:8].upper()}",
                "idempotent_replay": True,
                "event_type": "disbursement.settled",
            }

        attempts = (existing.attempts + 1) if existing else 1
        disb_id = existing.disbursement_id if existing else uuid4()
        replay_of = existing.disbursement_id if (existing and is_replay) else None
        status = "REPLAYED" if is_replay else "DISBURSED"
        row = DisbursementRow(
            disbursement_id=disb_id,
            loan_id=loan_id,
            sanction_letter_no=f"SL-CYM-{str(loan_id)[:8].upper()}",
            cbs_utr_number=f"UTR{str(loan_id).replace('-', '')[:12].upper()}",
            nach_mandate_id=f"NACH-CYM-{str(loan_id)[:8].upper()}",
            disbursed_amount_inr=Decimal(str(payload["loan_amount_inr"])),
            dealer_account_no=str(payload.get("dealer_account_no", "CYMBAL-DLR-0001")),
            status=status,  # type: ignore[arg-type]
            attempts=attempts,
            replay_of=replay_of,
            disbursed_at=datetime.now(UTC),
        )
        await self.repo.save_disbursement(row)
        return {
            **row.model_dump(mode="json"),
            "cibil_score": 762,
            "cibil_bureau_ref": f"CIBIL-REF-{str(loan_id)[:8].upper()}",
            "idempotent_replay": False,
            "event_type": "disbursement.settled",
        }

    async def activate_chaos_fault(
        self,
        target_service: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        normalized = target_service.strip().lower()
        if normalized not in ("kyc-model-armor-svc", "underwriting-los-svc"):
            return {"target_service": target_service, "forwarded": False}
        hdrs = dict(headers or {})
        hdrs.setdefault("X-Chaos-Orchestrator-Service", "loan-orchestrator-api")
        url = f"http://{normalized}:8080/internal/chaos/faults"
        self.call_log.append(
            DownstreamCallRecord(
                step="CHAOS_ACTIVATE",
                url=url,
                payload=dict(payload),
                headers=hdrs,
            )
        )
        return {
            "target_service": normalized,
            "forwarded": True,
            "is_active": True,
            **payload,
        }

    async def clear_chaos_fault(
        self,
        target_service: str,
        fault_type: str,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        normalized = target_service.strip().lower()
        if normalized not in ("kyc-model-armor-svc", "underwriting-los-svc"):
            return {"target_service": target_service, "forwarded": False}
        hdrs = dict(headers or {})
        hdrs.setdefault("X-Chaos-Orchestrator-Service", "loan-orchestrator-api")
        url = f"http://{normalized}:8080/internal/chaos/faults/{fault_type}"
        self.call_log.append(
            DownstreamCallRecord(
                step="CHAOS_CLEAR",
                url=url,
                payload={"fault_type": fault_type},
                headers=hdrs,
            )
        )
        return {
            "target_service": normalized,
            "fault_type": fault_type,
            "forwarded": True,
            "is_active": False,
        }
