"""HTTP client for Utility 3 (`core-banking-cibil-vm:8085`) with W3C TraceContext & Baggage propagation."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from godview_shared.otel import extract_baggage_context, inject_baggage_headers

logger = logging.getLogger(__name__)


class CibilScoreEndpointError(RuntimeError):
    """Typed error raised when core-banking-cibil-vm `/internal/cibil-cbs/score` fails or returns >=400."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def resolve_default_cbs_base_url() -> str:
    """Resolve core-banking-cibil-vm base URL from environment variables."""
    for env_key in (
        "CIBIL_CBS_URL",
        "CBS_VM_URL",
        "CIBIL_SERVICE_URL",
        "CBS_SERVICE_URL",
        "CORE_BANKING_CIBIL_URL",
    ):
        val = os.environ.get(env_key, "").strip()
        if val:
            return val.rstrip("/")

    host = os.environ.get("CBS_VM_HOST", "").strip()
    if host:
        port = os.environ.get("CBS_VM_PORT", "8085").strip() or "8085"
        return f"http://{host}:{port}"

    return "http://core-banking-cibil-vm:8085"


@dataclass(frozen=True)
class CibilScoreResult:
    """Credit score response from core-banking-cibil-vm."""

    cibil_score: int
    cibil_bureau_ref: str
    gce_vm_name: str = "core-banking-cibil-vm"


class CibilScoreClient:
    """Calls `POST /internal/cibil-cbs/score` on `core-banking-cibil-vm:8085` propagating W3C headers."""

    def __init__(
        self,
        base_url: str | None = None,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.base_url = (base_url or resolve_default_cbs_base_url()).rstrip("/")
        self._has_custom_http = http_client is not None
        self._http = http_client or httpx.Client(timeout=timeout_seconds)

    def fetch_score(
        self,
        *,
        loan_id: str,
        pan_number: str,
        customer_id: str | None = None,
        journey_id: str | None = None,
        monthly_income_inr: float | None = None,
        loan_amount_inr: float | None = None,
        synthetic: bool = False,
        inbound_headers: dict[str, str] | None = None,
    ) -> CibilScoreResult:
        """Call `POST /internal/cibil-cbs/score` with propagated traceparent, baggage, and X-Synthetic.

        Never falls back to `/internal/cibil-cbs/score-and-disburse` so underwriting cannot
        accidentally trigger a disbursement side-effect on an unapproved loan.
        """
        if os.environ.get("GODVIEW_ENV") == "hermetic" and not self._has_custom_http:
            score = 620 if (pan_number or "").strip().upper().startswith("LOW") else 760
            return CibilScoreResult(
                cibil_score=score,
                cibil_bureau_ref=f"CIBIL-HERMETIC-{loan_id[:8]}",
                gce_vm_name="core-banking-cibil-vm",
            )

        extracted = extract_baggage_context(inbound_headers or {})
        effective_journey_id = journey_id or extracted.get("journey.id")
        effective_customer_id = customer_id or extracted.get("customer.id")
        effective_synthetic = bool(synthetic or extracted.get("synthetic"))
        effective_traceparent = extracted.get("traceparent")

        outbound_headers = inject_baggage_headers(
            journey_id=effective_journey_id,
            customer_id=effective_customer_id,
            synthetic=effective_synthetic,
            traceparent=effective_traceparent,
        )
        outbound_headers["Content-Type"] = "application/json"

        payload: dict[str, Any] = {
            "loan_id": loan_id,
            "pan_number": pan_number,
            "customer_id": effective_customer_id,
            "journey_id": effective_journey_id,
            "monthly_income_inr": monthly_income_inr,
            "loan_amount_inr": loan_amount_inr,
            "synthetic": effective_synthetic,
        }

        score_url = f"{self.base_url}/internal/cibil-cbs/score"
        try:
            resp = self._http.post(score_url, headers=outbound_headers, json=payload)
        except httpx.HTTPError as exc:
            raise CibilScoreEndpointError(
                f"CBS score request to {score_url} failed: {type(exc).__name__}: {exc}"
            ) from exc

        if resp.status_code >= 400:
            raise CibilScoreEndpointError(
                f"CBS score endpoint {score_url} returned HTTP {resp.status_code}: {resp.text}",
                status_code=resp.status_code,
            )

        data = resp.json()
        return CibilScoreResult(
            cibil_score=int(data["cibil_score"]),
            cibil_bureau_ref=str(data.get("cibil_bureau_ref") or f"CIBIL-REF-{loan_id[:8]}"),
            gce_vm_name=str(data.get("gce_vm_name") or "core-banking-cibil-vm"),
        )
