# Copyright 2026 Harness² Authors & Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Runtime configuration helpers for Harness² core."""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def load_env(path: Path | None = None) -> dict[str, str]:
    """Load key=value pairs from .env into os.environ via setdefault."""
    env_file = path or (_REPO_ROOT / ".env")
    loaded: dict[str, str] = {}
    if not env_file.is_file():
        return loaded
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip("\"'")
        if k:
            os.environ.setdefault(k, v)
            loaded[k] = os.environ[k]
    return loaded


def require_env(*keys: str) -> dict[str, str]:
    """Ensure the requested environment variables are set and non-empty."""
    load_env()
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            f"Copy .env.example -> .env and fill them in."
        )
    return {k: os.environ[k] for k in keys}


def repo_root() -> Path:
    """Return the repository root path."""
    return _REPO_ROOT
