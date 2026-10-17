import httpx
api_url = "https://god-view-loan-api-gw-9t9witd8.uc.gateway.dev"
veh_resp = httpx.get(f"{api_url}/v1/vehicles", timeout=10.0)
if veh_resp.status_code != 200:
    print("Veh error:", veh_resp.text)
    exit(1)
vehicles = veh_resp.json().get("vehicles", []) if isinstance(veh_resp.json(), dict) else veh_resp.json()

apply_resp = httpx.post(
    f"{api_url}/v1/loans/apply",
    json={
        "vehicle_id": vehicles[0]["vehicle_id"],
        "utility_type": "RETAIL_AUTO_LOAN",
        "loan_amount_inr": 950000.0,
        "down_payment_inr": 250000.0,
        "tenure_months": 48,
        "interest_rate_pct": 9.25,
        "kyc_mode": "TEXT_ONLY",
        "customer": {
            "full_name": "Aarav Deshmukh",
            "dob": "1991-05-14",
            "email": "aarav.deshmukh@example.org",
            "phone": "+919820112233",
            "city": "Pune",
            "employment_type": "SALARIED",
            "employer_name": "Apex Precision Systems",
            "monthly_income_inr": 145000.0,
            "pan_number": "ABCDE1234F",
            "aadhaar_number": "999911112222",
        },
        "loan_purpose_text": "Personal electric SUV purchase for daily commute",
        "employment_remarks": "Confirmed permanent employee for 6 years",
        "applicant_notes": "Standard retail auto loan application",
    },
    timeout=20.0
)
import json
print(json.dumps(apply_resp.json(), indent=2))
