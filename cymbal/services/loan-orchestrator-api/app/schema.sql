-- Cymbal Auto Finance v4 Tenant Business Schema (TDD §2.1, exactly 9 business tables)

CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_id VARCHAR(32) PRIMARY KEY,
    make VARCHAR(64) NOT NULL,
    model VARCHAR(64) NOT NULL,
    category VARCHAR(32) NOT NULL CHECK (category IN ('EV_SUV', 'PASSENGER_SUV', 'TWO_WHEELER', 'COMMERCIAL_FLEET')),
    ex_showroom_inr NUMERIC(12,2) NOT NULL,
    cdn_image_url TEXT NOT NULL,
    min_down_payment_pct NUMERIC(5,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id UUID PRIMARY KEY,
    full_name VARCHAR(128) NOT NULL,
    dob DATE NOT NULL,
    email VARCHAR(128) NOT NULL,
    phone VARCHAR(20) NOT NULL,
    city VARCHAR(64) NOT NULL,
    employment_type VARCHAR(32) NOT NULL,
    employer_name VARCHAR(128) NOT NULL,
    monthly_income_inr NUMERIC(12,2) NOT NULL,
    pan_number CHAR(10) NOT NULL,
    aadhaar_number CHAR(12) NOT NULL,
    is_ai_generated BOOLEAN NOT NULL DEFAULT false,
    ai_persona_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS kyc_documents (
    kyc_id UUID PRIMARY KEY,
    customer_id UUID NOT NULL REFERENCES customers(customer_id),
    loan_id UUID,
    kyc_mode VARCHAR(16) NOT NULL CHECK (kyc_mode IN ('IMAGE', 'TEXT_ONLY')),
    pan_gcs_uri TEXT,
    aadhaar_gcs_uri TEXT,
    pan_image_model VARCHAR(64),
    aadhaar_image_model VARCHAR(64),
    pan_verified BOOLEAN NOT NULL DEFAULT false,
    aadhaar_verified BOOLEAN NOT NULL DEFAULT false,
    model_armor_text_status VARCHAR(32) NOT NULL,
    model_armor_findings_json JSONB,
    verified_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS loan_applications (
    loan_id UUID PRIMARY KEY,
    customer_id UUID NOT NULL REFERENCES customers(customer_id),
    vehicle_id VARCHAR(32) NOT NULL REFERENCES vehicles(vehicle_id),
    utility_type VARCHAR(32) NOT NULL CHECK (utility_type IN ('RETAIL_AUTO_LOAN', 'COMMERCIAL_FLEET_TOPUP')),
    loan_amount_inr NUMERIC(12,2) NOT NULL,
    down_payment_inr NUMERIC(12,2) NOT NULL,
    tenure_months INT NOT NULL,
    interest_rate_pct NUMERIC(5,2) NOT NULL,
    emi_inr NUMERIC(10,2) NOT NULL,
    loan_purpose_text TEXT,
    employment_remarks TEXT,
    applicant_notes TEXT,
    status VARCHAR(32) NOT NULL CHECK (status IN (
        'KYC_PENDING',
        'KYC_VERIFIED',
        'MODEL_ARMOR_BLOCKED',
        'APPROVED',
        'REJECTED',
        'INSURED',
        'DISBURSEMENT_PENDING',
        'DISBURSED'
    )),
    journey_id VARCHAR(64) NOT NULL,
    trace_id VARCHAR(64),
    total_latency_ms INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS underwriting_audits (
    audit_id UUID PRIMARY KEY,
    loan_id UUID NOT NULL REFERENCES loan_applications(loan_id),
    cibil_score INT NOT NULL,
    cibil_bureau_ref VARCHAR(64) NOT NULL,
    foir_pct NUMERIC(5,2) NOT NULL,
    ltv_pct NUMERIC(5,2) NOT NULL,
    rules_version VARCHAR(32) NOT NULL,
    gke_pod_name VARCHAR(128),
    gce_vm_name VARCHAR(128),
    decision VARCHAR(32) NOT NULL CHECK (decision IN ('APPROVED', 'REJECTED')),
    rejection_reason TEXT,
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS commercial_credit_lines (
    credit_line_id UUID PRIMARY KEY,
    customer_id UUID NOT NULL REFERENCES customers(customer_id),
    loan_id UUID NOT NULL REFERENCES loan_applications(loan_id),
    vehicle_category VARCHAR(32) NOT NULL CHECK (vehicle_category IN ('TWO_WHEELER', 'COMMERCIAL_FLEET')),
    fleet_size INT NOT NULL,
    requested_limit_inr NUMERIC(12,2) NOT NULL,
    sanctioned_limit_inr NUMERIC(12,2) NOT NULL,
    utilisation_pct NUMERIC(5,2) NOT NULL,
    decision VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    trace_id VARCHAR(64),
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS insurance_policies (
    policy_id UUID PRIMARY KEY,
    customer_id UUID NOT NULL REFERENCES customers(customer_id),
    loan_id UUID NOT NULL REFERENCES loan_applications(loan_id),
    policy_type VARCHAR(64) NOT NULL,
    fasttag_id VARCHAR(64) NOT NULL,
    premium_inr NUMERIC(10,2) NOT NULL,
    coverage_inr NUMERIC(12,2) NOT NULL,
    status VARCHAR(32) NOT NULL,
    trace_id VARCHAR(64),
    issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS disbursements (
    disbursement_id UUID PRIMARY KEY,
    loan_id UUID NOT NULL REFERENCES loan_applications(loan_id),
    sanction_letter_no VARCHAR(64) NOT NULL,
    cbs_utr_number VARCHAR(64),
    nach_mandate_id VARCHAR(64) NOT NULL,
    disbursed_amount_inr NUMERIC(12,2) NOT NULL,
    dealer_account_no VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL CHECK (status IN ('PENDING', 'DISBURSED', 'FAILED', 'REPLAYED')),
    attempts INT NOT NULL DEFAULT 0,
    replay_of UUID,
    disbursed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS chaos_fault_states (
    fault_id VARCHAR(64) PRIMARY KEY,
    fault_class VARCHAR(16) NOT NULL CHECK (fault_class IN ('BUSINESS', 'INFRA')),
    fault_type VARCHAR(64) NOT NULL CHECK (fault_type IN (
        'foir_threshold_regression',
        'insurance_no_policy_write',
        'emi_rounding',
        'cibil_stub_900',
        'step_order_insurance_before_kyc',
        'cloudsql_lock_contention',
        'gke_oom_503',
        'gce_vm_latency_504'
    )),
    target_service VARCHAR(64) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT false,
    parameters_json JSONB,
    triggered_by VARCHAR(32),
    activated_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_loan_applications_status ON loan_applications(status);
CREATE INDEX IF NOT EXISTS idx_loan_applications_journey_id ON loan_applications(journey_id);
CREATE INDEX IF NOT EXISTS idx_loan_applications_customer_id ON loan_applications(customer_id);
CREATE INDEX IF NOT EXISTS idx_disbursements_status ON disbursements(status);
CREATE INDEX IF NOT EXISTS idx_disbursements_loan_id ON disbursements(loan_id);

-- Seed 8 fictional vehicles (Cymbal Motors, 100% fictional vehicle-finance domain)
INSERT INTO vehicles (vehicle_id, make, model, category, ex_showroom_inr, cdn_image_url, min_down_payment_pct)
VALUES
    ('VEH-EV-001', 'Cymbal Motors', 'Volt Pulse EV', 'EV_SUV', 1850000.00, '/assets/vehicles/volt-pulse-ev.svg', 10.00),
    ('VEH-EV-002', 'Cymbal Motors', 'Aether Crest EV', 'EV_SUV', 2490000.00, '/assets/vehicles/aether-crest-ev.svg', 12.50),
    ('VEH-SUV-001', 'Cymbal Motors', 'Terra Ridge 4x4', 'PASSENGER_SUV', 1620000.00, '/assets/vehicles/terra-ridge.svg', 15.00),
    ('VEH-SUV-002', 'Cymbal Motors', 'Solstice Tourer', 'PASSENGER_SUV', 1290000.00, '/assets/vehicles/solstice-tourer.svg', 10.00),
    ('VEH-2W-001', 'Cymbal Mobility', 'SwiftGlide 125', 'TWO_WHEELER', 98000.00, '/assets/vehicles/swiftglide-125.svg', 5.00),
    ('VEH-2W-002', 'Cymbal Mobility', 'SparkGlide EV Pro', 'TWO_WHEELER', 142000.00, '/assets/vehicles/sparkglide-ev.svg', 5.00),
    ('VEH-FLT-001', 'Cymbal Commercial', 'Titan Hauler 16T', 'COMMERCIAL_FLEET', 3450000.00, '/assets/vehicles/titan-hauler-16t.svg', 20.00),
    ('VEH-FLT-002', 'Cymbal Commercial', 'CargoMax Express 9T', 'COMMERCIAL_FLEET', 2180000.00, '/assets/vehicles/cargomax-9t.svg', 15.00)
ON CONFLICT (vehicle_id) DO NOTHING;

-- Build Decision A12 + #82: godview_readonly role with read-only transaction enforcement,
-- 5s statement timeout, cloudsqlsuperuser revoked, and SELECT on all 9 business tables.
CREATE ROLE godview_readonly NOLOGIN;
REVOKE cloudsqlsuperuser FROM godview_readonly;
ALTER ROLE godview_readonly SET default_transaction_read_only = on;
ALTER ROLE godview_readonly SET statement_timeout = '5s';
GRANT USAGE ON SCHEMA public TO godview_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO godview_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO godview_readonly;
