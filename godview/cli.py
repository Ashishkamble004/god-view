# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Portable CLI utility for God-View v4 Sovereign SRE Organism (`godview`).

Provides environment-neutral commands to inspect, perceive, serve, evolve,
and deploy God-View across local Docker, Kubernetes, or Google Cloud Run:
  - `godview doctor`
  - `godview perceive`
  - `godview serve`
  - `godview harness inspect | evolve`
  - `godview deploy`
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from typing import Any


def _ensure_cli_env(env: str | None = None) -> None:
    if env:
        os.environ["GODVIEW_ENV"] = env
    else:
        os.environ.setdefault("GODVIEW_ENV", "hermetic")
    for k in ("APPLICATION", "BUSINESS_LINE", "CHANGE", "DATA", "PLATFORM", "SOVEREIGN"):
        os.environ.setdefault(f"GODVIEW_BUDGET_{k}_USD", "1.00")
    os.environ.setdefault("GODVIEW_COURT_DAILY_CEILING_USD", "10.00")


def _cmd_doctor(args: argparse.Namespace) -> int:
    _ensure_cli_env(args.env)

    from godview.harness.core import HarnessTree, validate
    from godview.harness.substrate import GODVIEW_SURFACE, GodViewSubstrate
    from godview_shared.config import GodViewSettings

    settings = GodViewSettings.from_env()
    domains = ("application", "data", "business_line", "change", "platform")
    harness_checks: list[dict[str, Any]] = []
    all_ok = True

    for dom in domains:
        sub = GodViewSubstrate(domain=dom)
        tree = HarnessTree(root=sub.base_tree(), surface=GODVIEW_SURFACE)
        rep = validate(tree)
        if not rep.ok:
            all_ok = False
        harness_checks.append(
            {
                "domain": dom,
                "ok": rep.ok,
                "always_on_tokens": rep.always_on_tokens,
                "always_on_max": GODVIEW_SURFACE.always_on_max,
                "sub_agents": [p.parent.name for p in tree.sub_agents()],
                "skills": [p.parent.name for p in tree.skills()],
                "scripts": [p.name for p in tree.scripts()],
                "errors": rep.errors,
            }
        )

    summary = {
        "status": "ok" if all_ok else "degraded",
        "version": "4.0.0",
        "env_mode": settings.env_mode,
        "python": sys.version.split()[0],
        "harness_surface": GODVIEW_SURFACE.name,
        "always_on_token_cap": GODVIEW_SURFACE.always_on_max,
        "archetype_trees": harness_checks,
    }
    if getattr(args, "json", False):
        print(json.dumps(summary, indent=2))
    else:
        print(f"God-View v4.0.0 Doctor — status={summary['status']} (env={settings.env_mode})")
        for hc in harness_checks:
            mark = "PASS" if hc["ok"] else "FAIL"
            print(
                f"  [{mark}] {hc['domain']:14s} | tokens={hc['always_on_tokens']}/{hc['always_on_max']} "
                f"| sub_agents={len(hc['sub_agents'])} skills={len(hc['skills'])} scripts={len(hc['scripts'])}"
            )
    return 0 if all_ok else 1


def _cmd_perceive(args: argparse.Namespace) -> int:
    if args.env:
        os.environ["GODVIEW_ENV"] = args.env
    if args.project:
        os.environ["GOOGLE_CLOUD_PROJECT"] = args.project

    from godview.sovereign.bootstrap import build_context
    from godview.sovereign.perception import PerceptionOrgan, resolve_env_id
    from godview.sovereign.topology.builder import TopologyBuilder

    ctx = build_context()
    env_id = resolve_env_id(ctx.settings)
    organ = PerceptionOrgan(
        firestore=ctx.docs,
        pubsub=ctx.bus,
        adapters=ctx.adapters,
        settings=ctx.settings,
        llm=ctx.llm,
        topology_builder=TopologyBuilder(env_id=env_id),
    )
    report = asyncio.run(organ.perceive(reset=bool(args.reset)))
    out = report.model_dump(mode="json")
    if getattr(args, "json", False):
        print(json.dumps(out, indent=2))
    else:
        print(
            f"Perception completed for env_id={report.env_id}: "
            f"services={report.services_count}, edges={report.edges_count}, "
            f"entities={report.entities_count}, journeys={report.journeys_count}"
        )
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    if args.env:
        os.environ["GODVIEW_ENV"] = args.env
    uvicorn.run(
        "godview.sovereign.control_api.app:app",
        host=args.host,
        port=args.port,
        reload=bool(args.reload),
    )
    return 0


