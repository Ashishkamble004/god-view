"""Hermetic unit and contract test suite for ai-customer-simulator (TDD §6.2 #9).

Covers:
1. Cadence maths: 300/day in 09:00–17:00 IST (480 min window, p = 300/480 = 0.625,
   ~37.5/hour, ~96s mean interval, bounded jitter applied via asyncio.sleep, weekend handling, outside-window block).
2. Exact 1-in-10 IMAGE vs 9-in-10 TEXT_ONLY selection over 10, 100, and 1,000 simulated ticks.
3. Instant stop on POST /v1/simulator/state {enabled: false} (kill switch / pause) and resume.
4. Durable state persistence across cold starts / new engine instances (enabled, paused_reason, daily caps, seq) with generation preconditions.
5. Pre-model cost-guard reservation, failure circuit breaker, and automatic stop on 503 TENANT_PAUSED.
6. Synthetic markers: X-Synthetic: true header and W3C baggage synthetic=true on every outbound call.
7. Checksum-invalid synthetic IDs (both failure directions) + 422 rejection of real PANs on /journeys.
8. Specimen card generation, PNG metadata check, strict non-thought image selection, GCS upload & model fallback chain.
9. google-auth ADC token provider with credential caching (zero subprocess / gcloud CLI dependency).
10. Hermetic transport wiring in GODVIEW_ENV=hermetic and rejection of test-override knobs (force_window, now, random_sample) outside hermetic mode.
11. OpenAPI admin extensions (x-godview-admin-action, x-godview-reversal) on all mutating routes and /v1/loans/apply payload contract verification.
"""

from __future__ import annotations

import base64
import subprocess
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import google.auth
import google.oauth2.id_token
import pytest
from app.cadence import (
    IST,
    MEAN_INTERVAL_SECONDS,
    TARGET_JOURNEYS_PER_DAY,
    TICK_LAUNCH_PROBABILITY,
    WINDOW_END_HOUR_IST,
    WINDOW_MINUTES,
    WINDOW_START_HOUR_IST,
    compute_jitter_seconds,
    is_within_business_window,
    select_kyc_mode,
    should_launch_on_tick,
)
from app.config import AdcTokenProvider, KycMode, SimulatorConfig, UtilityType
from app.identities import (
    VALID_PAN_FOURTH_CHARS,
    compute_pan_check_char,
    generate_synthetic_aadhaar,
    generate_synthetic_pan,
    is_checksum_invalid_pan,
    is_valid_real_pan,
    is_verhoeff_valid,
    verhoeff_checksum,
    verhoeff_expected_check_digit,
)
from app.main import build_default_engine, create_app
from app.personas import (
    ADVERSARIAL_INJECTION_PAYLOADS,
    HermeticVertexTransport,
    LiveVertexRestTransport,
    PersonaGenerationError,
    VertexPersonaClient,
)
from app.simulator import (
    HttpOrchestratorClient,
    InMemoryOrchestratorClient,
    InMemorySimulatorStateStore,
    SimulatorEngine,
    SimulatorPersistedState,
    SimulatorStateStore,
    StatePreconditionError,
)
from app.specimen_cards import (
    FORBIDDEN_EMBLEM_TERMS,
    SPECIMEN_WATERMARK,
    GcsHttpSpecimenStorage,
    InMemorySpecimenStorage,
    SpecimenCardError,
    VertexSpecimenCardGenerator,
    build_specimen_prompt,
    render_deterministic_specimen_png,
    verify_specimen_png,
)
from fastapi.testclient import TestClient
from godview_shared.config import MODEL_TABLE, validate_model_location
from godview_shared.otel import BAGGAGE_HEADER, SYNTHETIC_HEADER, extract_baggage_context

# ==============================================================================
# Fixtures & Deterministic Mocks
# ==============================================================================


class FakeModelTransport:
    """Deterministic mock for Vertex AI generateContent calls (persona + specimen image)."""

    def __init__(
        self,
        *,
        fail_models: set[str] | None = None,
        include_thought_parts: bool = True,
    ) -> None:
        self.fail_models = fail_models or set()
        self.include_thought_parts = include_thought_parts
        self.calls: list[dict[str, Any]] = []

    async def generate_content(
        self,
        *,
        model_id: str,
        location: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        validate_model_location(model_id, location)
        self.calls.append(
            {
                "model_id": model_id,
                "location": location,
                "payload": payload,
            }
        )
        if model_id in self.fail_models:
            raise RuntimeError(f"Simulated Vertex AI error for {model_id}@{location}")

        gen_cfg = payload.get("generationConfig", {})
        if gen_cfg.get("responseMimeType") == "application/json":
            idx = len(self.calls)
            persona_json = (
                "{"
                f'"name": "Kavya Nair {idx}", '
                '"dob": "1992-08-19", '
                '"city": "Kochi", '
                '"employment": "SALARIED", '
                '"employer_name": "Malabar Marine Logistics Pvt Ltd", '
                '"income": 115000, '
                '"vehicle_preference": "Cymbal Volt Pulse EV", '
                '"vehicle_id": "VEH-EV-001", '
                '"utility_type": "RETAIL_AUTO_LOAN", '
                '"loan_purpose_text": "Purchasing an electric SUV for daily commute in Kochi.", '
                '"employment_remarks": "Senior operations manager with 7 years tenure.", '
                '"applicant_notes": "Clean repayment track record; requesting 48-month tenure."'
                "}"
            )
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": persona_json,
                                    "thoughtSignature": "mock-sig-persona",
                                }
                            ]
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 65,
                    "candidatesTokenCount": 180,
                    "totalTokenCount": 245,
                },
                "modelVersion": model_id,
            }

        prompt_text = payload["contents"][0]["parts"][0]["text"]
        raw_png = render_deterministic_specimen_png(
            doc_type="pan" if "PAN" in prompt_text.upper() else "aadhaar",
            customer_id="cust-mock",
            full_name="KAVYA NAIR",
            dob="1992-08-19",
            id_number="CYMZX1234B",
            model_id=model_id,
        )
        b64_png = base64.b64encode(raw_png).decode("ascii")
        parts: list[dict[str, Any]] = []
        if self.include_thought_parts:
            parts.append({"text": "**Designing Test Card**", "thought": True})
            parts.append(
                {
                    "inlineData": {"mimeType": "image/png", "data": b64_png},
                    "thought": True,
                }
            )
        parts.append(
            {
                "inlineData": {"mimeType": "image/png", "data": b64_png},
                "thoughtSignature": "mock-sig-image",
            }
        )
        return {
            "candidates": [{"content": {"parts": parts}}],
            "usageMetadata": {
                "promptTokenCount": 99,
                "candidatesTokenCount": 1120,
                "totalTokenCount": 1419,
            },
            "modelVersion": model_id,
        }


