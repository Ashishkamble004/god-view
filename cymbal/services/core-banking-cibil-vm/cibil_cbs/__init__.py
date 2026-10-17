"""Utility 5: Credit Bureau (CIBIL) & Core Banking Settlement (CBS) VM Service."""

from __future__ import annotations

from typing import Any

from cibil_cbs.models import (
    ChaosFaultClass,
    ChaosFaultStateRow,
    ChaosFaultType,
    DisbursementRow,
    DisbursementStatus,
    DisburseRequest,
    DisburseResponse,
    ScoreRequest,
    ScoreResponse,
)
from cibil_cbs.repository import (
    CbsRepositoryProtocol,
    InMemoryCbsRepository,
    PostgresCbsRepository,
    RepositoryConfigurationError,
    RepositoryError,
    create_repository_from_env,
)
from cibil_cbs.service import CibilCbsService, compute_cibil_score


def __getattr__(name: str) -> Any:
    if name in ("app", "create_app"):
        from cibil_cbs import main

        return getattr(main, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "ChaosFaultClass",
    "ChaosFaultStateRow",
    "ChaosFaultType",
    "CbsRepositoryProtocol",
    "CibilCbsService",
    "DisburseRequest",
    "DisburseResponse",
    "DisbursementRow",
    "DisbursementStatus",
    "InMemoryCbsRepository",
    "PostgresCbsRepository",
    "RepositoryConfigurationError",
    "RepositoryError",
    "ScoreRequest",
    "ScoreResponse",
    "app",
    "compute_cibil_score",
    "create_app",
    "create_repository_from_env",
]
