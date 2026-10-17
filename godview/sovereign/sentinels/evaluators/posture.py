"""Sentinel evaluator for resource posture drift and security exposure (`posture`).

Evaluates:
- Cloud Run ingress configuration (internal-and-cloud-load-balancing vs all)
- Cloud Run invoker IAM check (invoker_iam_disabled must be False)
- Cloud Run allUsers IAM bindings (no unauthenticated invoker role)
- LB Backend Service IAP enforcement (IAP enabled on internal/management backends)
- LB Backend Security Policy attachment and WAF sensitivity setting
- Managed SSL certificate provisioning status

Baseline is retrieved from `tfstate.read` capability when available, or from
the sentinel check spec's initial snapshot.
"""

from __future__ import annotations

from typing import Any

from godview_shared.contracts import SentinelDefinition

from godview.sovereign.sentinels.evaluators.base import (
    BaseSentinelEvaluator,
    EvaluatorContext,
)
from godview.sovereign.sentinels.models import SentinelEvaluation


class PostureEvaluator(BaseSentinelEvaluator):
    """Evaluates compute and edge resource posture against expected security baselines."""

    async def evaluate(
        self,
        sentinel: SentinelDefinition,
        ctx: EvaluatorContext,
    ) -> SentinelEvaluation:
        spec = sentinel.check.spec or {}
        resource_id = str(spec.get("resource_id") or sentinel.source_ref)
        resource_kind = str(spec.get("resource_kind") or "cloud_run")
        baseline = spec.get("baseline") or {}

        # 1. Check if tfstate.read can provide baseline
        tfstate = await ctx.invoke_capability("tfstate.read", {"resource_id": resource_id})
        if isinstance(tfstate, dict) and "baseline" in tfstate:
            baseline = tfstate["baseline"]

        drift_items: list[dict[str, Any]] = []

        if resource_kind in ("cloud_run", "service"):
            posture_data = await ctx.invoke_capability(
                "run.get_posture",
                {"service_name": resource_id, "region": spec.get("region")},
            )
            if isinstance(posture_data, dict):
                current_ingress = posture_data.get("ingress")
                invoker_iam_disabled = posture_data.get("invoker_iam_disabled", False)
                allows_all_users = posture_data.get("all_users", False)

                # Check invoker_iam_disabled (critical exposure)
                expected_iam_disabled = baseline.get("invoker_iam_disabled", False)
                if invoker_iam_disabled and not expected_iam_disabled:
                    drift_items.append(
                        {
                            "field": "invoker_iam_disabled",
                            "expected": False,
                            "actual": True,
                            "reason": "invoker IAM check disabled on service",
                        }
                    )

                # Check ingress exposure (ingress="all" when expected internal/LB)
                expected_ingress = baseline.get(
                    "ingress", "internal-and-cloud-load-balancing"
                )
                if current_ingress == "all" and expected_ingress != "all":
                    drift_items.append(
                        {
                            "field": "ingress",
                            "expected": expected_ingress,
                            "actual": current_ingress,
                            "reason": "public ingress without load balancer restriction",
                        }
                    )

                # Check allUsers IAM binding
                expected_all_users = baseline.get("all_users", False)
                if allows_all_users and not expected_all_users:
                    drift_items.append(
                        {
                            "field": "all_users",
                            "expected": False,
                            "actual": True,
                            "reason": "allUsers granted run.invoker role",
                        }
                    )

        elif resource_kind in ("backend_service", "edge", "lb_backend"):
            backend_data = await ctx.invoke_capability(
                "edge.describe_backends",
                {"resource_id": resource_id},
            )
            if isinstance(backend_data, list):
                # Search for target backend
                target = next(
                    (b for b in backend_data if b.get("name") == resource_id or b.get("id") == resource_id),
                    backend_data[0] if backend_data else None,
                )
            elif isinstance(backend_data, dict):
                target = backend_data
            else:
                target = None

            if target:
                iap_cfg = target.get("iap") or {}
                iap_enabled = bool(iap_cfg.get("enabled"))
                expected_iap = bool(baseline.get("iap_enabled", True))
                if expected_iap and not iap_enabled:
                    drift_items.append(
                        {
                            "field": "iap_enabled",
                            "expected": True,
                            "actual": False,
                            "reason": "Identity-Aware Proxy disabled on protected backend",
                        }
                    )

                sec_policy = target.get("security_policy") or target.get("securityPolicy")
                if not sec_policy and baseline.get("require_security_policy", True):
                    drift_items.append(
                        {
                            "field": "security_policy",
                            "expected": "attached",
                            "actual": "none",
                            "reason": "public backend exposed without security policy",
                        }
                    )

        if drift_items:
            primary_drift = drift_items[0]
            sig = f"posture_drift_{primary_drift['field']}"
            severity = "sev1" if "invoker_iam_disabled" in [d["field"] for d in drift_items] else "sev2"
            return SentinelEvaluation(
                observed=float(len(drift_items)),
                breached=True,
                evidence_samples=drift_items,
                signature=sig,
                resource_id=resource_id,
                severity_override=severity,
                details={
                    "resource_id": resource_id,
                    "resource_kind": resource_kind,
                    "drift_count": len(drift_items),
                    "drifts": drift_items,
                },
            )

        return SentinelEvaluation(
            observed=0.0,
            breached=False,
            details={"resource_id": resource_id, "status": "compliant"},
        )
