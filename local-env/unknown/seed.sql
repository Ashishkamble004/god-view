-- Fictional Parcel Logistics Schema & Seed Data

CREATE TABLE IF NOT EXISTS parcel_manifests (
    parcel_id VARCHAR(64) PRIMARY KEY,
    origin VARCHAR(128) NOT NULL,
    destination VARCHAR(128) NOT NULL,
    weight_kg NUMERIC(8, 2) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'RECEIVED',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transit_routes (
    route_id VARCHAR(64) PRIMARY KEY,
    parcel_id VARCHAR(64) REFERENCES parcel_manifests(parcel_id),
    carrier_code VARCHAR(32) NOT NULL,
    estimated_transit_hours NUMERIC(6, 2) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PLANNED',
    calculated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS courier_dispatches (
    dispatch_id VARCHAR(64) PRIMARY KEY,
    route_id VARCHAR(64) REFERENCES transit_routes(route_id),
    courier_id VARCHAR(64) NOT NULL,
    dispatch_status VARCHAR(32) NOT NULL DEFAULT 'DISPATCHED',
    dispatched_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS delivery_proofs (
    proof_id VARCHAR(64) PRIMARY KEY,
    parcel_id VARCHAR(64) REFERENCES parcel_manifests(parcel_id),
    recipient_name VARCHAR(128) NOT NULL,
    signature_hash VARCHAR(128) NOT NULL,
    delivered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Seed rows
INSERT INTO parcel_manifests (parcel_id, origin, destination, weight_kg, status)
VALUES
    ('PCL-9001', 'Hub North', 'Depot West', 4.50, 'DELIVERED'),
    ('PCL-9002', 'Hub South', 'Depot East', 1.25, 'IN_TRANSIT')
ON CONFLICT (parcel_id) DO NOTHING;

INSERT INTO transit_routes (route_id, parcel_id, carrier_code, estimated_transit_hours, status)
VALUES
    ('RT-1001', 'PCL-9001', 'FAST_POST', 6.00, 'COMPLETED'),
    ('RT-1002', 'PCL-9002', 'EXPRESS_CARRIER', 4.50, 'IN_PROGRESS')
ON CONFLICT (route_id) DO NOTHING;

INSERT INTO courier_dispatches (dispatch_id, route_id, courier_id, dispatch_status)
VALUES
    ('DSP-5001', 'RT-1001', 'CR-881', 'COMPLETED'),
    ('DSP-5002', 'RT-1002', 'CR-412', 'DISPATCHED')
ON CONFLICT (dispatch_id) DO NOTHING;

INSERT INTO delivery_proofs (proof_id, parcel_id, recipient_name, signature_hash)
VALUES
    ('PRF-3001', 'PCL-9001', 'Taylor Smith', 'sha256_mock_sig_abc123')
ON CONFLICT (proof_id) DO NOTHING;