def _cmd_harness(args: argparse.Namespace) -> int:
    from godview.sovereign.control_api.routers.harness import (
        _build_harness_overview,
        _get_agent_tree,
        _serialize_tree,
    )

    if args.subcommand == "overview":
        print(json.dumps(_build_harness_overview(ctx=None), indent=2))
        return 0

    agent_id = args.agent or "application__specialist"
    if args.subcommand == "inspect":
        dom, tree, ver = _get_agent_tree(agent_id, ctx=None)
        print(json.dumps(_serialize_tree(agent_id, dom, tree, version=ver), indent=2))
        return 0

    if args.subcommand == "evolve":
        from godview.harness.rsi import evolve_specialist

        res = evolve_specialist(
            agent_id=agent_id,
            trigger="cli_manual",
            ctx=None,
            mode=args.mode,
            k=args.k,
        )
        print(
            json.dumps(
                {
                    "agent_id": res.agent_id,
                    "domain": res.domain,
                    "passed": res.passed,
                    "score": res.score,
                    "winning_tag": res.winning_tag,
                    "living_model_version": res.living_model_version,
                    "adopted_path": res.adopted_path,
                    "notes": res.notes,
                },
                indent=2,
            )
        )
        return 0
    return 1


def _cmd_deploy(args: argparse.Namespace) -> int:
    project = args.project or os.environ.get("GOOGLE_CLOUD_PROJECT", "YOUR_PROJECT_ID")
    region = args.region or os.environ.get("GODVIEW_REGIONS", "us-central1")
    target = args.target

    recipes: dict[str, list[str]] = {
        "docker": [
            "docker compose up --build -d",
        ],
        "cloud-run": [
            f"gcloud builds submit --project={project} --tag {region}-docker.pkg.dev/{project}/godview/sovereign:latest -f godview/sovereign/Dockerfile .",
            f"gcloud run deploy god-view-sovereign --project={project} --region={region} --image={region}-docker.pkg.dev/{project}/godview/sovereign:latest --set-env-vars=GODVIEW_ENV=gcp,GOOGLE_CLOUD_PROJECT={project},GODVIEW_REGIONS={region}",
            f"gcloud builds submit --project={project} --tag {region}-docker.pkg.dev/{project}/godview/cockpit:latest -f godview/god-view-sre-web/Dockerfile godview/god-view-sre-web",
            f"gcloud run deploy god-view-sre-web --project={project} --region={region} --image={region}-docker.pkg.dev/{project}/godview/cockpit:latest",
        ],
        "k8s": [
            "kubectl create namespace godview-system --dry-run=client -o yaml | kubectl apply -f -",
            "kubectl -n godview-system create deployment god-view-sovereign --image=godview-sovereign:latest --dry-run=client -o yaml | kubectl apply -f -",
        ],
        "terraform": [
            "terraform -chdir=infra/terraform/platform init",
            f"terraform -chdir=infra/terraform/platform apply -var='project_id={project}' -var='region={region}'",
        ],
    }
    cmds = recipes.get(target, [])
    if getattr(args, "json", False):
        print(json.dumps({"target": target, "project": project, "region": region, "commands": cmds}, indent=2))
        return 0

    print(f"=== God-View Portable Deploy Plan (target={target}) ===")
    for c in cmds:
        print(f"  $ {c}")
    if getattr(args, "execute", False):
        for c in cmds:
            subprocess.run(c, shell=True, check=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="godview",
        description="God-View v4 — Portable Sovereign SRE Organism & Harness² CLI Utility",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_doc = sub.add_parser("doctor", help="Validate environment and Harness² C1-C8 trees")
    p_doc.add_argument("--env", choices=["hermetic", "local", "gcp"], default=None)
    p_doc.add_argument("--json", action="store_true")
    p_doc.set_defaults(func=_cmd_doctor)

    p_perc = sub.add_parser("perceive", help="Run autonomous 6-sense environment discovery")
    p_perc.add_argument("--env", choices=["hermetic", "local", "gcp"], default=None)
    p_perc.add_argument("--project", default=None)
    p_perc.add_argument("--reset", action="store_true")
    p_perc.add_argument("--json", action="store_true")
    p_perc.set_defaults(func=_cmd_perceive)

    p_srv = sub.add_parser("serve", help="Start the Sovereign Control API server")
    p_srv.add_argument("--env", choices=["hermetic", "local", "gcp"], default=None)
    p_srv.add_argument("--host", default="0.0.0.0")
    p_srv.add_argument("--port", type=int, default=8080)
    p_srv.add_argument("--reload", action="store_true")
    p_srv.set_defaults(func=_cmd_serve)

    p_har = sub.add_parser("harness", help="Inspect or evolve Harness² agent trees")
    p_har.add_argument("subcommand", choices=["overview", "inspect", "evolve"])
    p_har.add_argument("--agent", default="application__specialist")
    p_har.add_argument("--mode", choices=["parallel", "sequential"], default="parallel")
    p_har.add_argument("--k", type=int, default=1)
    p_har.set_defaults(func=_cmd_harness)

    p_dep = sub.add_parser("deploy", help="Generate or run portable deployment commands")
    p_dep.add_argument("--target", choices=["docker", "cloud-run", "k8s", "terraform"], default="cloud-run")
    p_dep.add_argument("--project", default=None)
    p_dep.add_argument("--region", default="us-central1")
    p_dep.add_argument("--json", action="store_true")
    p_dep.add_argument("--execute", action="store_true")
    p_dep.set_defaults(func=_cmd_deploy)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
