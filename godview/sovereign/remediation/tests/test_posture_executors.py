"""Unit tests for Cloud Run, Pub/Sub, GKE, Tenant Admin, and Agent executors (Plan §3.4)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from godview.sovereign.remediation.executors.agent import AgentScalingExecutor
from godview.sovereign.remediation.executors.gke import GkeProbeExecutor
from godview.sovereign.remediation.executors.pubsub import PubSubDlqExecutor
from godview.sovereign.remediation.executors.run_posture import (
    CloudRunPostureExecutor,
)
from godview.sovereign.remediation.executors.tenant_admin import (
    TenantAdminExecutor,
)

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "remediation_fixtures.json"


@pytest.fixture
def remediation_fixtures() -> dict[str, Any]:
    with open(FIXTURES_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.unit
async def test_cloud_run_set_ingress(remediation_fixtures: dict[str, Any]) -> None:
    """Cloud Run set_ingress patches with updateMask=ingress and verifies live posture."""
    cr_fixture = remediation_fixtures["cloud_run"]
    live_service = {
        "name": cr_fixture["service_name"],
        "ingress": cr_fixture["ingress"],
    }

    def handle_request(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=live_service)
        if request.method == "PATCH":
            body = json.loads(request.content)
            live_service["ingress"] = body["ingress"]
            return httpx.Response(200, json=live_service)
        return httpx.Response(404)

    transport = httpx.MockTransport(handle_request)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        executor = CloudRunPostureExecutor(http_client=mock_client)

        result = await executor.set_ingress(
            ctx=None,
            args={
                "project_id": "proj-test",
                "region": "us-central1",
                "service": "svc-origination-901",
                "ingress": "internal-and-cloud-load-balancing",
            },
            target_resource="svc-origination-901",
        )

        assert result.ok is True
        assert result.reverted is False
        assert result.after["ingress"] == "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
        assert result.verification[0].passed is True


@pytest.mark.unit
async def test_cloud_run_set_invoker_iam(remediation_fixtures: dict[str, Any]) -> None:
    """Cloud Run set_invoker_iam removes allUsers to enforce authentication."""
    cr_fixture = remediation_fixtures["cloud_run"]
    live_policy = dict(cr_fixture["iam_policy"])

    def handle_request(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if ":getIamPolicy" in url:
            return httpx.Response(200, json=live_policy)
        if ":setIamPolicy" in url:
            body = json.loads(request.content)
            live_policy["bindings"] = body["policy"]["bindings"]
            return httpx.Response(200, json=live_policy)
        if "services/svc-origination-901" in url:
            return httpx.Response(200, json={"invokerIamDisabled": False})
        return httpx.Response(404)

    transport = httpx.MockTransport(handle_request)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        executor = CloudRunPostureExecutor(http_client=mock_client)

        result = await executor.set_invoker_iam(
            ctx=None,
            args={
                "project_id": "proj-test",
                "region": "us-central1",
                "service": "svc-origination-901",
                "invoker_iam_disabled": False,
            },
            target_resource="svc-origination-901",
        )

        assert result.ok is True
        assert result.verification[0].passed is True
        # Check allUsers removed
        has_all_users = any(
            "allUsers" in b.get("members", [])
            for b in live_policy.get("bindings", [])
            if b.get("role") == "roles/run.invoker"
        )
        assert has_all_users is False


@pytest.mark.unit
async def test_pubsub_replay_dlq(remediation_fixtures: dict[str, Any]) -> None:
    """Pub/Sub replay_dlq creates missing subscription, pulls messages, republishes, and acks."""
    ps_fixture = remediation_fixtures["pubsub"]
    subscription_exists = False
    republished_messages: list[dict[str, Any]] = []
    acknowledged_ids: list[str] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        nonlocal subscription_exists
        url = str(request.url)
        if request.method == "GET" and "subscriptions" in url:
            if not subscription_exists:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json={"name": url})
        if request.method == "PUT" and "subscriptions" in url:
            subscription_exists = True
            return httpx.Response(200, json={"name": url})
        if ":pull" in url:
            return httpx.Response(200, json={"receivedMessages": ps_fixture["messages"]})
        if ":publish" in url:
            body = json.loads(request.content)
            republished_messages.extend(body["messages"])
            return httpx.Response(200, json={"messageIds": ["msg-001"]})
        if ":acknowledge" in url:
            body = json.loads(request.content)
            acknowledged_ids.extend(body["ackIds"])
            return httpx.Response(200, json={})
        return httpx.Response(404)

    transport = httpx.MockTransport(handle_request)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        executor = PubSubDlqExecutor(http_client=mock_client)

        result = await executor.execute(
            ctx=None,
            args={
                "project_id": "proj-test",
                "topic": "godview-nervous-system-dlq",
                "target_topic": "godview-nervous-system",
                "subscription": "godview-nervous-system-dlq-sub",
            },
            target_resource="godview-nervous-system-dlq",
        )

        assert result.ok is True
        assert subscription_exists is True
        assert len(republished_messages) == 1
        assert len(acknowledged_ids) == 1
        assert result.after["republished_count"] == 1


@pytest.mark.unit
async def test_gke_patch_probe(remediation_fixtures: dict[str, Any]) -> None:
    """GKE patch_probe patches container probe on deployment spec."""
    k8s_fixture = remediation_fixtures["kubernetes"]
    live_deployment = {
        "metadata": {"name": k8s_fixture["deployment"], "namespace": k8s_fixture["namespace"]},
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "workload-api",
                            "image": "gcr.io/test/workload:v1",
                        }
                    ]
                }
            }
        },
    }

    def handle_request(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=live_deployment)
        if request.method == "PATCH":
            body = json.loads(request.content)
            patch_containers = body["spec"]["template"]["spec"]["containers"]
            for pc in patch_containers:
                for c in live_deployment["spec"]["template"]["spec"]["containers"]:
                    if c["name"] == pc["name"]:
                        c.update(pc)
            return httpx.Response(200, json=live_deployment)
        return httpx.Response(404)

    transport = httpx.MockTransport(handle_request)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        executor = GkeProbeExecutor(http_client=mock_client)

        result = await executor.execute(
            ctx=None,
            args={
                "namespace": "default",
                "deployment": "workload-api",
                "container": "workload-api",
                "probe_type": "startupProbe",
                "probe_spec": {
                    "httpGet": {"path": "/healthz", "port": 8080},
                    "periodSeconds": 2,
                    "failureThreshold": 45,
                },
            },
            target_resource="workload-api",
        )

        assert result.ok is True
        assert result.verification[0].passed is True
        live_probe = live_deployment["spec"]["template"]["spec"]["containers"][0].get("startupProbe")
        assert live_probe["periodSeconds"] == 2
        assert live_probe["failureThreshold"] == 45


@pytest.mark.unit
async def test_tenant_admin_action() -> None:
    """TenantAdminExecutor invokes OpenAPI admin action with OIDC token."""
    invoked_auth: list[str] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        invoked_auth.append(request.headers.get("Authorization", ""))
        return httpx.Response(200, json={"status": "ok", "paused": True})

    ctx = MagicMock()
    ctx.settings = MagicMock(env_mode="gcp")
    ctx.discovered_admin_actions = {
        "tenant.pause": {
            "action_name": "tenant.pause",
            "operation_id": "pause",
            "method": "POST",
            "url": "https://api.example.com/admin/v1/tenant/pause",
            "service_url": "https://api.example.com",
        }
    }

    transport = httpx.MockTransport(handle_request)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        executor = TenantAdminExecutor(http_client=mock_client)

        result = await executor.execute(
            ctx=ctx,
            args={
                "action_name": "tenant.pause",
                "method": "POST",
                "body": {"reason": "Maintenance"},
            },
            target_resource="tenant.pause",
        )

        assert result.ok is True
        assert result.verification[0].passed is True
        assert len(invoked_auth) == 1
        assert "Bearer" in invoked_auth[0]

        # Negative test: raw un-discovered URL raises NoExecutorError
        from godview.sovereign.remediation.types import NoExecutorError

        with pytest.raises(NoExecutorError, match="No discovered tenant admin action"):
            await executor.execute(
                ctx=ctx,
                args={
                    "action_name": "undiscovered.action",
                    "url": "https://api.example.com/arbitrary/endpoint",
                },
                target_resource="undiscovered.action",
            )


@pytest.mark.unit
async def test_agent_scaling_executor() -> None:
    """AgentScalingExecutor calls ctx.agents.set_min_instances."""
    mock_agents = MagicMock()
    mock_agent_data = {"agent_id": "application__svc-01", "deployment_spec": {"min_instances": 1}}
    mock_agents.get_agent = MagicMock(return_value=mock_agent_data)

    def fake_set_min(agent_id: str, count: int) -> dict[str, str]:
        mock_agent_data["deployment_spec"]["min_instances"] = count
        return {"status": "ok"}

    mock_agents.set_min_instances = MagicMock(side_effect=fake_set_min)

    ctx = MagicMock()
    ctx.agents = mock_agents

    executor = AgentScalingExecutor()
    result = await executor.execute(
        ctx=ctx,
        args={"agent_id": "application__svc-01", "min_instances": 0},
        target_resource="application__svc-01",
    )

    assert result.ok is True
    assert result.before["min_instances"] == 1
    mock_agents.set_min_instances.assert_called_once_with("application__svc-01", 0)
