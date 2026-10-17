"""Emit the OpenAPI 3.1 contract for loan-orchestrator-api (`python -m app.export_openapi > openapi.json`)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.config import OrchestratorSettings
from app.main import create_app
from app.repository import InMemoryOrchestratorRepository


def build_openapi_schema() -> dict[str, Any]:
    """Generate the complete OpenAPI schema with /v1, /api/v1, and /admin/v1 routes and x-godview-* extensions."""
    settings = OrchestratorSettings(
        service_name="loan-orchestrator-api",
        use_in_memory_db=True,
    )
    repo = InMemoryOrchestratorRepository.from_schema_sql()
    app = create_app(settings=settings, repository=repo)
    return app.openapi()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export OpenAPI 3.1 JSON contract for Cymbal Auto Finance loan-orchestrator-api"
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Optional output file path (defaults to stdout)",
    )
    args = parser.parse_args(argv)
    schema = build_openapi_schema()
    rendered = json.dumps(schema, indent=2, sort_keys=False) + "\n"
    if args.output is not None:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
