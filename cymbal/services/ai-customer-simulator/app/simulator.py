"""Core AI Customer Stream Simulator engine, durable state store, orchestrator client, and cost guards."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any, Literal, Protocol
from urllib.parse import quote
from uuid import uuid4

import httpx
from godview_shared.config import MODEL_TABLE
from godview_shared.otel import (
    get_tracer,
    inject_baggage_headers,
    set_business_span_attributes,
)
from pydantic import BaseModel

from app.cadence import (
    EXPECTED_JOURNEYS_PER_HOUR,
    MEAN_INTERVAL_SECONDS,
    TARGET_JOURNEYS_PER_DAY,
    TICK_LAUNCH_PROBABILITY,
    WINDOW_HOURS,
    WINDOW_MINUTES,
    compute_jitter_seconds,
    is_within_business_window,
    now_ist,
    select_kyc_mode,
    should_launch_on_tick,
)
from app.config import AdcTokenProvider, KycMode, SimulatorConfig, UtilityType
from app.identities import generate_synthetic_pan, is_checksum_invalid_pan
from app.personas import BorrowerPersona, VertexPersonaClient
from app.specimen_cards import VertexSpecimenCardGenerator


class SimulatorDisabledError(RuntimeError):
    """Raised when a journey burst is requested while the simulator is disabled."""


class DailyJourneyCapExceededError(RuntimeError):
    """Raised when the daily journey hard cap (320/day) is exhausted."""


class TenantPausedError(RuntimeError):
    """Raised when the tenant orchestrator responds with 503 TENANT_PAUSED."""


class StatePreconditionError(RuntimeError):
    """Raised when a durable state store generation precondition fails (HTTP 412)."""


class SimulatorPersistedState(BaseModel):
    """Durable state persisted across Cloud Run cold starts and instances."""

    enabled: bool = True
    paused_reason: str | None = None
    current_ist_date: str | None = None
    journeys_today: int = 0
    image_journeys_today: int = 0
    total_ticks: int = 0
    total_journeys: int = 0
    image_journeys: int = 0
    text_only_journeys: int = 0
    adversarial_journeys: int = 0
    duplicate_pan_journeys: int = 0
    consecutive_apply_failures: int = 0
    circuit_open_until: str | None = None


class SimulatorStateStore(Protocol):
    """Protocol for durable persistence of SimulatorPersistedState with optimistic concurrency."""

    def load_state(self) -> tuple[SimulatorPersistedState | None, int]: ...

    def save_state(
        self,
        state: SimulatorPersistedState,
        *,
        expected_generation: int | None = None,
    ) -> int: ...


class InMemorySimulatorStateStore:
    """In-memory durable state store supporting GCS-style generation preconditions."""

    def __init__(self) -> None:
        self._state: SimulatorPersistedState | None = None
        self._generation: int = 0

    def load_state(self) -> tuple[SimulatorPersistedState | None, int]:
        if self._state is None:
            return None, 0
        return self._state.model_copy(deep=True), self._generation

    def save_state(
        self,
        state: SimulatorPersistedState,
        *,
        expected_generation: int | None = None,
    ) -> int:
        if expected_generation is not None and expected_generation != self._generation:
            raise StatePreconditionError(
                f"Generation mismatch: expected {expected_generation}, actual {self._generation}"
            )
        self._generation += 1
        self._state = state.model_copy(deep=True)
        return self._generation


_HERMETIC_STORES: dict[str, InMemorySimulatorStateStore] = {}


def get_shared_hermetic_state_store(
    bucket_name: str,
    object_name: str = "_simulator_state/state.json",
) -> InMemorySimulatorStateStore:
    """Return a shared hermetic state store scoped to the current test or process."""
    test_scope = os.environ.get("PYTEST_CURRENT_TEST", "").split(" ")[0]
    key = f"{bucket_name}/{object_name}::{test_scope}"
    if key not in _HERMETIC_STORES:
        _HERMETIC_STORES[key] = InMemorySimulatorStateStore()
    return _HERMETIC_STORES[key]


class GcsSimulatorStateStore:
    """GCS-backed durable state store using ifGenerationMatch preconditions."""

    def __init__(
        self,
        bucket_name: str = "general-ak-god-view-kyc-vault",
        object_name: str = "_simulator_state/state.json",
        timeout_s: float = 15.0,
        token_provider: AdcTokenProvider | None = None,
    ) -> None:
        self.bucket_name = bucket_name
        self.object_name = object_name
        self.timeout_s = timeout_s
        self.token_provider = token_provider or AdcTokenProvider()

    def _headers(self) -> dict[str, str]:
        token = self.token_provider.get_access_token()
        return {"Authorization": f"Bearer {token}"}

    def load_state(self) -> tuple[SimulatorPersistedState | None, int]:
        encoded = quote(self.object_name, safe="")
        meta_url = f"https://storage.googleapis.com/storage/v1/b/{self.bucket_name}/o/{encoded}"
        media_url = f"{meta_url}?alt=media"
        headers = self._headers()
        with httpx.Client(timeout=self.timeout_s) as client:
            meta_resp = client.get(meta_url, headers=headers)
            if meta_resp.status_code == 404:
                return None, 0
            if meta_resp.status_code != 200:
                raise RuntimeError(
                    f"GCS state metadata GET failed (HTTP {meta_resp.status_code}): {meta_resp.text[:300]}"
                )
            meta_json = meta_resp.json()
            generation = int(meta_json.get("generation", 0))

            media_resp = client.get(media_url, headers=headers)
            if media_resp.status_code == 404:
                return None, 0
            if media_resp.status_code != 200:
                raise RuntimeError(
                    f"GCS state media GET failed (HTTP {media_resp.status_code}): {media_resp.text[:300]}"
                )
            payload = media_resp.json()
            return SimulatorPersistedState.model_validate(payload), generation

    def save_state(
        self,
        state: SimulatorPersistedState,
        *,
        expected_generation: int | None = None,
    ) -> int:
        encoded = quote(self.object_name, safe="")
        url = (
            f"https://storage.googleapis.com/upload/storage/v1/b/{self.bucket_name}/o"
            f"?uploadType=media&name={encoded}"
        )
        if expected_generation is not None:
            url += f"&ifGenerationMatch={expected_generation}"
        headers = {**self._headers(), "Content-Type": "application/json"}
        body = json.dumps(state.model_dump(mode="json")).encode("utf-8")
        with httpx.Client(timeout=self.timeout_s) as client:
            resp = client.post(url, headers=headers, content=body)
            if resp.status_code == 412:
                raise StatePreconditionError(
                    f"GCS ifGenerationMatch={expected_generation} precondition failed (HTTP 412)"
                )
            if resp.status_code not in (200, 201):
                raise RuntimeError(
                    f"GCS state PUT failed (HTTP {resp.status_code}): {resp.text[:300]}"
                )
            resp_json = resp.json()
            return int(resp_json.get("generation", (expected_generation or 0) + 1))


class JourneyLaunchResult(BaseModel):
    """Outcome of a single tick or journey launch."""

    launched: bool
    skipped_reason: str | None = None
    journey_id: str | None = None
    loan_id: str | None = None
    customer_id: str | None = None
    sequence_number: int | None = None
    kyc_mode: KycMode | None = None
    persona_model: str | None = None
    pan_gcs_uri: str | None = None
    aadhaar_gcs_uri: str | None = None
    pan_image_model: str | None = None
    aadhaar_image_model: str | None = None
    pan_number: str | None = None
    aadhaar_number: str | None = None
    jitter_seconds: float = 0.0
    image_budget_exhausted: bool = False
    is_adversarial: bool = False
    is_duplicate_pan: bool = False
    orchestrator_status: str | None = None
    journeys_today: int = 0
    image_journeys_today: int = 0


class OrchestratorClient(Protocol):
    """Client interface for driving the public Cymbal tenant API (/v1/loans/apply)."""

    async def apply_loan(
        self,
        *,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]: ...


class InMemoryOrchestratorClient:
    """Hermetic in-memory tenant API client for unit tests."""

    def __init__(self, base_url: str = "https://api.cymbal.ak-demos.com") -> None:
        self.base_url = base_url.rstrip("/")
        self.requests: list[dict[str, Any]] = []
        self.tenant_paused: bool = False
        self.fail_with_error: Exception | None = None

    async def apply_loan(
        self,
        *,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        record = {
            "url": f"{self.base_url}/v1/loans/apply",
            "headers": dict(headers),
            "json": dict(payload),
        }
        self.requests.append(record)
        if self.tenant_paused:
            raise TenantPausedError("503 TENANT_PAUSED")
        if self.fail_with_error is not None:
            raise self.fail_with_error

        loan_id = str(payload.get("loan_id") or uuid4())
        journey_id = str(payload.get("journey_id") or f"jrn-{uuid4().hex[:12]}")
        status = "MODEL_ARMOR_BLOCKED" if payload.get("is_adversarial") else "DISBURSED"
        return {
            "loan_id": loan_id,
            "journey_id": journey_id,
            "customer_id": payload.get("customer_id"),
            "status": status,
            "is_synthetic": True,
        }


def _is_tenant_paused_response(status_code: int, body: Any, raw_text: str) -> bool:
    if status_code == 503 and "TENANT_PAUSED" in raw_text.upper():
        return True
    if isinstance(body, dict):
        if str(body.get("code", "")).upper() == "TENANT_PAUSED":
            return True
        if str(body.get("status", "")).upper() == "TENANT_PAUSED":
            return True
        detail = body.get("detail")
        if isinstance(detail, dict) and str(detail.get("code", "")).upper() == "TENANT_PAUSED":
            return True
        if isinstance(detail, str) and "TENANT_PAUSED" in detail.upper():
            return True
    return False


class HttpOrchestratorClient:
    """Production async HTTP client calling API Gateway / loan-orchestrator-api."""

    def __init__(
        self,
        base_url: str,
        timeout_s: float = 45.0,
        token_provider: AdcTokenProvider | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.token_provider = token_provider or AdcTokenProvider()

    def _build_request_headers(self, headers: dict[str, str]) -> dict[str, str]:
        req_headers = dict(headers)
        if (
            self.base_url.startswith("https://")
            and "Authorization" not in req_headers
            and "authorization" not in req_headers
        ):
            id_token = self.token_provider.get_id_token(self.base_url)
            if id_token:
                req_headers["Authorization"] = f"Bearer {id_token}"
        return req_headers

    async def apply_loan(
        self,
        *,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        url = f"{self.base_url}/v1/loans/apply"
        req_headers = self._build_request_headers(headers)
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(url, json=payload, headers=req_headers)
            parsed_body: dict[str, Any] | None = None
            try:
                candidate = resp.json()
                if isinstance(candidate, dict):
                    parsed_body = candidate
            except ValueError:
                parsed_body = None

            if _is_tenant_paused_response(resp.status_code, parsed_body, resp.text):
                raise TenantPausedError(f"503 TENANT_PAUSED: {resp.text[:300]}")

            if resp.status_code not in (200, 201, 202, 400, 422):
                raise RuntimeError(
                    f"Orchestrator POST {url} failed with HTTP {resp.status_code}: {resp.text[:300]}"
                )
            if parsed_body is not None:
                return parsed_body
            return {"http_status": resp.status_code, "raw": resp.text[:200]}


def _make_traceparent() -> tuple[str, str]:
    trace_id = secrets.token_hex(16)
    span_id = secrets.token_hex(8)
    return f"00-{trace_id}-{span_id}-01", trace_id


class SimulatorEngine:
    """Stateful coordinator for the Cymbal synthetic customer stream backed by durable state."""

    def __init__(
        self,
        *,
        config: SimulatorConfig,
        persona_client: VertexPersonaClient,
        card_generator: VertexSpecimenCardGenerator,
        orchestrator_client: OrchestratorClient,
        state_store: SimulatorStateStore | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.config = config
        self.persona_client = persona_client
        self.card_generator = card_generator
        self.orchestrator_client = orchestrator_client
        self.state_store: SimulatorStateStore = state_store or InMemorySimulatorStateStore()
        self._sleep = sleep_fn
        self.tracer = get_tracer("ai-customer-simulator")

        self._enabled: bool = config.enabled
        self.paused_reason: str | None = None if config.enabled else "startup_disabled"

        self.total_ticks: int = 0
        self.total_journeys: int = 0
        self.image_journeys: int = 0
        self.text_only_journeys: int = 0
        self.adversarial_journeys: int = 0
        self.duplicate_pan_journeys: int = 0

        self._current_ist_date: date | None = None
        self.journeys_today: int = 0
        self.image_journeys_today: int = 0

        self.consecutive_apply_failures: int = 0
        self.circuit_open_until: datetime | None = None
        self._state_generation: int = 0
        self._store_initialized: bool = False

        if isinstance(self.state_store, InMemorySimulatorStateStore):
            self._ensure_store_initialized()

    @property
    def enabled(self) -> bool:
        self._ensure_store_initialized()
        self._sync_from_store()
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def _ensure_store_initialized(self) -> None:
        if self._store_initialized:
            return
        self._store_initialized = True
        existing, gen = self.state_store.load_state()
        if existing is not None:
            self._apply_persisted_state(existing, gen)
        else:
            self._persist_to_store()

    def _apply_persisted_state(self, persisted: SimulatorPersistedState, gen: int) -> None:
        self._enabled = persisted.enabled
        self.paused_reason = persisted.paused_reason
        self._current_ist_date = (
            date.fromisoformat(persisted.current_ist_date) if persisted.current_ist_date else None
        )
        self.journeys_today = persisted.journeys_today
        self.image_journeys_today = persisted.image_journeys_today
        self.total_ticks = persisted.total_ticks
        self.total_journeys = persisted.total_journeys
        self.image_journeys = persisted.image_journeys
        self.text_only_journeys = persisted.text_only_journeys
        self.adversarial_journeys = persisted.adversarial_journeys
        self.duplicate_pan_journeys = persisted.duplicate_pan_journeys
        self.consecutive_apply_failures = persisted.consecutive_apply_failures
        self.circuit_open_until = (
            datetime.fromisoformat(persisted.circuit_open_until)
            if persisted.circuit_open_until
            else None
        )
        self._state_generation = gen

    def _to_persisted_state(self) -> SimulatorPersistedState:
        return SimulatorPersistedState(
            enabled=self._enabled,
            paused_reason=self.paused_reason,
            current_ist_date=(
                self._current_ist_date.isoformat() if self._current_ist_date else None
            ),
            journeys_today=self.journeys_today,
            image_journeys_today=self.image_journeys_today,
            total_ticks=self.total_ticks,
            total_journeys=self.total_journeys,
            image_journeys=self.image_journeys,
            text_only_journeys=self.text_only_journeys,
            adversarial_journeys=self.adversarial_journeys,
            duplicate_pan_journeys=self.duplicate_pan_journeys,
            consecutive_apply_failures=self.consecutive_apply_failures,
            circuit_open_until=(
                self.circuit_open_until.isoformat() if self.circuit_open_until else None
            ),
        )

    def _sync_from_store(self) -> None:
        self._ensure_store_initialized()
        existing, gen = self.state_store.load_state()
        if existing is not None:
            self._apply_persisted_state(existing, gen)

    def _persist_to_store(self) -> None:
        state_obj = self._to_persisted_state()
        try:
            self._state_generation = self.state_store.save_state(
                state_obj,
                expected_generation=self._state_generation,
            )
        except StatePreconditionError:
            # Reload latest generation on concurrent write and retry once
            _, latest_gen = self.state_store.load_state()
            self._state_generation = self.state_store.save_state(
                state_obj,
                expected_generation=latest_gen,
            )

    def _rollover_daily_counters_if_needed(self, current_ist: datetime) -> bool:
        today = current_ist.date()
        if self._current_ist_date != today:
            self._current_ist_date = today
            self.journeys_today = 0
            self.image_journeys_today = 0
            return True
        return False

    def _is_circuit_open(self, current_ist: datetime) -> bool:
        if self.circuit_open_until is None:
            return False
        return current_ist < self.circuit_open_until

    def _record_apply_failure(self, current_ist: datetime) -> None:
        self.consecutive_apply_failures += 1
        if self.consecutive_apply_failures >= self.config.circuit_breaker_threshold:
            exponent = self.consecutive_apply_failures - self.config.circuit_breaker_threshold
            backoff_s = min(300.0, self.config.circuit_breaker_base_seconds * (2**exponent))
            self.circuit_open_until = current_ist + timedelta(seconds=backoff_s)
        self._persist_to_store()

    def _record_apply_success(self) -> None:
        if self.consecutive_apply_failures > 0 or self.circuit_open_until is not None:
            self.consecutive_apply_failures = 0
            self.circuit_open_until = None
            self._persist_to_store()

    def set_state(self, *, enabled: bool, reason: str | None = None) -> dict[str, Any]:
        """Flip simulator enabled state immediately and persist to durable store."""
        self._sync_from_store()
        self._enabled = enabled
        if not enabled:
            self.paused_reason = reason or "manual_disable"
        else:
            self.paused_reason = None
            self.consecutive_apply_failures = 0
            self.circuit_open_until = None
        self._persist_to_store()
        return self.status()

    async def launch_single_journey(
        self,
        *,
        now: datetime | None = None,
        force_kyc_mode: KycMode | None = None,
        adversarial: bool = False,
        override_pan: str | None = None,
        is_duplicate_pan: bool = False,
        jitter_seconds: float = 0.0,
    ) -> JourneyLaunchResult:
        """Execute a single synthetic borrower journey end-to-end."""
        if override_pan is not None and not is_checksum_invalid_pan(override_pan):
            raise ValueError(
                f"override_pan {override_pan!r} must be a synthetic checksum-invalid PAN"
            )

        current = now_ist(now)
        self._sync_from_store()
        if self._rollover_daily_counters_if_needed(current):
            self._persist_to_store()

        if not self._enabled:
            return JourneyLaunchResult(
                launched=False,
                skipped_reason="simulator_disabled",
                journeys_today=self.journeys_today,
                image_journeys_today=self.image_journeys_today,
            )

        if self._is_circuit_open(current):
            return JourneyLaunchResult(
                launched=False,
                skipped_reason="circuit_breaker_open",
                journeys_today=self.journeys_today,
                image_journeys_today=self.image_journeys_today,
            )

        if self.journeys_today >= self.config.max_journeys_per_day:
            return JourneyLaunchResult(
                launched=False,
                skipped_reason="daily_journey_cap_reached",
                journeys_today=self.journeys_today,
                image_journeys_today=self.image_journeys_today,
            )

        # Reserve sequence number and budget BEFORE any Vertex AI model call (prevents cost leak on failure)
        seq = self.total_journeys + 1
        desired_mode = force_kyc_mode if force_kyc_mode is not None else select_kyc_mode(seq)
        image_budget_exhausted = False
        if (
            desired_mode == KycMode.IMAGE
            and self.image_journeys_today >= self.config.max_images_per_day
        ):
            kyc_mode = KycMode.TEXT_ONLY
            image_budget_exhausted = True
        else:
            kyc_mode = desired_mode

        self.total_journeys = seq
        self.journeys_today += 1
        if kyc_mode == KycMode.IMAGE:
            self.image_journeys += 1
            self.image_journeys_today += 1
        else:
            self.text_only_journeys += 1
        if adversarial:
            self.adversarial_journeys += 1
        if is_duplicate_pan:
            self.duplicate_pan_journeys += 1
        self._persist_to_store()

        persona: BorrowerPersona = await self.persona_client.generate_persona(
            sequence_number=seq,
            adversarial=adversarial,
            override_pan=override_pan,
        )

        pan_gcs_uri: str | None = None
        aadhaar_gcs_uri: str | None = None
        pan_image_model: str | None = None
        aadhaar_image_model: str | None = None

        if kyc_mode == KycMode.IMAGE:
            pair = await self.card_generator.generate_and_upload_pair(
                customer_id=persona.customer_id,
                full_name=persona.full_name,
                dob=persona.dob.isoformat(),
                pan_number=persona.pan_number,
                aadhaar_number=persona.aadhaar_number,
            )
            pan_gcs_uri = pair.pan_gcs_uri
            aadhaar_gcs_uri = pair.aadhaar_gcs_uri
            pan_image_model = pair.pan_image_model
            aadhaar_image_model = pair.aadhaar_image_model

        journey_id = f"jrn-{current.strftime('%Y%m%d')}-{seq:05d}-{uuid4().hex[:6]}"
        loan_id = str(uuid4())
        traceparent, trace_id = _make_traceparent()

        headers = inject_baggage_headers(
            journey_id=journey_id,
            customer_id=persona.customer_id,
            synthetic=True,
            traceparent=traceparent,
        )

        persona_json = persona.model_dump(mode="json")
        customer_dict = {
            "customer_id": persona.customer_id,
            "full_name": persona.full_name,
            "name": persona.name,
            "dob": persona.dob.isoformat(),
            "email": persona.email,
            "phone": persona.phone,
            "city": persona.city,
            "employment_type": persona.employment_type,
            "employment": persona.employment,
            "employer_name": persona.employer_name,
            "monthly_income_inr": float(persona.monthly_income_inr),
            "income": float(persona.income),
            "pan_number": persona.pan_number,
            "aadhaar_number": persona.aadhaar_number,
            "is_ai_generated": True,
            "ai_persona_json": persona_json,
        }

        is_fleet = persona.utility_type == UtilityType.COMMERCIAL_FLEET_TOPUP
        apply_payload: dict[str, Any] = {
            "loan_id": loan_id,
            "journey_id": journey_id,
            "trace_id": trace_id,
            "customer_id": persona.customer_id,
            "vehicle_id": persona.vehicle_id,
            "utility_type": persona.utility_type.value,
            "loan_amount_inr": float(persona.loan_amount_inr),
            "down_payment_inr": float(persona.down_payment_inr),
            "tenure_months": persona.tenure_months,
            "interest_rate_pct": float(persona.interest_rate_pct),
            "existing_obligations_inr": 0.0,
            "kyc_mode": kyc_mode.value,
            "pan_gcs_uri": pan_gcs_uri,
            "aadhaar_gcs_uri": aadhaar_gcs_uri,
            "pan_image_model": pan_image_model,
            "aadhaar_image_model": aadhaar_image_model,
            "loan_purpose_text": persona.loan_purpose_text,
            "employment_remarks": persona.employment_remarks,
            "applicant_notes": persona.applicant_notes,
            "fleet_size": 5 if is_fleet else None,
            "requested_limit_inr": float(persona.loan_amount_inr) if is_fleet else None,
            "dealer_account_no": "CYMBAL-DLR-0001",
            "is_ai_generated": True,
            "is_adversarial": adversarial,
            "customer": customer_dict,
            **customer_dict,
        }

        try:
            with self.tracer.start_as_current_span("simulator.launch_journey") as span:
                set_business_span_attributes(
                    span,
                    journey_id=journey_id,
                    customer_id=persona.customer_id,
                    loan_id=loan_id,
                    event_type="simulator.journey_launched",
                    amount_inr=float(persona.loan_amount_inr),
                    entity="loan_application",
                    entity_id=loan_id,
                    synthetic=True,
                    extra={"kyc_mode": kyc_mode.value},
                )
                orch_resp = await self.orchestrator_client.apply_loan(
                    payload=apply_payload,
                    headers=headers,
                )
        except TenantPausedError:
            self.set_state(enabled=False, reason="orchestrator_tenant_paused")
            return JourneyLaunchResult(
                launched=False,
                skipped_reason="tenant_paused",
                sequence_number=seq,
                kyc_mode=kyc_mode,
                orchestrator_status="TENANT_PAUSED",
                journeys_today=self.journeys_today,
                image_journeys_today=self.image_journeys_today,
            )
        except Exception:
            self._record_apply_failure(current)
            raise

        if _is_tenant_paused_response(
            int(orch_resp.get("http_status", 200)),
            orch_resp,
            str(orch_resp),
        ):
            self.set_state(enabled=False, reason="orchestrator_tenant_paused")
            return JourneyLaunchResult(
                launched=False,
                skipped_reason="tenant_paused",
                sequence_number=seq,
                kyc_mode=kyc_mode,
                orchestrator_status="TENANT_PAUSED",
                journeys_today=self.journeys_today,
                image_journeys_today=self.image_journeys_today,
            )

        self._record_apply_success()

        return JourneyLaunchResult(
            launched=True,
            journey_id=str(orch_resp.get("journey_id") or journey_id),
            loan_id=str(orch_resp.get("loan_id") or loan_id),
            customer_id=persona.customer_id,
            sequence_number=seq,
            kyc_mode=kyc_mode,
            persona_model=persona.model_used,
            pan_gcs_uri=pan_gcs_uri,
            aadhaar_gcs_uri=aadhaar_gcs_uri,
            pan_image_model=pan_image_model,
            aadhaar_image_model=aadhaar_image_model,
            pan_number=persona.pan_number,
            aadhaar_number=persona.aadhaar_number,
            jitter_seconds=jitter_seconds,
            image_budget_exhausted=image_budget_exhausted,
            is_adversarial=adversarial,
            is_duplicate_pan=is_duplicate_pan,
            orchestrator_status=str(orch_resp.get("status") or "SUBMITTED"),
            journeys_today=self.journeys_today,
            image_journeys_today=self.image_journeys_today,
        )

    async def tick(
        self,
        *,
        now: datetime | None = None,
        random_sample: float | None = None,
        force_window: bool = False,
    ) -> JourneyLaunchResult:
        """Handle a 1-minute Cloud Scheduler tick (`* 9-16 * * *` Asia/Kolkata)."""
        current = now_ist(now)
        self._sync_from_store()
        self._rollover_daily_counters_if_needed(current)
        self.total_ticks += 1
        self._persist_to_store()

        if not self.enabled:
            return JourneyLaunchResult(
                launched=False,
                skipped_reason="simulator_disabled",
                journeys_today=self.journeys_today,
                image_journeys_today=self.image_journeys_today,
            )

        if self._is_circuit_open(current):
            return JourneyLaunchResult(
                launched=False,
                skipped_reason="circuit_breaker_open",
                journeys_today=self.journeys_today,
                image_journeys_today=self.image_journeys_today,
            )

        if not force_window and not is_within_business_window(
            current,
            weekdays_only=self.config.weekdays_only,
        ):
            return JourneyLaunchResult(
                launched=False,
                skipped_reason="outside_business_window",
                journeys_today=self.journeys_today,
                image_journeys_today=self.image_journeys_today,
            )

        if self.journeys_today >= self.config.max_journeys_per_day:
            return JourneyLaunchResult(
                launched=False,
                skipped_reason="daily_journey_cap_reached",
                journeys_today=self.journeys_today,
                image_journeys_today=self.image_journeys_today,
            )

        if not should_launch_on_tick(random_sample):
            return JourneyLaunchResult(
                launched=False,
                skipped_reason="probability_skip",
                journeys_today=self.journeys_today,
                image_journeys_today=self.image_journeys_today,
            )

        jitter = compute_jitter_seconds(
            seed=f"tick-{self.total_ticks}",
            max_jitter_s=self.config.max_tick_jitter_seconds,
        )
        if jitter > 0.0:
            await self._sleep(jitter)

        return await self.launch_single_journey(now=current, jitter_seconds=jitter)

    async def launch_burst(
        self,
        *,
        count: int,
        mode: Literal["normal", "adversarial", "duplicate_pan"] = "normal",
        pan_number: str | None = None,
        now: datetime | None = None,
    ) -> list[JourneyLaunchResult]:
        """Launch an explicit burst of `count` journeys (normal, adversarial, or duplicate_pan)."""
        if pan_number is not None and not is_checksum_invalid_pan(pan_number):
            raise ValueError(f"pan_number {pan_number!r} must be a synthetic checksum-invalid PAN")

        self._sync_from_store()
        if not self.enabled:
            raise SimulatorDisabledError("SIMULATOR_DISABLED")

        current = now_ist(now)
        if self._rollover_daily_counters_if_needed(current):
            self._persist_to_store()

        if self.journeys_today >= self.config.max_journeys_per_day:
            raise DailyJourneyCapExceededError("DAILY_JOURNEY_CAP_EXCEEDED")

        shared_pan: str | None = None
        if mode == "duplicate_pan":
            shared_pan = pan_number or generate_synthetic_pan(
                seed=f"dup-burst-{self.total_journeys + 1}"
            )

        results: list[JourneyLaunchResult] = []
        for _ in range(count):
            self._sync_from_store()
            if not self.enabled:
                break
            if self._is_circuit_open(current):
                break
            if self.journeys_today >= self.config.max_journeys_per_day:
                break
            res = await self.launch_single_journey(
                now=current,
                adversarial=(mode == "adversarial"),
                override_pan=shared_pan if mode == "duplicate_pan" else pan_number,
                is_duplicate_pan=(mode == "duplicate_pan"),
            )
            if res.launched:
                results.append(res)
            elif res.skipped_reason in (
                "tenant_paused",
                "simulator_disabled",
                "circuit_breaker_open",
            ):
                break
        return results

    def status(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Return full operational status, counters, budgets, and configured models."""
        current = now_ist(now)
        self._sync_from_store()
        if self._rollover_daily_counters_if_needed(current):
            self._persist_to_store()
        journeys_remaining = max(0, self.config.max_journeys_per_day - self.journeys_today)
        images_remaining = max(0, self.config.max_images_per_day - self.image_journeys_today)
        return {
            "service": "ai-customer-simulator",
            "enabled": self.enabled,
            "paused_reason": self.paused_reason,
            "within_business_window": is_within_business_window(
                current,
                weekdays_only=self.config.weekdays_only,
            ),
            "ist_timestamp": current.isoformat(),
            "cadence": {
                "target_journeys_per_day": TARGET_JOURNEYS_PER_DAY,
                "window_hours": WINDOW_HOURS,
                "window_minutes": WINDOW_MINUTES,
                "tick_launch_probability": TICK_LAUNCH_PROBABILITY,
                "expected_per_hour": EXPECTED_JOURNEYS_PER_HOUR,
                "mean_interval_seconds": MEAN_INTERVAL_SECONDS,
                "image_journey_ratio": 0.1,
            },
            "counters": {
                "total_ticks": self.total_ticks,
                "total_journeys": self.total_journeys,
                "image_journeys": self.image_journeys,
                "text_only_journeys": self.text_only_journeys,
                "journeys_today": self.journeys_today,
                "image_journeys_today": self.image_journeys_today,
                "adversarial_journeys": self.adversarial_journeys,
                "duplicate_pan_journeys": self.duplicate_pan_journeys,
                "consecutive_apply_failures": self.consecutive_apply_failures,
            },
            "circuit_breaker": {
                "open": self._is_circuit_open(current),
                "consecutive_failures": self.consecutive_apply_failures,
                "open_until": (
                    self.circuit_open_until.isoformat() if self.circuit_open_until else None
                ),
            },
            "budgets": {
                "max_journeys_per_day": self.config.max_journeys_per_day,
                "journeys_remaining_today": journeys_remaining,
                "max_images_per_day": self.config.max_images_per_day,
                "images_remaining_today": images_remaining,
                "journey_cap_exhausted": journeys_remaining == 0,
                "image_budget_exhausted": images_remaining == 0,
            },
            "models": {
                "persona": MODEL_TABLE["persona"].model_dump(),
                "specimen_image": MODEL_TABLE["specimen_image"].model_dump(),
            },
            "config": {
                "env_mode": self.config.env_mode,
                "api_base_url": self.config.api_base_url,
                "kyc_vault_bucket": self.config.kyc_vault_bucket,
                "weekdays_only": self.config.weekdays_only,
            },
        }