async def _noop_sleep(_seconds: float) -> None:
    return None


def make_engine(
    *,
    env_mode: str = "hermetic",
    fail_models: set[str] | None = None,
    max_journeys_per_day: int = 320,
    max_images_per_day: int = 40,
    weekdays_only: bool = False,
    circuit_breaker_threshold: int = 3,
    state_store: SimulatorStateStore | None = None,
    sleep_fn: Any = _noop_sleep,
) -> tuple[
    SimulatorEngine,
    FakeModelTransport,
    InMemorySpecimenStorage,
    InMemoryOrchestratorClient,
]:
    config = SimulatorConfig(
        env_mode=env_mode,  # type: ignore[arg-type]
        api_base_url="https://api.cymbal.ak-demos.com",
        kyc_vault_bucket="general-ak-god-view-kyc-vault",
        gcp_project_id="general-ak",
        enabled=True,
        weekdays_only=weekdays_only,
        max_journeys_per_day=max_journeys_per_day,
        max_images_per_day=max_images_per_day,
        circuit_breaker_threshold=circuit_breaker_threshold,
    )
    transport = FakeModelTransport(fail_models=fail_models)
    storage = InMemorySpecimenStorage(bucket_name=config.kyc_vault_bucket)
    orchestrator = InMemoryOrchestratorClient(base_url=config.api_base_url)
    persona_client = VertexPersonaClient(transport=transport)
    card_generator = VertexSpecimenCardGenerator(transport=transport, storage=storage)
    engine = SimulatorEngine(
        config=config,
        persona_client=persona_client,
        card_generator=card_generator,
        orchestrator_client=orchestrator,
        state_store=state_store or InMemorySimulatorStateStore(),
        sleep_fn=sleep_fn,
    )
    return engine, transport, storage, orchestrator


# ==============================================================================
# 1. Cadence Maths, Business Window & Jitter Sleep Tests
# ==============================================================================


@pytest.mark.unit
def test_cadence_maths_constants_and_probability() -> None:
    """Verify 300/day in 09:00-17:00 IST (480 min) => p = 300/480 = 0.625, ~37.5/h, ~96s interval."""
    assert WINDOW_START_HOUR_IST == 9
    assert WINDOW_END_HOUR_IST == 17
    assert WINDOW_MINUTES == 480
    assert TARGET_JOURNEYS_PER_DAY == 300
    assert TICK_LAUNCH_PROBABILITY == pytest.approx(300 / 480)
    assert TICK_LAUNCH_PROBABILITY == pytest.approx(0.625)
    assert TARGET_JOURNEYS_PER_DAY / 8 == pytest.approx(37.5)
    assert MEAN_INTERVAL_SECONDS == pytest.approx(96.0)


@pytest.mark.unit
def test_business_window_ist_boundaries_both_directions() -> None:
    """Verify 09:00-17:00 IST window inclusion and exclusion (including weekday filter)."""
    just_before = datetime(2026, 10, 16, 8, 59, 59, tzinfo=IST)
    at_open = datetime(2026, 10, 16, 9, 0, 0, tzinfo=IST)
    mid_day_utc = datetime(2026, 10, 16, 12, 30, 0, tzinfo=IST).astimezone(UTC)
    just_before_close = datetime(2026, 10, 16, 16, 59, 59, tzinfo=IST)
    at_close = datetime(2026, 10, 16, 17, 0, 0, tzinfo=IST)
    late_night = datetime(2026, 10, 16, 23, 15, 0, tzinfo=IST)

    assert is_within_business_window(at_open) is True
    assert is_within_business_window(mid_day_utc) is True
    assert is_within_business_window(just_before_close) is True

    assert is_within_business_window(just_before) is False
    assert is_within_business_window(at_close) is False
    assert is_within_business_window(late_night) is False

    saturday_morning = datetime(2026, 10, 17, 11, 0, 0, tzinfo=IST)
    assert is_within_business_window(saturday_morning, weekdays_only=False) is True
    assert is_within_business_window(saturday_morning, weekdays_only=True) is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tick_launch_probability_and_jitter_sleep_applied() -> None:
    """Verify should_launch_on_tick threshold and that tick() awaits sleep_fn(jitter) before apply."""
    assert should_launch_on_tick(0.0) is True
    assert should_launch_on_tick(0.6249) is True
    assert should_launch_on_tick(0.625) is False
    assert should_launch_on_tick(0.99) is False

    samples = [compute_jitter_seconds(seed=i) for i in range(200)]
    assert all(0.0 <= s <= 15.0 for s in samples)
    assert len({round(s, 3) for s in samples}) > 20

    slept_durations: list[float] = []

    async def record_sleep(seconds: float) -> None:
        slept_durations.append(seconds)

    engine, _, _, orchestrator = make_engine(sleep_fn=record_sleep)
    now_ist = datetime(2026, 10, 16, 10, 15, 0, tzinfo=IST)
    res = await engine.tick(now=now_ist, random_sample=0.1)
    assert res.launched is True
    assert res.jitter_seconds > 0.0
    assert slept_durations == [res.jitter_seconds]
    assert len(orchestrator.requests) == 1


