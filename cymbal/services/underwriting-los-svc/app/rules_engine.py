"""Deterministic underwriting rules engine for Cymbal Retail Passenger & EV Loans.

Implements TDD §2.1, §3.2, §4 UC-07, and §6.2 #7:
- Exact reducing-balance EMI calculation
- Exact FOIR % and LTV % calculation
- Approval iff cibil_score >= 680 AND foir_pct <= 60.00
- Exposes threshold constants MIN_CIBIL_SCORE, MAX_FOIR_PCT, and RULES_VERSION (plus rules_version alias)
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path


def _resolve_rules_version() -> str:
    """Resolve deterministic rules_version from build-time env or SHA-256 of rules_engine.py."""
    for env_key in ("RULES_VERSION", "GIT_SHA", "BUILD_ID"):
        val = os.environ.get(env_key, "").strip()
        if val:
            return val
    source_bytes = Path(__file__).read_bytes()
    return hashlib.sha256(source_bytes).hexdigest()[:12]


MIN_CIBIL_SCORE: int = 680
MAX_FOIR_PCT: Decimal = Decimal("60.00")
DEFAULT_MAX_LTV_PCT: Decimal = Decimal("90.00")
RULES_VERSION: str = _resolve_rules_version()
rules_version: str = RULES_VERSION


@dataclass(frozen=True)
class UnderwritingEvaluationResult:
    """Deterministic output of the underwriting rules engine."""

    emi_inr: Decimal
    foir_pct: Decimal
    ltv_pct: Decimal
    cibil_score: int
    decision: str
    status: str
    rejection_reason: str | None
    rules_version: str


def calculate_emi_inr(
    loan_amount_inr: Decimal | float | int | str,
    interest_rate_pct: Decimal | float | int | str,
    tenure_months: int,
) -> Decimal:
    """Calculate exact monthly reducing-balance EMI in INR, quantized to 0.01."""
    principal = Decimal(str(loan_amount_inr))
    if principal <= Decimal("0"):
        raise ValueError("loan_amount_inr must be positive")
    if tenure_months <= 0:
        raise ValueError("tenure_months must be positive")

    annual_rate = Decimal(str(interest_rate_pct))
    if annual_rate < Decimal("0"):
        raise ValueError("interest_rate_pct cannot be negative")

    n = int(tenure_months)
    if annual_rate == Decimal("0"):
        return (principal / Decimal(str(n))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    monthly_rate = annual_rate / Decimal("1200")
    factor = (Decimal("1") + monthly_rate) ** n
    emi = (principal * monthly_rate * factor) / (factor - Decimal("1"))
    return emi.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_foir_pct(
    emi_inr: Decimal | float | int | str,
    monthly_income_inr: Decimal | float | int | str,
    existing_emi_inr: Decimal | float | int | str = Decimal("0.00"),
) -> Decimal:
    """Calculate Fixed Obligation to Income Ratio (FOIR) percentage, quantized to 0.01."""
    income = Decimal(str(monthly_income_inr))
    if income <= Decimal("0"):
        raise ValueError("monthly_income_inr must be positive")
    total_obligation = Decimal(str(emi_inr)) + Decimal(str(existing_emi_inr))
    foir = (total_obligation / income) * Decimal("100")
    return foir.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_ltv_pct(
    loan_amount_inr: Decimal | float | int | str,
    ex_showroom_inr: Decimal | float | int | str,
) -> Decimal:
    """Calculate Loan-to-Value (LTV) percentage against ex-showroom / vehicle price, quantized to 0.01."""
    vehicle_value = Decimal(str(ex_showroom_inr))
    if vehicle_value <= Decimal("0"):
        raise ValueError("ex_showroom_inr must be positive")
    principal = Decimal(str(loan_amount_inr))
    ltv = (principal / vehicle_value) * Decimal("100")
    return ltv.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def evaluate_underwriting_rules(
    *,
    loan_amount_inr: Decimal | float | int | str,
    down_payment_inr: Decimal | float | int | str,
    tenure_months: int,
    interest_rate_pct: Decimal | float | int | str,
    monthly_income_inr: Decimal | float | int | str,
    cibil_score: int,
    ex_showroom_inr: Decimal | float | int | str | None = None,
    existing_emi_inr: Decimal | float | int | str = Decimal("0.00"),
) -> UnderwritingEvaluationResult:
    """Evaluate Cymbal retail passenger & EV vehicle loan underwriting rules.

    Approval iff `cibil_score >= MIN_CIBIL_SCORE (680) AND foir_pct <= MAX_FOIR_PCT (60.00)`.
    """
    principal = Decimal(str(loan_amount_inr))
    down_payment = Decimal(str(down_payment_inr))
    vehicle_price = (
        Decimal(str(ex_showroom_inr))
        if ex_showroom_inr is not None and Decimal(str(ex_showroom_inr)) > Decimal("0")
        else (principal + down_payment)
    )

    emi_inr = calculate_emi_inr(
        loan_amount_inr=principal,
        interest_rate_pct=interest_rate_pct,
        tenure_months=tenure_months,
    )
    foir_pct = calculate_foir_pct(
        emi_inr=emi_inr,
        monthly_income_inr=monthly_income_inr,
        existing_emi_inr=existing_emi_inr,
    )
    ltv_pct = calculate_ltv_pct(
        loan_amount_inr=principal,
        ex_showroom_inr=vehicle_price,
    )

    score_int = int(cibil_score)
    reasons: list[str] = []
    if score_int < MIN_CIBIL_SCORE:
        reasons.append(f"CIBIL score {score_int} is below minimum threshold {MIN_CIBIL_SCORE}")
    if foir_pct > MAX_FOIR_PCT:
        reasons.append(f"FOIR {foir_pct}% exceeds maximum threshold {MAX_FOIR_PCT}%")

    if not reasons:
        return UnderwritingEvaluationResult(
            emi_inr=emi_inr,
            foir_pct=foir_pct,
            ltv_pct=ltv_pct,
            cibil_score=score_int,
            decision="APPROVED",
            status="APPROVED",
            rejection_reason=None,
            rules_version=RULES_VERSION,
        )

    return UnderwritingEvaluationResult(
        emi_inr=emi_inr,
        foir_pct=foir_pct,
        ltv_pct=ltv_pct,
        cibil_score=score_int,
        decision="REJECTED",
        status="REJECTED",
        rejection_reason="; ".join(reasons),
        rules_version=RULES_VERSION,
    )
