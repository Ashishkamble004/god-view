"""Text-only Model Armor scanner, deterministic fallback ruleset, and image-bypass metadata checks.

Enforces SDD §8.1 and TDD §4 UC-12, UC-13, UC-25:
- Model Armor inspects ONLY `loan_purpose_text`, `employment_remarks`, `applicant_notes`.
- Specimen KYC card images (`pan_gcs_uri`, `aadhaar_gcs_uri`, PNG bytes) are NEVER sent to
  Model Armor or any model; only GCS URI scheme and 8-byte PNG header checks run on IMAGE mode.
- Raw PAN and Aadhaar numbers are masked before logging (UC-13).
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

logger = logging.getLogger("kyc-model-armor-svc")

DEFAULT_MODEL_ARMOR_TEMPLATE_ID = (
    "projects/general-ak/locations/us-central1/templates/cymbal-text-guard"
)
MODEL_ARMOR_BASE_URL = "https://modelarmor.us-central1.rep.googleapis.com/v1"
SCANNED_TEXT_FIELDS: tuple[str, ...] = (
    "loan_purpose_text",
    "employment_remarks",
    "applicant_notes",
)

PNG_MAGIC_HEADER = b"\x89PNG\r\n\x1a\n"
PAN_REGEX = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$")
AADHAAR_REGEX = re.compile(r"^[0-9]{12}$")


def mask_pan(pan: str) -> str:
    """Mask PAN as ABCDE****Z so raw PAN is never written to logs (TDD UC-13)."""
    cleaned = (pan or "").strip().upper()
    if len(cleaned) != 10:
        return "**********"
    return f"{cleaned[:5]}****{cleaned[-1]}"


def mask_aadhaar(aadhaar: str) -> str:
    """Mask 12-digit Aadhaar as ********2222 so raw Aadhaar is never written to logs (TDD UC-13)."""
    cleaned = (aadhaar or "").strip()
    if len(cleaned) != 12:
        return "************"
    return f"********{cleaned[-4:]}"


def validate_identity_numbers(pan_number: str, aadhaar_number: str) -> tuple[bool, bool, list[str]]:
    """Validate PAN ([A-Z]{5}[0-9]{4}[A-Z]{1}) and 12-digit Aadhaar format."""
    errors: list[str] = []
    pan_ok = bool(PAN_REGEX.match((pan_number or "").strip()))
    if not pan_ok:
        errors.append("Invalid PAN format: expected [A-Z]{5}[0-9]{4}[A-Z]{1}")
    aadhaar_ok = bool(AADHAAR_REGEX.match((aadhaar_number or "").strip()))
    if not aadhaar_ok:
        errors.append("Invalid Aadhaar format: expected 12 digits")
    return pan_ok, aadhaar_ok, errors


def validate_specimen_image_metadata(
    *,
    kyc_mode: str,
    pan_gcs_uri: str | None,
    aadhaar_gcs_uri: str | None,
    pan_image_bytes_b64: str | None = None,
    aadhaar_image_bytes_b64: str | None = None,
) -> dict[str, Any]:
    """Validate specimen KYC card metadata and PNG magic bytes without invoking any model or scanner.

    SDD §8.1: Images are NEVER sent to Model Armor or any LLM because watermarked specimen cards
    (`SPECIMEN · NOT A GOVERNMENT DOCUMENT`) would trigger false-positive refusals.
    """
    if kyc_mode == "TEXT_ONLY":
        return {
            "valid": True,
            "mode": "TEXT_ONLY",
            "pan_header_valid": True,
            "aadhaar_header_valid": True,
            "images_scanned_by_model_armor": False,
            "errors": [],
        }

    errors: list[str] = []
    pan_uri_ok = bool(
        pan_gcs_uri and pan_gcs_uri.startswith("gs://") and pan_gcs_uri.lower().endswith(".png")
    )
    aadhaar_uri_ok = bool(
        aadhaar_gcs_uri
        and aadhaar_gcs_uri.startswith("gs://")
        and aadhaar_gcs_uri.lower().endswith(".png")
    )
    if not pan_uri_ok:
        errors.append("IMAGE mode requires a valid gs://*.png URI for pan_gcs_uri")
    if not aadhaar_uri_ok:
        errors.append("IMAGE mode requires a valid gs://*.png URI for aadhaar_gcs_uri")

    pan_header_ok = True
    aadhaar_header_ok = True

    for label, b64_payload in (("pan", pan_image_bytes_b64), ("aadhaar", aadhaar_image_bytes_b64)):
        if b64_payload is not None:
            try:
                raw_bytes = base64.b64decode(b64_payload, validate=True)
            except (binascii.Error, ValueError):
                errors.append(f"Invalid base64 encoding for {label} specimen image")
                if label == "pan":
                    pan_header_ok = False
                else:
                    aadhaar_header_ok = False
                continue

            if not raw_bytes.startswith(PNG_MAGIC_HEADER):
                errors.append(
                    f"Invalid PNG magic header for {label} specimen image (expected 8-byte PNG signature)"
                )
                if label == "pan":
                    pan_header_ok = False
                else:
                    aadhaar_header_ok = False

    return {
        "valid": len(errors) == 0,
        "mode": "IMAGE",
        "pan_uri_valid": pan_uri_ok,
        "aadhaar_uri_valid": aadhaar_uri_ok,
        "pan_header_valid": pan_header_ok,
        "aadhaar_header_valid": aadhaar_header_ok,
        "images_scanned_by_model_armor": False,
        "errors": errors,
    }


@dataclass(frozen=True)
class ScannerVerdict:
    blocked: bool
    model_armor_text_status: str
    findings: dict[str, Any]


class TextScannerProtocol(Protocol):
    def scan_text_fields(
        self,
        *,
        loan_purpose_text: str,
        employment_remarks: str | None = None,
        applicant_notes: str | None = None,
    ) -> ScannerVerdict: ...


class DeterministicTextFallbackRuleset:
    """Deterministic prompt-injection, jailbreak, and SQLi heuristics.

    Always evaluated and recorded in `model_armor_findings_json` alongside the Model Armor API status.
    """

    RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
        (
            "prompt_injection",
            "system_override_directive",
            re.compile(
                r"(system\s+override|ignore\s+(all\s+|previous\s+|prior\s+)?(underwriting\s+)?(instructions|rules|prompts|directives)|"
                r"disregard\s+(all\s+|previous\s+)?(instructions|rules)|skip\s+cibil|"
                r"set\s+decision\s*=\s*approved|interest_rate_pct\s*=\s*0|override\s+(underwriting|cibil|foir|policy))",
                re.IGNORECASE,
            ),
        ),
        (
            "jailbreak",
            "jailbreak_persona_or_bypass",
            re.compile(
                r"(dan\s+mode|do\s+anything\s+now|jailbreak|act\s+as\s+an?\s+unrestricted|"
                r"bypass\s+(all\s+)?(safety|guardrails|model\s+armor|filters)|developer\s+mode\s+enabled)",
                re.IGNORECASE,
            ),
        ),
        (
            "sql_injection",
            "sql_command_or_tautology",
            re.compile(
                r"(drop\s+table|union\s+(all\s+)?select|;\s*--|'\s*or\s+['\"]?1['\"]?\s*=\s*['\"]?1|"
                r"delete\s+from\s+\w+|insert\s+into\s+\w+|update\s+loan_applications\s+set|pg_terminate_backend)",
                re.IGNORECASE,
            ),
        ),
    )

    def evaluate(self, fields: Mapping[str, str | None]) -> dict[str, Any]:
        matched_categories: list[str] = []
        matched_rules: list[str] = []
        field_findings: dict[str, list[str]] = {}

        for field_name in SCANNED_TEXT_FIELDS:
            text_val = (fields.get(field_name) or "").strip()
            if not text_val:
                field_findings[field_name] = []
                continue
            hits_for_field: list[str] = []
            for category, rule_name, pattern in self.RULES:
                if pattern.search(text_val):
                    hits_for_field.append(f"{category}:{rule_name}")
                    if category not in matched_categories:
                        matched_categories.append(category)
                    if rule_name not in matched_rules:
                        matched_rules.append(rule_name)
            field_findings[field_name] = hits_for_field

        return {
            "evaluated": True,
            "is_blocked": len(matched_rules) > 0,
            "matched_categories": matched_categories,
            "matched_rules": matched_rules,
            "field_findings": field_findings,
        }


_CACHED_CREDENTIALS: Any = None
_LAST_TOKEN_ERROR: str | None = None


def _default_adc_token_provider() -> str | None:
    """Resolve an OAuth2 access token from env, cached google-auth ADC, or GKE metadata server.

    Logs at ERROR and records `_LAST_TOKEN_ERROR` (`<ExcType>: <message>`) if both fail.
    """
    global _CACHED_CREDENTIALS, _LAST_TOKEN_ERROR
    _LAST_TOKEN_ERROR = None
    env_token = os.environ.get("MODEL_ARMOR_ACCESS_TOKEN") or os.environ.get(
        "GOOGLE_OAUTH_ACCESS_TOKEN"
    )
    if env_token:
        return env_token.strip()

    adc_err: str | None = None
    try:
        import google.auth
        import google.auth.transport.requests

        if _CACHED_CREDENTIALS is None:
            _CACHED_CREDENTIALS, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        if not _CACHED_CREDENTIALS.valid or not _CACHED_CREDENTIALS.token:
            _CACHED_CREDENTIALS.refresh(google.auth.transport.requests.Request())
        if _CACHED_CREDENTIALS.token:
            return str(_CACHED_CREDENTIALS.token)
        adc_err = "DefaultCredentialsError: google.auth.default returned empty token"
    except Exception as exc:
        adc_err = f"{type(exc).__name__}: {exc}"

    meta_err: str | None = None
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(
                "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                headers={"Metadata-Flavor": "Google"},
            )
            if resp.status_code == 200:
                tok = resp.json().get("access_token")
                if tok:
                    return str(tok)
                meta_err = "MetadataTokenError: metadata response missing access_token"
            else:
                meta_err = f"MetadataHTTPStatusError: HTTP {resp.status_code}"
    except Exception as exc:
        meta_err = f"{type(exc).__name__}: {exc}"

    _LAST_TOKEN_ERROR = f"ADC ({adc_err}); Metadata ({meta_err})"
    logger.error("Failed to acquire OAuth2 token for Model Armor REST API: %s", _LAST_TOKEN_ERROR)
    return None


class ModelArmorRestScanner:
    """Calls Model Armor REST API `:sanitizeUserPrompt` on text fields ONLY + deterministic fallback."""

    def __init__(
        self,
        *,
        template_id: str | None = None,
        base_url: str = MODEL_ARMOR_BASE_URL,
        http_client: httpx.Client | None = None,
        token_provider: Callable[[], str | None] | None = None,
        fallback_ruleset: DeterministicTextFallbackRuleset | None = None,
    ) -> None:
        self.template_id = (
            template_id
            or os.environ.get("MODEL_ARMOR_TEMPLATE_ID")
            or DEFAULT_MODEL_ARMOR_TEMPLATE_ID
        )
        self.base_url = base_url.rstrip("/")
        self._http_client = http_client
        self._token_provider = token_provider or _default_adc_token_provider
        self.fallback = fallback_ruleset or DeterministicTextFallbackRuleset()

    @property
    def sanitize_endpoint(self) -> str:
        return f"{self.base_url}/{self.template_id}:sanitizeUserPrompt"

    def scan_text_fields(
        self,
        *,
        loan_purpose_text: str,
        employment_remarks: str | None = None,
        applicant_notes: str | None = None,
    ) -> ScannerVerdict:
        fields: dict[str, str] = {
            "loan_purpose_text": loan_purpose_text or "",
            "employment_remarks": employment_remarks or "",
            "applicant_notes": applicant_notes or "",
        }
        fallback_result = self.fallback.evaluate(fields)
        combined_text = "\n".join(f"{k}: {v}" for k, v in fields.items() if v and v.strip())

        # In hermetic mode without an injected mock client, avoid external network calls
        if os.environ.get("GODVIEW_ENV") == "hermetic" and self._http_client is None:
            is_blocked = bool(fallback_result["is_blocked"])
            return ScannerVerdict(
                blocked=is_blocked,
                model_armor_text_status="MODEL_ARMOR_BLOCKED" if is_blocked else "CLEAN",
                findings={
                    "template_id": self.template_id,
                    "endpoint": self.sanitize_endpoint,
                    "scanned_fields": list(SCANNED_TEXT_FIELDS),
                    "images_scanned": False,
                    "api_status": "HERMETIC_FALLBACK",
                    "api_error": None,
                    "api_response": None,
                    "deterministic_fallback": fallback_result,
                },
            )

        api_status = "UNAVAILABLE"
        api_blocked = False
        api_raw: dict[str, Any] | None = None
        api_error: str | None = None

        token: str | None = None
        try:
            token = self._token_provider() if self._token_provider else None
        except Exception as exc:
            api_error = f"{type(exc).__name__}: {exc}"
            logger.error("Model Armor token_provider raised %s", api_error)

        if not token and api_error is None:
            if self._token_provider is _default_adc_token_provider and _LAST_TOKEN_ERROR:
                api_error = _LAST_TOKEN_ERROR
            elif self._http_client is None:
                api_error = (
                    "AuthenticationError: No OAuth2 access token available for Model Armor REST API"
                )
                logger.error("%s", api_error)

        if token or self._http_client is not None:
            headers: dict[str, str] = {
                "Content-Type": "application/json",
                "x-goog-user-project": os.environ.get("GCP_PROJECT_ID", "general-ak"),
            }
            if token:
                headers["Authorization"] = f"Bearer {token}"

            payload = {"userPromptData": {"text": combined_text or "empty"}}
            try:
                if self._http_client is not None:
                    resp = self._http_client.post(
                        self.sanitize_endpoint,
                        headers=headers,
                        json=payload,
                    )
                else:
                    with httpx.Client(timeout=5.0) as client:
                        resp = client.post(
                            self.sanitize_endpoint,
                            headers=headers,
                            json=payload,
                        )

                if resp.status_code == 200:
                    api_raw = resp.json()
                    sanitization = (api_raw or {}).get("sanitizationResult") or {}
                    match_state = str(sanitization.get("filterMatchState", "NO_MATCH_FOUND"))
                    api_status = match_state
                    api_blocked = match_state == "MATCH_FOUND"
                    api_error = None
                else:
                    try:
                        err_json = resp.json()
                    except ValueError:
                        err_json = {"raw": resp.text[:500]}
                    api_raw = err_json
                    details = ((err_json or {}).get("error") or {}).get("details") or []
                    reasons = [
                        d.get("reason") for d in details if isinstance(d, dict) and d.get("reason")
                    ]
                    if "TEMPLATE_NOT_FOUND" in reasons or "was not found" in resp.text:
                        api_status = "TEMPLATE_NOT_FOUND"
                    else:
                        api_status = f"HTTP_{resp.status_code}"
                    api_error = f"HTTPStatusError: HTTP {resp.status_code} from Model Armor API ({api_status})"
                    logger.error(
                        "Model Armor API returned non-200 status: %s",
                        api_error,
                    )
            except Exception as exc:
                api_status = "UNAVAILABLE"
                api_error = f"{type(exc).__name__}: {exc}"
                api_raw = {"error": api_error}
                logger.error("Model Armor API request failed: %s", api_error)

        is_blocked = bool(api_blocked or fallback_result["is_blocked"])
        return ScannerVerdict(
            blocked=is_blocked,
            model_armor_text_status="MODEL_ARMOR_BLOCKED" if is_blocked else "CLEAN",
            findings={
                "template_id": self.template_id,
                "endpoint": self.sanitize_endpoint,
                "scanned_fields": list(SCANNED_TEXT_FIELDS),
                "images_scanned": False,
                "api_status": api_status,
                "api_error": api_error,
                "api_response": api_raw,
                "deterministic_fallback": fallback_result,
            },
        )