# ==============================================================================
# 2. Exact 1-in-10 IMAGE Selection over 1,000 Simulated Ticks
# ==============================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_one_in_ten_image_selection_exactness_over_1000_ticks() -> None:
    """Verify exact 1-in-10 IMAGE selection over 1,000 simulated ticks and journeys."""
    modes = [select_kyc_mode(seq) for seq in range(1, 1001)]
    assert modes.count(KycMode.IMAGE) == 100
    assert modes.count(KycMode.TEXT_ONLY) == 900

    for block_start in range(0, 1000, 10):
        block = modes[block_start : block_start + 10]
        assert block.count(KycMode.IMAGE) == 1
        assert block.count(KycMode.TEXT_ONLY) == 9

    engine, _transport, storage, orchestrator = make_engine(
        max_journeys_per_day=320,
        max_images_per_day=40,
    )
    image_count = 0
    text_only_count = 0
    base_day = datetime(2026, 10, 16, 10, 0, 0, tzinfo=IST)

    for tick_idx in range(1000):
        day_offset = tick_idx // 250
        now_ist = base_day.replace(day=16 + day_offset)
        res = await engine.tick(now=now_ist, random_sample=0.1)
        assert res.launched is True
        if res.kyc_mode == KycMode.IMAGE:
            image_count += 1
        else:
            text_only_count += 1

    assert image_count == 100
    assert text_only_count == 900
    assert len(orchestrator.requests) == 1000
    assert len(storage.objects) == 200


# ==============================================================================
# 3. Instant Stop on state=false & Durable State Across Cold Starts
# ==============================================================================


