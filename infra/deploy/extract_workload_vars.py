#!/usr/bin/env python3
"""Bridge infra/terraform/platform/outputs.json to declared variables in infra/terraform/workloads."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def parse_declared_variables(workloads_dir: Path) -> set[str]:
    declared: set[str] = set()
    for tf_file in workloads_dir.glob("*.tf"):
        content = tf_file.read_text(encoding="utf-8")
        for match in re.finditer(r'variable\s+"([^"]+)"', content):
            declared.add(match.group(1))
    return declared


def unwrap_outputs(raw_outputs: dict[str, Any]) -> dict[str, Any]:
    unwrapped: dict[str, Any] = {}
    for k, v in raw_outputs.items():
        if isinstance(v, dict) and "value" in v:
            unwrapped[k] = v["value"]
        else:
            unwrapped[k] = v
    return unwrapped


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract workload variables from platform outputs.json")
    parser.add_argument("--platform-outputs", required=True)
    parser.add_argument("--workloads-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    outputs_path = Path(args.platform_outputs)
    workloads_dir = Path(args.workloads_dir)
    out_path = Path(args.out)

    if not outputs_path.exists():
        return

    raw = json.loads(outputs_path.read_text(encoding="utf-8"))
    flat = unwrap_outputs(raw)
    declared = parse_declared_variables(workloads_dir)

    # Direct matching variable names + common alias mappings between platform outputs and workloads vars
    alias_map = {
        "gke_dns_endpoint": ["gke_dns_endpoint", "cluster_endpoint"],
        "gke_cluster_name": ["gke_cluster_name", "cluster_name"],
        "subnet_cidr": ["subnet_cidr", "vpc_subnet_cidr"],
        "psa_range": ["psa_range", "psa_cidr"],
        "sql_private_ip": ["sql_private_ip", "db_host", "postgres_host"],
        "cbs_vm_internal_ip": ["cbs_vm_internal_ip", "vm_internal_ip", "cbs_host"],
        "model_armor_template_id": ["model_armor_template_id"],
        "kyc_ilb_address": ["kyc_ilb_address", "kyc_ilb_ip"],
        "los_ilb_address": ["los_ilb_address", "los_ilb_ip"],
        "ilb_addresses": ["ilb_addresses"],
        "sovereign_service_url": ["sovereign_service_url", "sovereign_url"],
        "sovereign_uri": ["sovereign_service_url", "sovereign_url"],
        "sovereign_url": ["sovereign_service_url", "sovereign_url"],
        "otel_exporter_otlp_endpoint": ["otel_exporter_otlp_endpoint"],
    }

    resolved: dict[str, Any] = {}
    for var_name in declared:
        if var_name in flat:
            resolved[var_name] = flat[var_name]

    for src_key, target_vars in alias_map.items():
        if src_key in flat:
            for target_var in target_vars:
                if target_var in declared and target_var not in resolved:
                    resolved[target_var] = flat[src_key]

    out_path.write_text(json.dumps(resolved, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
