# Cymbal Auto Finance — High-Level Design (HLD)

**Plane**: Customer Application & 5-Utility Digital Vehicle Loan Origination Tenant (`cymbal/`)
**Live Customer Portal (URL 1)**: `https://cymbal.ak-demos.com`
**Live Internal Bank LOS Portal (URL 2)**: `https://los.cymbal.ak-demos.com` (IAP Protected)
**Related Docs**: [Cymbal Hub](README.md) | [Cymbal LLD](LLD.md) | [Cymbal System Design](SYSTEM_DESIGN.md) | [Master HLD](../HLD.md) | [God-View SRE HLD](../god-view/HLD.md)

---

## 1. Multi-Tier 5-Utility Architecture

Cymbal Auto Finance processes digital vehicle loans across **8 fictional vehicle models** (`VEH-EV-001` through `VEH-FLT-002`) spanning four vehicle categories (`EV_SUV`, `PASSENGER_SUV`, `TWO_WHEELER`, `COMMERCIAL_FLEET`). Every application traverses a 5-utility pipeline deployed across **Cloud Run v2**, **GKE Autopilot**, and **GCE VM**:

```mermaid
flowchart LR
    subgraph Portals["Customer & Bank Portals"]
        P1["URL 1: Customer Loan Portal (cymbal.ak-demos.com)"]
        P2["URL 2: Internal Bank LOS Portal (los.cymbal.ak-demos.com)"]
        Sim["ai-customer-simulator (Cloud Run v2)"]
    end

    subgraph Coordinator["Journey Coordinator (Cloud Run v2)"]
        Orch["loan-orchestrator-api (/v1/*, /api/v1/*, /admin/v1/*)"]
    end

    subgraph Utilities["Five Domain Utilities Across Three Compute Substrates"]
        U1["Utility 1: kyc-model-armor-svc (GKE Autopilot ILB 10.10.0.3:8080)"]
        U2["Utility 2: underwriting-los-svc (GKE Autopilot ILB 10.10.0.2:8080)"]
        U3["Utility 3: commercial-fleet-credit-svc (Cloud Run v2)"]
        U4["Utility 4: motor-insurance-svc (Cloud Run v2)"]
        U5["Utility 5: core-banking-cibil-vm (GCE VM 10.10.0.5:8085)"]
    end

    subgraph Storage["Persistence & Guardrails"]
        PG[("Cloud SQL PG16: godview_lms (9 Business Tables)")]
        Vault[("GCS: general-ak-god-view-kyc-vault (7d TTL Specimen PNGs)")]
        Armor["Vertex AI Model Armor (cymbal-text-guard)"]
    end

    P1 -->|"POST /api/v1/loans/apply"| Orch
    P2 -->|"GET /api/v1/loans, /kyc-documents/*, /observability/*"| Orch
    Sim -->|"X-Synthetic: true"| Orch

    Orch -->|"1. KYC & Text Scan"| U1
    U1 -->|"Scan 3 Text Fields"| Armor
    Sim -->|"Upload Watermarked PNGs"| Vault
    Orch -->|"Signed URLs"| Vault

    Orch -->|"2. EMI / FOIR / LTV"| U2
    U2 -->|"Fetch Bureau Score"| U5
    Orch -->|"3. Fleet Credit (If COMMERCIAL_FLEET_TOPUP)"| U3
    Orch -->|"4. Motor Policy & FASTag"| U4
    Orch -->|"5. CBS UTR Settlement"| U5

    Orch --> PG
    U3 --> PG
    U4 --> PG
    U5 --> PG
```

---

## 2. Text-Only Model Armor & Watermarked Specimen KYC Privacy Design

1. **Strict Synthetic Identity Rules (`I-08`)**:
   - All AI-generated borrowers carry `X-Synthetic: true` and W3C baggage `synthetic=true`, setting `customers.is_ai_generated = true`.
   - Synthetic PANs use checksum-invalid 4th-character holder classes so they can never collide with real Indian PAN numbers; synthetic Aadhaar numbers use the reserved `9999xxxxxxxx` prefix.
   - All generated specimen cards (`15%` of journeys via `gemini-3.1-flash-image-preview`) are watermarked with **`SPECIMEN · NOT A GOVERNMENT DOCUMENT`** and expire automatically after 7 days in `gs://general-ak-god-view-kyc-vault`.
2. **Text-Only Model Armor Guardrail (`kyc-model-armor-svc`)**:
   - Specimen PNG images undergo **only deterministic PNG magic-header (`\x89PNG\r\n\x1a\n`) and metadata validation**; image bytes are **never** sent to Model Armor or any LLM (`images_sent_to_model_armor = False`).
   - Only the 3 free-text applicant fields (`loan_purpose_text`, `employment_remarks`, `applicant_notes`) are scanned by Google Cloud Model Armor template `projects/general-ak/locations/us-central1/templates/cymbal-text-guard`.
