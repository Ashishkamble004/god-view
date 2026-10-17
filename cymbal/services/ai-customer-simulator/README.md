# Cymbal Auto Finance — AI Customer Stream Simulator (`ai-customer-simulator`)

Synthetic Indian vehicle-loan borrower stream driving the Cymbal Auto Finance proving ground (`https://api.cymbal.ak-demos.com/v1/loans/apply`) with realistic borrower personas and watermarked specimen KYC cards.

---

## 1. Cadence Maths & Cost Governance (Decision A16, BR-01, TDD I-08)

| Parameter | Value | Formula / Notes |
| :-- | :-- | :-- |
| **Operating window** | `09:00–17:00 IST` (`Asia/Kolkata`) | 8 hours = `480` one-minute ticks (`* 9-16 * * *`) |
| **Target daily volume** | `300` journeys / day | `~37.5` journeys / hour, mean interval `96.0 s` (`≈ 100 s`) |
| **Per-tick launch probability** | `300 / 480 = 0.625` (`5/8`) | Evaluated on every `POST /v1/simulator/tick` inside the window |
| **Inter-arrival jitter** | `0.0–15.0 s` | Deterministic SHA-256 jitter applied via `await asyncio.sleep(jitter)` per tick |
| **KYC image mode ratio** | Exact `1 in 10` (`10.0 %`) | `sequence_number % 10 == 1` → `IMAGE`; other 9 → `TEXT_ONLY` |
| **Daily journey hard cap** | `320` journeys / day | Reserved **before** any Vertex AI model call (`daily_journey_cap_reached`); resets at `00:00 IST` |
| **Daily image budget** | `40` image journeys / day | Reserved **before** image generation; excess `IMAGE` journeys downgrade to `TEXT_ONLY` (`image_budget_exhausted=True`) |
| **Circuit breaker & `TENANT_PAUSED`** | `3` consecutive failures / `503 TENANT_PAUSED` | Opens exponential backoff circuit (`30s..300s`) after 3 apply errors; auto-disables on `503 TENANT_PAUSED` |
| **Durable state store** | `gs://<bucket>/_simulator_state/state.json` | Persists `{enabled, paused_reason, current_ist_date, journeys_today, image_journeys_today, total_journeys, ...}` with `ifGenerationMatch` preconditions across Cloud Run cold starts (`min_instances=0`, `max_instances=1`) |

---

## 2. Live Vertex AI Probe Findings (§B3 Prove-Don't-Assume, 2026-10-16)

Before implementing the model clients, a throwaway probe (`~/tmp/probe_models.py`) was executed against live Vertex AI in project `general-ak` at `locations/global` using ADC (`x-goog-user-project: general-ak`):

### Probe 1 — Persona JSON (`gemini-3.8-flash` @ `locations/global`, `thinking_level=LOW`)
- **Endpoint**: `POST https://aiplatform.googleapis.com/v1beta1/projects/general-ak/locations/global/publishers/google/models/gemini-3.8-flash:generateContent`
- **Request `generationConfig`**:
  ```json
  {
    "thinkingConfig": {"thinkingLevel": "LOW"},
    "responseMimeType": "application/json",
    "temperature": 0.7
  }
  ```
- **Observed HTTP status**: `200 OK`
- **Observed `usageMetadata`**: `{"promptTokenCount": 65, "candidatesTokenCount": 181, "totalTokenCount": 246}`
- **Observed response structure**: `candidates[0].content.parts[0]` has keys `["text", "thoughtSignature"]` where `text` is a strict JSON object with keys `name`, `dob`, `city`, `employment_type`, `employer_name`, `monthly_income_inr`, `vehicle_preference`, `loan_purpose_text`, `applicant_notes`.
- **Fallback**: `gemini-2.5-flash` at `locations/us-central1` per `godview_shared.config.MODEL_TABLE["persona"]`.

### Probe 2 — Watermarked Specimen Card (`gemini-nano-banana-2.1` @ `locations/global`)
- **Endpoint**: `POST https://aiplatform.googleapis.com/v1beta1/projects/general-ak/locations/global/publishers/google/models/gemini-nano-banana-2.1:generateContent`
- **Request `generationConfig`**:
  ```json
  {
    "responseModalities": ["TEXT", "IMAGE"]
  }
  ```
- **Observed HTTP status**: `200 OK`
- **Observed `usageMetadata`**: `{"promptTokenCount": 99, "candidatesTokenCount": 1120, "totalTokenCount": 1419, "thoughtsTokenCount": 200}`
- **Critical finding on response parts**: `candidates[0].content.parts` returns **4 parts**:
  - `Part 0`: `{"text": "**Designing Test Card**...", "thought": true}`
  - `Part 1`: `{"inlineData": {"mimeType": "image/png", "data": "..."}, "thought": true}` (intermediate thought draft image)
  - `Part 2`: `{"text": "**Finalizing Graphic Creation**...", "thought": true}`
  - `Part 3`: `{"inlineData": {"mimeType": "image/png", "data": "..."}, "thoughtSignature": "..."}` (`1,482,125` bytes, magic `b"\x89PNG\r\n\x1a\n"`, final rendered card)
