"""Unit tests for Cloud Armor remediation executor (Plan §3.4, T1 Cloud Armor root cause)."""

from __future__ import annotations

import json
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
    """Executes set_waf_sensitivity against recorded API fixture, rolling out preview=true then preview=false."""
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
            # 422 validation response indicates request reached backend (not 403 WAF block)
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
        assert len(patch_history) == 2

        # First patch: preview=True
        assert patch_history[0]["preview"] is True
        assert "{'sensitivity': 1}" in patch_history[0]["match"]["expr"]["expression"]

        # Second patch: preview=False
        assert patch_history[1]["preview"] is False

        # Verification checks recorded
        assert len(result.verification) == 3
        check_names = [v.check for v in result.verification]
        assert "waf_rule_preview_mode_active" in check_names
        assert "post_enforce_probe_bypasses_waf_block" in check_names
        assert "waf_rule_enforced_with_sensitivity" in check_names

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
