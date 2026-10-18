"""Unit tests for Cloud Armor remediation executor (Plan §3.4, T1 Cloud Armor root cause)."""

from __future__ import annotations

import json
import urllib
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
import pytest

from godview.sovereign.remediation.executors.armor import (
    CloudArmorExecutor,
    patch_waf_expression,
)

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "remediation_fixtures.json"


@pytest.fixture
def remediation_fixtures() -> dict[str, Any]:
    with open(FIXTURES_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.unit
def test_patch_waf_expression_all_four_rules_pinned_to_sensitivity_1() -> None:
    """T1 Root Cause: CRS rule with 4 evaluatePreconfiguredWaf calls must add sensitivity: 1 to every call."""
    expr = (
        "evaluatePreconfiguredWaf('sqli-v33-stable') || "
        "evaluatePreconfiguredWaf('xss-v33-stable') || "
        "evaluatePreconfiguredWaf('lfi-v33-stable') || "
        "evaluatePreconfiguredWaf('rfi-v33-stable')"
    )

    patched = patch_waf_expression(expr, sensitivity=1)

    expected = (
        "evaluatePreconfiguredWaf('sqli-v33-stable', {'sensitivity': 1}) || "
        "evaluatePreconfiguredWaf('xss-v33-stable', {'sensitivity': 1}) || "
        "evaluatePreconfiguredWaf('lfi-v33-stable', {'sensitivity': 1}) || "
        "evaluatePreconfiguredWaf('rfi-v33-stable', {'sensitivity': 1})"
    )
    assert patched == expected


@pytest.mark.unit
def test_patch_waf_expression_updates_existing_strict_sensitivity() -> None:
    """Updating an existing sensitivity 4 -> 1."""
    expr = "evaluatePreconfiguredWaf('sqli-v33-stable', {'sensitivity': 4})"
    patched = patch_waf_expression(expr, sensitivity=1)
    assert patched == "evaluatePreconfiguredWaf('sqli-v33-stable', {'sensitivity': 1})"


@pytest.mark.unit
async def test_armor_executor_set_waf_sensitivity_preview_first_and_verify(
    remediation_fixtures: dict[str, Any],
) -> None:
    """Executes set_waf_sensitivity with preview_first=True: creates temp rule at higher priority, verifies, patches real rule, cleans up temp rule."""
    ca_fixture = remediation_fixtures["cloud_armor"]
    rule_state = dict(ca_fixture["rule_1000"])
    temp_rules: dict[int, dict[str, Any]] = {}
    patch_history: list[dict[str, Any]] = []
    added_rules: list[dict[str, Any]] = []
    removed_rules: list[int] = []

    policy_data = {
        "name": "edge-security-policy",
        "rules": [
            {"priority": 100, "action": "deny(403)", "description": "emergency kill switch", "match": {"versionedExpr": "SRC_IPS_V1", "config": {"srcIpRanges": ["*"]}}},
            rule_state,
        ],
    }

    def handle_request(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        # Policy GET (for priority discovery)
        if url.endswith("/securityPolicies/edge-security-policy"):
            return httpx.Response(200, json=policy_data)

        # Temp rule add
        if "addRule" in url:
            body = json.loads(request.content)
            added_rules.append(body)
            temp_rules[body["priority"]] = body
            return httpx.Response(200, json={"status": "DONE", "name": "op-add"})

        # Temp rule remove
        if "removeRule" in url:
            parsed = urllib.parse.urlsplit(url)
            qs = urllib.parse.parse_qs(parsed.query)
            pri = int(qs["priority"][0])
            removed_rules.append(pri)
            temp_rules.pop(pri, None)
            return httpx.Response(200, json={"status": "DONE", "name": "op-rm"})

        # getRule
        if "getRule" in url:
            parsed = urllib.parse.urlsplit(url)
            qs = urllib.parse.parse_qs(parsed.query)
            pri = int(qs["priority"][0])
            if pri == 1000:
                return httpx.Response(200, json=rule_state)
            if pri in temp_rules:
                return httpx.Response(200, json=temp_rules[pri])
            return httpx.Response(404)

        # patchRule
        if "patchRule" in url and "priority=1000" in url:
            body = json.loads(request.content)
            patch_history.append(body)
            if "preview" in body:
                rule_state["preview"] = body["preview"]
            if "match" in body and "expr" in body["match"]:
                rule_state["match"]["expr"]["expression"] = body["match"]["expr"]["expression"]
            return httpx.Response(200, json=rule_state)

        # probe
        if "test-probe" in url:
            return httpx.Response(422, json={"detail": "validation error"})

        return httpx.Response(404)

    transport = httpx.MockTransport(handle_request)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        executor = CloudArmorExecutor(http_client=mock_client)

        result = await executor.set_waf_sensitivity(
            ctx=None,
            args={
                "project_id": "proj-test",
                "security_policy": "edge-security-policy",
                "priority": 1000,
                "sensitivity": 1,
                "preview_first": True,
                "probe_url": "https://api.example.com/test-probe",
            },
            target_resource="edge-security-policy",
        )

        assert result.ok is True
        assert result.reverted is False

        # Temp rule was added at free priority 900 (1000 - 100) with preview=True
        assert len(added_rules) == 1
        assert added_rules[0]["priority"] == 900
        assert added_rules[0]["preview"] is True
        assert "{'sensitivity': 1}" in added_rules[0]["match"]["expr"]["expression"]

        # Temp rule was removed
        assert removed_rules == [900]

        # Real rule 1000 was NEVER patched with preview=True (WAF was never disabled!)
        assert len(patch_history) == 1
        assert patch_history[0]["preview"] is False
        assert "{'sensitivity': 1}" in patch_history[0]["match"]["expr"]["expression"]

        # Verification checks recorded (temp rule active, temp rule cleaned up, enforced, probe)
        check_names = [v.check for v in result.verification]
        assert "waf_preview_temp_rule_active" in check_names
        assert "waf_preview_temp_rule_cleaned_up" in check_names
        assert "waf_rule_enforced_with_sensitivity" in check_names
        assert "post_enforce_probe_bypasses_waf_block" in check_names

        # Final state
        assert result.after["preview"] is False
        assert "{'sensitivity': 1}" in result.after["expression"]


@pytest.mark.unit
async def test_armor_executor_rollback_on_failed_verification(
    remediation_fixtures: dict[str, Any],
) -> None:
    """Verification failure during set_waf_sensitivity must trigger auto-rollback."""
    ca_fixture = remediation_fixtures["cloud_armor"]
    orig_expr = ca_fixture["rule_1000"]["match"]["expr"]["expression"]
    rule_state = dict(ca_fixture["rule_1000"])

    call_count = 0

    def handle_request(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        url = str(request.url)
        if "getRule" in url:
            call_count += 1
            # Simulate a live desync on preview check: returns old expression
            if call_count == 2:
                return httpx.Response(
                    200,
                    json={
                        "priority": 1000,
                        "preview": False,
                        "match": {"expr": {"expression": orig_expr}},
                    },
                )
            return httpx.Response(200, json=rule_state)
        if "patchRule" in url:
            body = json.loads(request.content)
            if "preview" in body:
                rule_state["preview"] = body["preview"]
            if "match" in body:
                rule_state["match"]["expr"]["expression"] = body["match"]["expr"]["expression"]
            return httpx.Response(200, json=rule_state)
        if "test-probe" in url:
            return httpx.Response(422)
        return httpx.Response(404)

    transport = httpx.MockTransport(handle_request)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        executor = CloudArmorExecutor(http_client=mock_client)

        result = await executor.set_waf_sensitivity(
            ctx=None,
            args={
                "project_id": "proj-test",
                "security_policy": "edge-security-policy",
                "priority": 1000,
                "sensitivity": 1,
                "preview_first": False,
                "probe_url": "https://api.example.com/test-probe",
            },
            target_resource="edge-security-policy",
        )

        assert result.ok is False
        assert result.reverted is True
        # Original rule expression restored
        assert rule_state["match"]["expr"]["expression"] == orig_expr


@pytest.mark.unit
async def test_armor_executor_set_rule_preview_kill_switch(
    remediation_fixtures: dict[str, Any],
) -> None:
    """Toggling rule 100 preview for the kill switch."""
    ca_fixture = remediation_fixtures["cloud_armor"]
    rule_state = dict(ca_fixture["rule_100"])

    def handle_request(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "getRule" in url and "priority=100" in url:
            return httpx.Response(200, json=rule_state)
        if "patchRule" in url and "priority=100" in url:
            body = json.loads(request.content)
            rule_state["preview"] = body["preview"]
            return httpx.Response(200, json=rule_state)
        return httpx.Response(404)

    transport = httpx.MockTransport(handle_request)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        executor = CloudArmorExecutor(http_client=mock_client)

        # Engage kill switch: set preview=False on deny rule 100
        res = await executor.set_rule_preview(
            ctx=None,
            args={
                "project_id": "proj-test",
                "security_policy": "edge-security-policy",
                "priority": 100,
                "preview": False,
            },
            target_resource="edge-security-policy",
        )
        assert res.ok is True
        assert res.after["preview"] is False
        assert res.verification[0].passed is True

        # Disengage kill switch: set preview=True
        res_disengage = await executor.set_rule_preview(
            ctx=None,
            args={
                "project_id": "proj-test",
                "security_policy": "edge-security-policy",
                "priority": 100,
                "preview": True,
            },
            target_resource="edge-security-policy",
        )
        assert res_disengage.ok is True
        assert res_disengage.after["preview"] is True


@pytest.mark.unit
async def test_armor_executor_rollback_on_post_enforce_probe_403(
    remediation_fixtures: dict[str, Any],
) -> None:
    """If post-enforcement probe returns 403, rule must automatically roll back."""
    ca_fixture = remediation_fixtures["cloud_armor"]
    rule_state = dict(ca_fixture["rule_1000"])
    patch_history: list[dict[str, Any]] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "getRule" in url and "priority=1000" in url:
            return httpx.Response(200, json=rule_state)
        if "patchRule" in url and "priority=1000" in url:
            body = json.loads(request.content)
            patch_history.append(body)
            if "preview" in body:
                rule_state["preview"] = body["preview"]
            if "match" in body and "expr" in body["match"]:
                rule_state["match"]["expr"]["expression"] = body["match"]["expr"]["expression"]
            return httpx.Response(200, json=rule_state)
        if "test-probe" in url:
            # 403: WAF is blocking legitimate traffic!
            return httpx.Response(403, text="Forbidden by Cloud Armor WAF")
        return httpx.Response(404)

    transport = httpx.MockTransport(handle_request)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        executor = CloudArmorExecutor(http_client=mock_client)
        result = await executor.set_waf_sensitivity(
            ctx=None,
            args={
                "project_id": "proj-test",
                "priority": 1000,
                "sensitivity": 1,
                "probe_url": "https://api.example.com/test-probe",
            },
            target_resource="edge-security-policy",
        )

        assert result.ok is False
        assert result.reverted is True
        assert "403 Forbidden" in (result.error or "")


@pytest.mark.unit
async def test_armor_executor_ssrf_forbidden_url() -> None:
    """Probing internal metadata server addresses must raise ScopeViolationError."""
    from godview.sovereign.remediation.types import ScopeViolationError

    executor = CloudArmorExecutor()
    with pytest.raises(ScopeViolationError, match="SSRF protection"):
        await executor.set_waf_sensitivity(
            ctx=None,
            args={
                "probe_url": "http://169.254.169.254/computeMetadata/v1/",
            },
            target_resource="edge-security-policy",
        )


@pytest.mark.unit
async def test_armor_executor_ssrf_advanced_encodings() -> None:
    """Alternate SSRF encodings (decimal IP, IPv6 loopback, RFC1918) must all be rejected."""
    from godview.sovereign.remediation.executors.armor import validate_probe_url
    from godview.sovereign.remediation.types import ScopeViolationError

    # Decimal representation of 169.254.169.254
    with pytest.raises(ScopeViolationError, match="disallowed IP|SSRF"):
        validate_probe_url("http://2852039166/probe", env_mode="gcp")

    # IPv6 loopback
    with pytest.raises(ScopeViolationError, match="disallowed IP|SSRF"):
        validate_probe_url("http://[::1]/probe", env_mode="gcp")

    # Private RFC1918 address
    with pytest.raises(ScopeViolationError, match="disallowed IP|SSRF"):
        validate_probe_url("http://10.1.2.3/probe", env_mode="gcp")


@pytest.mark.unit
async def test_discover_rule_priority_disambiguates_kill_switch_from_waf() -> None:
    """Kill-switch discovery must specifically select the kill-switch rule and never match WAF rules."""
    executor = CloudArmorExecutor()

    policy_data = {
        "rules": [
            {
                "priority": 1000,
                "action": "deny(403)",
                "match": {"expr": {"expression": "evaluatePreconfiguredWaf('sqli-v33-stable')"}},
                "description": "OWASP CRS protection",
            },
            {
                "priority": 100,
                "action": "deny(403)",
                "match": {"versionedExpr": "SRC_IPS_V1", "config": {"srcIpRanges": ["*"]}},
                "description": "Emergency Kill Switch",
            },
        ]
    }

    async def handle_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=policy_data)

    transport = httpx.MockTransport(handle_request)
    async with httpx.AsyncClient(transport=transport) as client:
        # For WAF: discovers priority 1000
        waf_pri = await executor._discover_rule_priority(client, "test-proj", "edge-security-policy", {}, for_waf=True)
        assert waf_pri == 1000

        # For Kill Switch: discovers priority 100 (never 1000)
        kill_pri = await executor._discover_rule_priority(client, "test-proj", "edge-security-policy", {}, for_waf=False)
        assert kill_pri == 100


@pytest.mark.unit
async def test_discover_rule_priority_aborts_when_rule_not_found_or_policy_fails() -> None:
    """Verifier-core #129 item 3: abort without hardcoded fallbacks if policy can't be read or rule isn't found."""
    executor = CloudArmorExecutor()

    # Case 1: Policy GET fails (404/500)
    async def handle_404(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_404)) as client:
        with pytest.raises(RuntimeError, match="Failed to read security policy"):
            await executor._discover_rule_priority(client, "test-proj", "missing-policy", {}, for_waf=True)

    # Case 2: Policy has no WAF rule
    policy_no_waf = {"rules": [{"priority": 2147483647, "action": "allow", "match": {"versionedExpr": "SRC_IPS_V1"}}]}
    async def handle_no_waf(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=policy_no_waf)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_no_waf)) as client:
        with pytest.raises(RuntimeError, match="No WAF rule matching evaluatePreconfiguredWaf found"):
            await executor._discover_rule_priority(client, "test-proj", "edge-security-policy", {}, for_waf=True)

    # Case 3: Policy has no kill-switch rule
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_no_waf)) as client:
        with pytest.raises(RuntimeError, match="No kill-switch rule discovered"):
            await executor._discover_rule_priority(client, "test-proj", "edge-security-policy", {}, for_waf=False)


@pytest.mark.unit
def test_derive_probe_url_from_incident_log_samples() -> None:
    """Verifier-core #129 item 4: probe URL is derived from raw_evidence.log_samples[*].url as side-effect-free GET canary."""
    from godview_shared.fakes import InMemoryFirestore

    class MockCtx:
        def __init__(self) -> None:
            self.docs = InMemoryFirestore()

    ctx = MockCtx()
    ctx.docs.set(
        "incidents",
        "inc-waf-01",
        {
            "incident_id": "inc-waf-01",
            "raw_evidence": {
                "log_samples": [
                    {"url": "https://service.tenant.example.com/api/v1/apply", "status": 403}
                ]
            },
        },
    )

    executor = CloudArmorExecutor()
    probe_url = executor._derive_probe_url(
        ctx=ctx,
        args={"incident_id": "inc-waf-01"},
        target_resource="edge-security-policy",
        approval_id=None,
        env_mode="gcp",
    )
    # Side-effect-free GET canary
    assert probe_url == "https://service.tenant.example.com/?a=b-c-d"