- **Implementation consequence**: `VertexSpecimenCardGenerator` strictly requires a non-thought `inlineData` part (`not part.get("thought")`, raising if only thought drafts are present) and injects PNG `tEXt` metadata chunks (`Watermark: SPECIMEN · NOT A GOVERNMENT DOCUMENT`, `Emblems: NONE`, `Synthetic: true`) before uploading to `gs://general-ak-god-view-kyc-vault/<customer_id>/{pan,aadhaar}.png`.
- **Fallback chain**: `gemini-nano-banana-2.1` (`global`) → `gemini-3-pro-image` (`global`) → `gemini-2.5-flash-image` (`us-central1`).

---

## 3. Synthetic Identity & Watermark Guarantees (BR-01, TDD I-07)

- **Synthetic PAN (`pan_number`)**: 10-character `[A-Z]{5}[0-9]{4}[A-Z]{1}` where the 4th character (index 3) is strictly chosen from `("Z", "X", "Q", "W", "U", "V", "Y")` — never a real Income Tax holder status class (`P, C, H, F, A, T, B, L, J, G`) — and the 10th character intentionally mismatches the mod-26 check character of the first 9 characters. Caller-supplied `pan_number` values on `/v1/simulator/journeys` are validated with `is_checksum_invalid_pan` (returning HTTP `422` if a real PAN class or valid check char is supplied).
- **Synthetic Aadhaar (`aadhaar_number`)**: 12-digit numeric string (`^[2-9][0-9]{11}$`) whose 12th digit is offset from the Verhoeff dihedral group $D_5$ check digit so `verhoeff_checksum(aadhaar) != 0` (`is_verhoeff_valid(aadhaar) is False`).
- **Specimen Watermark & Metadata Check**: Every specimen card prompt requests `"SPECIMEN · NOT A GOVERNMENT DOCUMENT"` and forbids official emblems (`UIDAI`, `Income Tax Department`, `Government of India`, `Ashoka`, `Satyameva Jayate`); visual rendering was verified on `~/tmp/probe_specimen_pan.png`, and `verify_specimen_png()` performs a programmatic PNG structure and `tEXt` metadata chunk check (`Watermark`, `Emblems: NONE`, `Synthetic: true`).
- **Synthetic Headers**: Every outbound `POST /v1/loans/apply` request carries `X-Synthetic: true`, W3C `baggage: journey.id=...,customer.id=...,synthetic=true`, and `traceparent`.
- **Authentication**: Uses `google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])` with cached credentials and automatic refresh (`AdcTokenProvider` in `app/config.py`) — zero `gcloud` CLI or `subprocess` dependency inside the `python:3.13-slim` container.

---

## 4. Control Endpoints (Port `8080`, Cloud Run Internal Ingress, `min_instances=0, max_instances=1`)

| Method & Path | Purpose | OpenAPI Extension |
| :-- | :-- | :-- |
| `POST /v1/simulator/tick` | Cloud Scheduler 1-minute tick (`* 9-16 * * *` `Asia/Kolkata`); launches with $p = 300/480 = 0.625$ (test overrides `force_window`, `now`, `random_sample` permitted only when `GODVIEW_ENV=hermetic`) | — |
| `POST /v1/simulator/state` | `{enabled: bool, reason?: str}` — instant pause / Kill Switch / resume (persisted to durable store) | `x-godview-admin-action: simulator.set_state` |
| `POST /v1/simulator/journeys` | `{count: int, mode?: "normal" \| "adversarial" \| "duplicate_pan", pan_number?: str}` — manual burst, UC-12/UC-25 adversarial injection, or UC-11 duplicate-PAN velocity burst | `x-godview-admin-action: simulator.launch_journeys` |
| `POST /v1/simulator/journeys/adversarial` | Explicit adversarial prompt-injection burst (`loan_purpose_text`) for UC-12 / UC-25 | `x-godview-admin-action: simulator.launch_adversarial_journeys` |
| `POST /v1/simulator/journeys/duplicate-pan` | Explicit duplicate-PAN velocity burst for UC-11 | `x-godview-admin-action: simulator.launch_duplicate_pan_journeys` |
| `GET /v1/simulator/status` | Full status, cadence maths, counters, daily caps, circuit breaker, and active model table | — |
| `GET /healthz`, `GET /metrics` | Health probe & Prometheus metrics exposition | — |

---

## 5. Environment Variables

| Variable | Default | Purpose |
| :-- | :-- | :-- |
| `GODVIEW_ENV` | `gcp` | Runtime mode (`gcp`, `local`, or `hermetic`) |
| `API_BASE_URL` (or `ORCHESTRATOR_URL`) | `https://api.cymbal.ak-demos.com` | Target base URL for `POST /v1/loans/apply` |
| `KYC_VAULT_BUCKET` | `general-ak-god-view-kyc-vault` | GCS bucket for `<customer_id>/{pan,aadhaar}.png` and `_simulator_state/state.json` |
| `SIMULATOR_STATE_OBJECT` | `_simulator_state/state.json` | GCS object path for durable simulator state |
| `GCP_PROJECT_ID` | `general-ak` | Vertex AI project ID |
| `SIMULATOR_ENABLED` | `true` | Initial enabled state (when no durable state exists yet) |
| `TRAFFIC_WEEKDAYS_ONLY` | `false` | Restrict automatic ticks to Monday–Friday IST |
| `DAILY_JOURNEY_HARD_CAP` | `320` | Maximum synthetic journeys per IST day |
| `DAILY_IMAGE_BUDGET_CAP` | `40` | Maximum `IMAGE`-mode journeys per IST day |
| `MAX_TICK_JITTER_SECONDS` | `15.0` | Maximum per-tick sleep jitter in seconds |
| `PORT` | `8080` | HTTP listen port |