@pytest.mark.unit
def test_instant_stop_on_state_false_and_resume() -> None:
    """POST /v1/simulator/state {enabled: false} stops ticks and bursts immediately."""
    engine, transport, _storage, orchestrator = make_engine()
    app = create_app(engine=engine)
    client = TestClient(app)

    resp = client.post(
        "/v1/simulator/state",
        json={"enabled": False, "reason": "UC-18 Traffic Kill Switch"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["paused_reason"] == "UC-18 Traffic Kill Switch"

    tick_resp = client.post("/v1/simulator/tick", json={"force_window": True, "random_sample": 0.1})
    assert tick_resp.status_code == 200
    tick_data = tick_resp.json()
    assert tick_data["launched"] is False
    assert tick_data["skipped_reason"] == "simulator_disabled"
    assert len(transport.calls) == 0
    assert len(orchestrator.requests) == 0

    burst_resp = client.post("/v1/simulator/journeys", json={"count": 3})
    assert burst_resp.status_code == 503
    assert "SIMULATOR_DISABLED" in burst_resp.text
    assert len(transport.calls) == 0
    assert len(orchestrator.requests) == 0

    enable_resp = client.post("/v1/simulator/state", json={"enabled": True, "reason": "resume"})
    assert enable_resp.status_code == 200
    assert enable_resp.json()["enabled"] is True

    tick_ok = client.post("/v1/simulator/tick", json={"force_window": True, "random_sample": 0.1})
    assert tick_ok.status_code == 200
    assert tick_ok.json()["launched"] is True
    assert len(orchestrator.requests) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_durable_state_persists_across_cold_starts_and_preconditions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify state, counters, and kill switch survive cold starts (new build_default_engine)."""
    monkeypatch.setenv("GODVIEW_ENV", "hermetic")

    e1 = build_default_engine()
    assert e1.enabled is True
    assert isinstance(e1.persona_client.transport, HermeticVertexTransport)
    assert not isinstance(e1.persona_client.transport, LiveVertexRestTransport)

    # Flip state to False on e1 -> a brand-new build_default_engine() sees enabled=False
    e1.set_state(enabled=False, reason="kill_switch_cold_start_test")
    e2 = build_default_engine()
    assert e2.enabled is False
    assert e2.paused_reason == "kill_switch_cold_start_test"

    # Re-enable on e2 and launch 3 journeys -> e3 sees journeys_today=3 and total_journeys=3
    e2.set_state(enabled=True, reason="resume")
    assert e1.enabled is True
    day = datetime(2026, 10, 16, 11, 0, 0, tzinfo=IST)
    await e2.launch_burst(count=3, now=day)

    e3 = build_default_engine()
    assert e3.enabled is True
    assert e3.journeys_today == 3
    assert e3.total_journeys == 3
    assert e3.image_journeys_today == 1

    # Verify generation precondition enforcement on InMemorySimulatorStateStore
    store = InMemorySimulatorStateStore()
    gen1 = store.save_state(SimulatorPersistedState(enabled=True), expected_generation=0)
    assert gen1 == 1
    with pytest.raises(StatePreconditionError):
        store.save_state(SimulatorPersistedState(enabled=False), expected_generation=0)


# ==============================================================================
# 4. Pre-Model Cost Guard Reservation, Circuit Breaker & 503 TENANT_PAUSED
# ==============================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cost_guards_never_leak_on_apply_failure_and_circuit_breaker() -> None:
    """Verify failing apply_loan calls advance seq, count against caps before model calls, and open circuit breaker."""
    engine, transport, _storage, orchestrator = make_engine(
        max_journeys_per_day=320,
        max_images_per_day=3,
        circuit_breaker_threshold=3,
    )
    orchestrator.fail_with_error = RuntimeError("502 Bad Gateway from API Gateway")
    now_ist = datetime(2026, 10, 16, 10, 0, 0, tzinfo=IST)

    # Fire 10 ticks while gateway is down:
    # Tick 1 (seq 1, IMAGE): 1 persona + 2 image calls, then apply_loan raises (failure #1)
    # Tick 2 (seq 2, TEXT_ONLY): 1 persona + 0 image calls, then apply_loan raises (failure #2)
    # Tick 3 (seq 3, TEXT_ONLY): 1 persona + 0 image calls, then apply_loan raises (failure #3 -> circuit opens!)
    # Ticks 4..10: circuit_breaker_open -> 0 model calls!
    for i in range(10):
        if i < 3:
            with pytest.raises(RuntimeError, match="502 Bad Gateway"):
                await engine.tick(now=now_ist, random_sample=0.1)
        else:
            res = await engine.tick(now=now_ist, random_sample=0.1)
            assert res.launched is False
            assert res.skipped_reason == "circuit_breaker_open"

    image_calls = [
        c
        for c in transport.calls
        if c["payload"].get("generationConfig", {}).get("responseModalities") == ["TEXT", "IMAGE"]
    ]
    persona_calls = [
        c
        for c in transport.calls
        if c["payload"].get("generationConfig", {}).get("responseMimeType") == "application/json"
    ]
    assert len(image_calls) == 2  # Only seq 1 generated PAN + Aadhaar; never 20!
    assert len(persona_calls) == 3  # Ticks 4..10 made 0 persona calls
    assert engine.total_journeys == 3
    assert engine.journeys_today == 3
    assert engine.image_journeys_today == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stop_generating_when_orchestrator_returns_503_tenant_paused() -> None:
    """When orchestrator returns 503 TENANT_PAUSED, simulator immediately disables itself and halts burst."""
    engine, transport, _storage, orchestrator = make_engine()
    orchestrator.tenant_paused = True
    now_ist = datetime(2026, 10, 16, 10, 0, 0, tzinfo=IST)

    # Request a burst of 5 while orchestrator is TENANT_PAUSED -> stops after first attempt!
    results = await engine.launch_burst(count=5, now=now_ist)
    assert len(results) == 0
    assert engine.enabled is False
    assert engine.paused_reason == "orchestrator_tenant_paused"
    assert len(orchestrator.requests) == 1

    # Subsequent tick makes 0 model calls because simulator is now disabled
    calls_before = len(transport.calls)
    tick_res = await engine.tick(now=now_ist, random_sample=0.1)
    assert tick_res.launched is False
    assert tick_res.skipped_reason == "simulator_disabled"
    assert len(transport.calls) == calls_before


# ==============================================================================
# 5. Synthetic Header & Baggage Propagation on Every Call (TDD I-07)
# ==============================================================================


@pytest.mark.unit
def test_synthetic_header_and_baggage_on_every_outbound_call() -> None:
    """Every outbound call to loan-orchestrator-api carries X-Synthetic: true and baggage synthetic=true."""
    engine, _transport, _storage, orchestrator = make_engine()
    app = create_app(engine=engine)
    client = TestClient(app)

    burst_resp = client.post("/v1/simulator/journeys", json={"count": 12})
    assert burst_resp.status_code == 200
    data = burst_resp.json()
    assert data["launched_count"] == 12
    assert len(orchestrator.requests) == 12

    for req in orchestrator.requests:
        headers = req["headers"]
        assert headers.get(SYNTHETIC_HEADER) == "true"
        assert "synthetic=true" in headers.get(BAGGAGE_HEADER, "")
        ctx = extract_baggage_context(headers)
        assert ctx["synthetic"] is True
        assert ctx["journey.id"] == req["json"]["journey_id"]
        assert ctx["customer.id"] == req["json"]["customer_id"]
        assert req["json"]["is_ai_generated"] is True
        assert req["json"]["customer"]["is_ai_generated"] is True


# ==============================================================================
# 6. Checksum-Invalid Synthetic PAN & Aadhaar Numbers (Both Directions + 422)
# ==============================================================================


@pytest.mark.unit
def test_synthetic_pan_and_aadhaar_checksum_invalidity_and_422_guard() -> None:
    """Verify synthetic PAN/Aadhaar checksum invalidity in both directions and 422 on real PAN input."""
    for i in range(100):
        pan = generate_synthetic_pan(seed=f"cust-{i}")
        assert len(pan) == 10
        assert pan[:5].isalpha() and pan[:5].isupper()
        assert pan[5:9].isdigit()
        assert pan[9].isalpha() and pan[9].isupper()
        assert pan[3] not in VALID_PAN_FOURTH_CHARS
        assert pan[9] != compute_pan_check_char(pan[:9])
        assert is_checksum_invalid_pan(pan) is True
        assert is_valid_real_pan(pan) is False

    real_prefix = "ABCPS1234"
    real_check = compute_pan_check_char(real_prefix)
    valid_pan = real_prefix + real_check
    assert is_valid_real_pan(valid_pan) is True
    assert is_checksum_invalid_pan(valid_pan) is False

    # Caller-supplied real PAN on /v1/simulator/journeys must be rejected with 422
    engine, _, _, _ = make_engine()
    client = TestClient(create_app(engine=engine))
    bad_pan_resp = client.post(
        "/v1/simulator/journeys",
        json={"count": 1, "mode": "duplicate_pan", "pan_number": "ABCPE1234F"},
    )
    assert bad_pan_resp.status_code == 422

    good_pan = generate_synthetic_pan(seed="custom-dup")
    good_pan_resp = client.post(
        "/v1/simulator/journeys",
        json={"count": 2, "mode": "duplicate_pan", "pan_number": good_pan},
    )
    assert good_pan_resp.status_code == 200
    assert all(j["pan_number"] == good_pan for j in good_pan_resp.json()["journeys"])

    for i in range(100):
        aadhaar = generate_synthetic_aadhaar(seed=f"cust-{i}")
        assert len(aadhaar) == 12
        assert aadhaar.isdigit()
        assert aadhaar[0] not in ("0", "1")
        assert verhoeff_checksum(aadhaar) != 0
        assert is_verhoeff_valid(aadhaar) is False

    prefix_11 = "23456789012"[:11]
    valid_digit = verhoeff_expected_check_digit(prefix_11)
    valid_aadhaar = f"{prefix_11}{valid_digit}"
    assert verhoeff_checksum(valid_aadhaar) == 0
    assert is_verhoeff_valid(valid_aadhaar) is True


# ==============================================================================
# 7. Watermark Metadata, Non-Thought Image Selection, GCS Upload & Fallbacks
# ==============================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_specimen_card_watermark_no_emblems_and_gcs_upload() -> None:
    """Verify specimen cards carry watermark metadata, forbid emblems, and upload to kyc-vault bucket."""
    engine, _transport, storage, _orchestrator = make_engine()

    now_ist = datetime(2026, 10, 16, 10, 30, 0, tzinfo=IST)
    results = await engine.launch_burst(count=10, now=now_ist)
    image_results = [r for r in results if r.kyc_mode == KycMode.IMAGE]
    text_results = [r for r in results if r.kyc_mode == KycMode.TEXT_ONLY]
    assert len(image_results) == 1
    assert len(text_results) == 9

    img_res = image_results[0]
    cid = img_res.customer_id
    expected_pan_uri = f"gs://general-ak-god-view-kyc-vault/{cid}/pan.png"
    expected_aadhaar_uri = f"gs://general-ak-god-view-kyc-vault/{cid}/aadhaar.png"
    assert img_res.pan_gcs_uri == expected_pan_uri
    assert img_res.aadhaar_gcs_uri == expected_aadhaar_uri
    assert img_res.pan_image_model == "gemini-nano-banana-2.1"
    assert img_res.aadhaar_image_model == "gemini-nano-banana-2.1"

    for uri in (expected_pan_uri, expected_aadhaar_uri):
        assert uri in storage.objects
        png_bytes = storage.objects[uri]["data"]
        verification = verify_specimen_png(png_bytes)
        assert verification["valid_png"] is True
        assert verification["has_watermark"] is True
        assert verification["watermark"] == SPECIMEN_WATERMARK
        assert verification["has_forbidden_emblem"] is False

    unwatermarked = b"\x89PNG\r\n\x1a\n" + b"no watermark bytes IEND"
    bad_check = verify_specimen_png(unwatermarked)
    assert bad_check["has_watermark"] is False

    prompt_pan = build_specimen_prompt(
        doc_type="pan",
        full_name="Aarav Kulkarni",
        dob="1991-05-14",
        id_number="CYMZX1234B",
    )
    assert SPECIMEN_WATERMARK in prompt_pan
    for forbidden in FORBIDDEN_EMBLEM_TERMS:
        assert f"include {forbidden}" not in prompt_pan.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_model_fallback_chains_per_tdd_i09() -> None:
    """Verify persona and image generators follow MODEL_TABLE fallback chains when primary fails."""
    engine, _transport, _storage, _orchestrator = make_engine(
        fail_models={"gemini-3.8-flash", "gemini-nano-banana-2.1"}
    )
    now_ist = datetime(2026, 10, 16, 11, 0, 0, tzinfo=IST)
    res = await engine.tick(now=now_ist, random_sample=0.1)
    assert res.launched is True
    assert res.persona_model == "gemini-2.5-flash"
    assert res.pan_image_model == "gemini-3-pro-image"
    assert res.aadhaar_image_model == "gemini-3-pro-image"

    engine2, _transport2, _storage2, _orch2 = make_engine(
        fail_models={"gemini-nano-banana-2.1", "gemini-3-pro-image"}
    )
    res2 = await engine2.tick(now=now_ist, random_sample=0.1)
    assert res2.launched is True
    assert res2.pan_image_model == "gemini-2.5-flash-image"

    engine3, _, _, _ = make_engine(fail_models={"gemini-3.8-flash", "gemini-2.5-flash"})
    with pytest.raises(PersonaGenerationError):
        await engine3.tick(now=now_ist, random_sample=0.1)

    engine4, _, _, _ = make_engine(
        fail_models={
            "gemini-nano-banana-2.1",
            "gemini-3-pro-image",
            "gemini-2.5-flash-image",
        }
    )
    with pytest.raises(SpecimenCardError):
        await engine4.tick(now=now_ist, random_sample=0.1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_thought_part_filtering_and_rejection_of_thought_only_drafts() -> None:
    """Verify VertexSpecimenCardGenerator ignores thought=True drafts and raises if only thought drafts exist."""

    class ThoughtDraftTransport:
        def __init__(self, *, only_thought_drafts: bool = False) -> None:
            self.only_thought_drafts = only_thought_drafts

        async def generate_content(
            self,
            *,
            model_id: str,
            location: str,
            payload: dict[str, Any],
        ) -> dict[str, Any]:
            validate_model_location(model_id, location)
            final_png = render_deterministic_specimen_png(
                doc_type="pan",
                customer_id="cust-final",
                full_name="FINAL CARD HOLDER",
                dob="1990-01-01",
                id_number="CYMZX9999B",
                model_id=model_id,
            )
            parts: list[dict[str, Any]] = [
                {"text": "Thinking about layout...", "thought": True},
                {
                    "inlineData": {
                        "mimeType": "image/png",
                        "data": base64.b64encode(final_png).decode("ascii"),
                    },
                    "thought": True,
                },
            ]
            if not self.only_thought_drafts:
                parts.append(
                    {
                        "inlineData": {
                            "mimeType": "image/png",
                            "data": base64.b64encode(final_png).decode("ascii"),
                        },
                        "thoughtSignature": "final-sig",
                    }
                )
            return {"candidates": [{"content": {"parts": parts}}]}

    storage = InMemorySpecimenStorage()
    gen_ok = VertexSpecimenCardGenerator(
        transport=ThoughtDraftTransport(only_thought_drafts=False),
        storage=storage,
    )
    pair = await gen_ok.generate_and_upload_pair(
        customer_id="cust-123",
        full_name="Aarav Kulkarni",
        dob="1990-01-01",
        pan_number="CYMZX9999B",
        aadhaar_number="234567890128",
    )
    assert verify_specimen_png(pair.pan_bytes)["valid_png"] is True

    # Negative direction: if model returns ONLY thought=True draft images, must raise SpecimenCardError
    gen_fail = VertexSpecimenCardGenerator(
        transport=ThoughtDraftTransport(only_thought_drafts=True),
        storage=storage,
    )
    with pytest.raises(SpecimenCardError, match="No non-thought inlineData image"):
        await gen_fail.generate_and_upload_pair(
            customer_id="cust-456",
            full_name="Aarav Kulkarni",
            dob="1990-01-01",
            pan_number="CYMZX9999B",
            aadhaar_number="234567890128",
        )


# ==============================================================================
# 8. Google-Auth ADC Token Provider (Zero Subprocess / gcloud Dependency)
# ==============================================================================


@pytest.mark.unit
def test_google_auth_adc_token_provider_never_calls_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify AdcTokenProvider, LiveVertexRestTransport, and GcsHttpSpecimenStorage use google-auth without subprocess."""

    def forbid_subprocess(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("subprocess.run is forbidden in Cloud Run slim container")

    monkeypatch.setattr(subprocess, "run", forbid_subprocess)

    class FakeCredentials:
        def __init__(self) -> None:
            self.valid = False
            self.expired = True
            self.token: str | None = None
            self.refresh_calls = 0

        def refresh(self, _request: Any) -> None:
            self.refresh_calls += 1
            self.valid = True
            self.expired = False
            self.token = f"ya29.fake-adc-token-{self.refresh_calls}"

    fake_creds = FakeCredentials()
    monkeypatch.setattr(
        google.auth,
        "default",
        lambda scopes=None: (fake_creds, "general-ak"),
    )

    provider = AdcTokenProvider()
    t1 = provider.get_access_token()
    t2 = provider.get_access_token()
    assert t1 == "ya29.fake-adc-token-1"
    assert t2 == "ya29.fake-adc-token-1"
    assert fake_creds.refresh_calls == 1  # Cached on second call

    # When expired, refreshes again
    fake_creds.expired = True
    t3 = provider.get_access_token()
    assert t3 == "ya29.fake-adc-token-2"
    assert fake_creds.refresh_calls == 2

    live_transport = LiveVertexRestTransport(project_id="general-ak", token_provider=provider)
    gcs_storage = GcsHttpSpecimenStorage(token_provider=provider)
    assert live_transport._get_access_token() == "ya29.fake-adc-token-2"
    assert gcs_storage._get_access_token() == "ya29.fake-adc-token-2"

    # Verify OIDC ID token caching and attachment on HttpOrchestratorClient for https:// Cloud Run URLs
    oidc_calls: list[str] = []

    def fake_fetch_id_token(_request: Any, audience: str) -> str:
        oidc_calls.append(audience)
        return f"eyJ.fake-oidc-token-for-{len(oidc_calls)}"

    monkeypatch.setattr(google.oauth2.id_token, "fetch_id_token", fake_fetch_id_token)

    http_orch = HttpOrchestratorClient(
        base_url="https://loan-orchestrator-api-mcj3w7ujpq-uc.a.run.app",
        token_provider=provider,
    )
    h1 = http_orch._build_request_headers({"X-Synthetic": "true"})
    h2 = http_orch._build_request_headers({"X-Synthetic": "true"})
    assert h1["Authorization"] == "Bearer eyJ.fake-oidc-token-for-1"
    assert h2["Authorization"] == "Bearer eyJ.fake-oidc-token-for-1"
    assert oidc_calls == ["https://loan-orchestrator-api-mcj3w7ujpq-uc.a.run.app"]

    # Local http:// URLs do not fetch OIDC ID tokens
    local_orch = HttpOrchestratorClient(
        base_url="http://loan-orchestrator-api:8080",
        token_provider=provider,
    )
    h_local = local_orch._build_request_headers({"X-Synthetic": "true"})
    assert "Authorization" not in h_local


@pytest.mark.unit
def test_http_tick_returns_200_on_downstream_error_and_trips_circuit_breaker() -> None:
    """Verify HTTP POST /v1/simulator/tick catches downstream errors gracefully (no HTTP 500) and opens circuit breaker."""
    engine, _transport, _storage, orchestrator = make_engine(circuit_breaker_threshold=3)
    orchestrator.fail_with_error = RuntimeError("503 Service Unavailable from downstream")
    client = TestClient(create_app(engine=engine))

    for i in range(3):
        resp = client.post(
            "/v1/simulator/tick",
            json={"force_window": True, "random_sample": 0.1},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["launched"] is False
        assert body["skipped_reason"] == "orchestrator_error"
        assert "503 Service Unavailable" in (body["orchestrator_status"] or "")
        assert body["journeys_today"] == i + 1

    # 4th tick is blocked immediately by the open circuit breaker
    resp4 = client.post(
        "/v1/simulator/tick",
        json={"force_window": True, "random_sample": 0.1},
    )
    assert resp4.status_code == 200
    assert resp4.json()["launched"] is False
    assert resp4.json()["skipped_reason"] == "circuit_breaker_open"


# ==============================================================================
# 9. Adversarial (UC-12/UC-25), Duplicate-PAN (UC-11) & Daily Cost Guards
# ==============================================================================


@pytest.mark.unit
def test_adversarial_and_duplicate_pan_bursts_only_on_explicit_request() -> None:
    """Verify normal traffic never injects or duplicates PAN, while explicit modes do."""
    engine, _transport, _storage, orchestrator = make_engine()
    app = create_app(engine=engine)
    client = TestClient(app)

    normal_resp = client.post("/v1/simulator/journeys", json={"count": 5})
    assert normal_resp.status_code == 200
    normal_reqs = orchestrator.requests[:5]
    normal_pans = {r["json"]["pan_number"] for r in normal_reqs}
    assert len(normal_pans) == 5
    for r in normal_reqs:
        purpose = r["json"]["loan_purpose_text"]
        assert not any(inj in purpose for inj in ADVERSARIAL_INJECTION_PAYLOADS)

    adv_resp = client.post(
        "/v1/simulator/journeys",
        json={"count": 3, "mode": "adversarial"},
    )
    assert adv_resp.status_code == 200
    adv_reqs = orchestrator.requests[5:8]
    assert len(adv_reqs) == 3
    for r in adv_reqs:
        purpose = r["json"]["loan_purpose_text"]
        assert any(inj in purpose for inj in ADVERSARIAL_INJECTION_PAYLOADS)

    dup_resp = client.post(
        "/v1/simulator/journeys",
        json={"count": 4, "mode": "duplicate_pan"},
    )
    assert dup_resp.status_code == 200
    dup_reqs = orchestrator.requests[8:12]
    assert len(dup_reqs) == 4
    dup_pans = {r["json"]["pan_number"] for r in dup_reqs}
    assert len(dup_pans) == 1
    shared_pan = next(iter(dup_pans))
    assert is_checksum_invalid_pan(shared_pan) is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_daily_journey_hard_cap_and_image_budget_guard() -> None:
    """Verify 320/day journey hard cap and 40/day image budget guard with IST midnight reset."""
    engine, _transport, _storage, orchestrator = make_engine(
        max_journeys_per_day=5,
        max_images_per_day=1,
    )
    day1 = datetime(2026, 10, 16, 10, 0, 0, tzinfo=IST)

    r1 = await engine.tick(now=day1, random_sample=0.1)
    assert r1.launched is True
    assert r1.kyc_mode == KycMode.IMAGE

    r2 = await engine.launch_single_journey(now=day1, force_kyc_mode=KycMode.IMAGE)
    assert r2.launched is True
    assert r2.kyc_mode == KycMode.TEXT_ONLY
    assert r2.image_budget_exhausted is True

    for _ in range(3):
        r = await engine.tick(now=day1, random_sample=0.1)
        assert r.launched is True

    assert len(orchestrator.requests) == 5

    r6 = await engine.tick(now=day1, random_sample=0.1)
    assert r6.launched is False
    assert r6.skipped_reason == "daily_journey_cap_reached"
    assert len(orchestrator.requests) == 5

    day2 = datetime(2026, 10, 17, 10, 0, 0, tzinfo=IST)
    r_next_day = await engine.launch_single_journey(now=day2, force_kyc_mode=KycMode.IMAGE)
    assert r_next_day.launched is True
    assert r_next_day.kyc_mode == KycMode.IMAGE


# ==============================================================================
# 10. Production Guard on Test Knobs, OpenAPI Extensions & Apply Contract
# ==============================================================================


@pytest.mark.unit
def test_production_tick_rejects_test_override_knobs() -> None:
    """In non-hermetic mode (env_mode='gcp'), /tick and /journeys reject force_window, now, and random_sample."""
    engine_gcp, _, _, _ = make_engine(env_mode="gcp")
    client_gcp = TestClient(create_app(engine=engine_gcp))

    # Reject force_window=True in gcp mode
    r_fw = client_gcp.post("/v1/simulator/tick", json={"force_window": True})
    assert r_fw.status_code == 422
    assert "TEST_OVERRIDES_FORBIDDEN_IN_PRODUCTION" in r_fw.text

    # Reject random_sample in gcp mode
    r_rs = client_gcp.post("/v1/simulator/tick", json={"random_sample": 0.0})
    assert r_rs.status_code == 422

    # Reject now override in gcp mode on /tick and /journeys
    r_now = client_gcp.post("/v1/simulator/tick", json={"now": "2026-10-16T11:00:00+05:30"})
    assert r_now.status_code == 422

    r_j_now = client_gcp.post(
        "/v1/simulator/journeys",
        json={"count": 1, "now": "2026-10-16T11:00:00+05:30"},
    )
    assert r_j_now.status_code == 422


@pytest.mark.unit
def test_status_metrics_healthz_and_openapi_admin_extensions() -> None:
    """Verify GET /v1/simulator/status, /healthz, /metrics, and OpenAPI x-godview-admin-action on all mutating routes."""
    engine, _, _, _ = make_engine()
    app = create_app(engine=engine)
    client = TestClient(app)

    status_resp = client.get("/v1/simulator/status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["enabled"] is True
    assert status_data["cadence"]["target_journeys_per_day"] == 300
    assert status_data["cadence"]["tick_launch_probability"] == pytest.approx(0.625)
    assert status_data["budgets"]["max_journeys_per_day"] == 320
    assert status_data["budgets"]["max_images_per_day"] == 40
    assert status_data["models"]["persona"]["model_id"] == MODEL_TABLE["persona"].model_id
    assert (
        status_data["models"]["specimen_image"]["model_id"]
        == MODEL_TABLE["specimen_image"].model_id
    )

    health_resp = client.get("/healthz")
    assert health_resp.status_code == 200
    assert health_resp.json()["status"] == "ok"

    metrics_resp = client.get("/metrics")
    assert metrics_resp.status_code == 200
    assert "godview_http_requests_total" in metrics_resp.text

    schema = app.openapi()
    for path in (
        "/v1/simulator/state",
        "/v1/simulator/journeys",
        "/v1/simulator/journeys/adversarial",
        "/v1/simulator/journeys/duplicate-pan",
    ):
        op = schema["paths"][path]["post"]
        assert "x-godview-admin-action" in op, f"Missing x-godview-admin-action on {path}"
        assert "x-godview-reversal" in op, f"Missing x-godview-reversal on {path}"


@pytest.mark.unit
def test_double_mounted_api_v1_routes_and_orchestrator_apply_contract() -> None:
    """Verify /api/v1/simulator/* routes and field-by-field contract match with loan-orchestrator-api LoanApplyRequest."""
    engine, _, _, orchestrator = make_engine()
    app = create_app(engine=engine)
    client = TestClient(app)

    status_resp = client.get("/api/v1/simulator/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["service"] == "ai-customer-simulator"

    adv_resp = client.post("/api/v1/simulator/journeys/adversarial", json={"count": 2})
    assert adv_resp.status_code == 200
    assert adv_resp.json()["mode"] == "adversarial"
    assert adv_resp.json()["launched_count"] == 2

    dup_resp = client.post("/api/v1/simulator/journeys/duplicate-pan", json={"count": 6})
    assert dup_resp.status_code == 200
    assert dup_resp.json()["mode"] == "duplicate_pan"
    assert dup_resp.json()["launched_count"] == 6
    assert len(orchestrator.requests) == 8

    # Verify field-by-field contract compatibility with loan-orchestrator-api /v1/loans/apply
    for req in orchestrator.requests:
        payload = req["json"]
        cust = payload["customer"]
        assert UUID(str(cust["customer_id"]))
        assert isinstance(cust["full_name"], str) and len(cust["full_name"]) >= 2
        assert len(cust["dob"]) == 10
        assert "@" in cust["email"]
        assert cust["phone"].startswith("+91")
        assert len(cust["pan_number"]) == 10 and is_checksum_invalid_pan(cust["pan_number"])
        assert len(cust["aadhaar_number"]) == 12 and not is_verhoeff_valid(cust["aadhaar_number"])
        assert isinstance(cust["ai_persona_json"], dict)
        assert payload["vehicle_id"].startswith("VEH-")
        assert payload["utility_type"] in (
            UtilityType.RETAIL_AUTO_LOAN.value,
            UtilityType.COMMERCIAL_FLEET_TOPUP.value,
        )
        assert payload["loan_amount_inr"] > 0
        assert payload["down_payment_inr"] >= 0
        assert 12 <= payload["tenure_months"] <= 84
        assert payload["interest_rate_pct"] > 0
        assert payload["dealer_account_no"] == "CYMBAL-DLR-0001"
        assert payload["journey_id"].startswith("jrn-")
