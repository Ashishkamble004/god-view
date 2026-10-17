"""Environment-neutral configuration, model routing table, and operational defaults.

Enforces TDD I-02 (zero hardcoded application/tenant knowledge), I-08 (per-agent token
budgets and court ceiling), I-09 (Gemini 3.x/4 models only at location 'global'), and
Coordinator Decisions A1, A8, A9, A11, A15.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelFallback(BaseModel):
    """Fallback model specification with required Vertex AI location."""

    model_config = ConfigDict(frozen=True)

    model_id: str
    location: Literal["global", "us-central1"]


class ModelConfig(BaseModel):
    """Canonical model configuration entry (TDD I-09 & design §11)."""

    model_config = ConfigDict(frozen=True)

    role: str
    model_id: str
    location: Literal["global", "us-central1"]
    thinking_level: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    dimensions: int | None = None
    fallbacks: tuple[ModelFallback, ...] = ()


ALLOWED_MODEL_IDS: frozenset[str] = frozenset(
    {
        "gemini-3.8-flash",
        "gemini-4-argon",
        "gemini-nano-banana-2.1",
        "gemini-3-pro-image",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.5-flash-image",
        "text-embedding-005",
    }
)

GLOBAL_ONLY_PREFIXES: tuple[str, ...] = (
    "gemini-3",
    "gemini-4",
    "gemini-nano-banana",
)


def validate_model_location(model_id: str, location: str) -> bool:
    """Validate model ID and location pairing per TDD I-09.

    Raises ValueError if an unknown model ID is given, if a Gemini 3.x/4 or
    gemini-nano-banana model is paired with any location other than 'global',
    or if text-embedding-005 is paired with any location other than 'us-central1'.
    """
    if model_id not in ALLOWED_MODEL_IDS:
        raise ValueError(
            f"Model '{model_id}' is not in ALLOWED_MODEL_IDS: {sorted(ALLOWED_MODEL_IDS)}"
        )
    if model_id.startswith(GLOBAL_ONLY_PREFIXES):
        if location != "global":
            raise ValueError(
                f"Model '{model_id}' requires location='global' (got '{location}') per TDD I-09"
            )
        return True
    if model_id.startswith("gemini-2.5"):
        if location != "us-central1":
            raise ValueError(
                f"Fallback model '{model_id}' requires location='us-central1' (got '{location}') per TDD I-09"
            )
        return True
    if model_id == "text-embedding-005":
        if location != "us-central1":
            raise ValueError(
                f"Model '{model_id}' requires location='us-central1' (got '{location}')"
            )
        return True
    if location not in ("global", "us-central1"):
        raise ValueError(f"Unsupported location '{location}' for model '{model_id}'")
    return True


# Default God-View Agent & Proposer Model (Argon primary, Gemini 3.8 Flash fallback on 429/quota/budget)
DEFAULT_AGENT_MODEL_ID: str = "gemini-4-argon"
DEFAULT_AGENT_FALLBACK_MODEL_ID: str = "gemini-3.8-flash"
DEFAULT_AGENT_MODEL_LOCATION: Literal["global", "us-central1"] = "global"

MODEL_TABLE: dict[str, ModelConfig] = {
    "reasoning": ModelConfig(
        role="reasoning",
        model_id=DEFAULT_AGENT_MODEL_ID,
        location=DEFAULT_AGENT_MODEL_LOCATION,
        thinking_level="MEDIUM",
        fallbacks=(
            ModelFallback(
                model_id=DEFAULT_AGENT_FALLBACK_MODEL_ID,
                location=DEFAULT_AGENT_MODEL_LOCATION,
            ),
            ModelFallback(model_id="gemini-2.5-pro", location="us-central1"),
        ),
    ),
    "escalation": ModelConfig(
        role="escalation",
        model_id=DEFAULT_AGENT_MODEL_ID,
        location=DEFAULT_AGENT_MODEL_LOCATION,
        fallbacks=(
            ModelFallback(
                model_id=DEFAULT_AGENT_FALLBACK_MODEL_ID,
                location=DEFAULT_AGENT_MODEL_LOCATION,
            ),
        ),
    ),
    "persona": ModelConfig(
        role="persona",
        model_id="gemini-3.8-flash",
        location="global",
        thinking_level="LOW",
        fallbacks=(ModelFallback(model_id="gemini-2.5-flash", location="us-central1"),),
    ),
    "specimen_image": ModelConfig(
        role="specimen_image",
        model_id="gemini-nano-banana-2.1",
        location="global",
        fallbacks=(
            ModelFallback(model_id="gemini-3-pro-image", location="global"),
            ModelFallback(model_id="gemini-2.5-flash-image", location="us-central1"),
        ),
    ),
    "embedding": ModelConfig(
        role="embedding",
        model_id="text-embedding-005",
        location="us-central1",
        dimensions=768,
        fallbacks=(),
    ),
}

# Coordinator Decision A1: Court Manager hard-caps the court at 40 agents
DEFAULT_COURT_MAX_AGENTS: int = 40

# Coordinator Decision A9: Token budgets (per-agent daily USD & daily ceiling)
DEFAULT_AGENT_DAILY_BUDGETS_USD: dict[str, Decimal] = {
    "application": Decimal("0.75"),
    "business_line": Decimal("0.75"),
    "change": Decimal("0.75"),
    "data": Decimal("0.50"),
    "platform": Decimal("0.50"),
    "sovereign": Decimal("1.50"),
}
DEFAULT_ESCALATION_RESERVE_FRACTION: Decimal = Decimal("0.20")
DEFAULT_COURT_DAILY_CEILING_USD: Decimal = Decimal("6.00")
DEFAULT_MONTHLY_CEILING_USD: Decimal = Decimal("600.00")

# Coordinator Decision A8: Schedules & windows
DEFAULT_MORNING_DIGEST_IST: str = "09:05"
DEFAULT_NIGHTLY_REFLECTION_IST: str = "03:30"
DEFAULT_PROBE_RUN_CRON: str = "*/5 9-16 * * *"
DEFAULT_WARMTH_SWEEP_MINUTES: int = 15
DEFAULT_SENTINEL_BASELINE_WINDOW_DAYS: int = 7
DEFAULT_INVARIANT_SHADOW_WINDOW_HOURS: int = 24
DEFAULT_INVARIANT_AUTO_RETIREMENT_DAYS: int = 30
DEFAULT_TRAFFIC_WINDOW_START_IST: str = "09:00"
DEFAULT_TRAFFIC_WINDOW_END_IST: str = "17:00"
DEFAULT_COMMS_WINDOW_START_IST: str = "08:00"
DEFAULT_COMMS_WINDOW_END_IST: str = "22:00"

# Coordinator Decision A11: Project-agnostic discovery exclusion patterns for God-View observer infra
DEFAULT_HINT_EXCLUDE: str = (
    "*-god-view-tfstate,"
    "god-view-sovereign,"
    "god-view-sre-web,"
    "*-god-view-agent-staging,"
    "*-god-view-agent-skills"
)
OBSERVER_ROLE_LABEL_KEY: str = "godview_role"
OBSERVER_ROLE_LABEL_VALUE: str = "observer"
MANAGED_LABEL_KEY: str = "godview_managed"
MANAGED_LABEL_VALUE: str = "true"


def parse_csv_env(raw: str | None) -> tuple[str, ...]:
    """Parse a comma-separated environment variable into stripped non-empty tokens."""
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def is_excluded_resource(
    name: str,
    labels: Mapping[str, str] | None = None,
    hint_exclude: Sequence[str] | None = None,
) -> bool:
    """Return True if a resource belongs to God-View observer infra and must be excluded from tenant discovery (A11)."""
    if labels and labels.get(OBSERVER_ROLE_LABEL_KEY) == OBSERVER_ROLE_LABEL_VALUE:
        return True
    default_patterns = parse_csv_env(DEFAULT_HINT_EXCLUDE)
    extra_patterns = (
        tuple(hint_exclude)
        if hint_exclude is not None
        else parse_csv_env(os.environ.get("GODVIEW_HINT_EXCLUDE"))
    )
    patterns = tuple(dict.fromkeys(default_patterns + extra_patterns))
    for pattern in patterns:
        if name == pattern or fnmatch.fnmatch(name, pattern):
            return True
    return False


class GodViewSettings(BaseModel):
    """Runtime settings resolved from environment variables without hardcoded tenant knowledge."""

    env_mode: Literal["hermetic", "local", "gcp"] = Field(default="hermetic")
    court_max_agents: int = Field(
        default=DEFAULT_COURT_MAX_AGENTS, ge=1, le=DEFAULT_COURT_MAX_AGENTS
    )
    warm_agents_max: int = Field(default=2, ge=1, le=3)
    hint_repo_urls: tuple[str, ...] = Field(default=())
    hint_exclude: tuple[str, ...] = Field(
        default_factory=lambda: parse_csv_env(DEFAULT_HINT_EXCLUDE)
    )
    chat_enabled: bool = Field(default=False)
    court_daily_ceiling_usd: Decimal = Field(default=DEFAULT_COURT_DAILY_CEILING_USD, gt=0)
    escalation_reserve_fraction: Decimal = Field(
        default=DEFAULT_ESCALATION_RESERVE_FRACTION, gt=0, le=1
    )
    agent_daily_budgets_usd: dict[str, Decimal] = Field(
        default_factory=lambda: dict(DEFAULT_AGENT_DAILY_BUDGETS_USD)
    )
    sentinel_cadence_s: int = Field(default=10, ge=1)
    hibernate_cadence_s: int = Field(default=60, ge=10)
    default_model_id: str = Field(default=DEFAULT_AGENT_MODEL_ID)
    fallback_model_id: str = Field(default=DEFAULT_AGENT_FALLBACK_MODEL_ID)
    default_model_location: Literal["global", "us-central1"] = Field(
        default=DEFAULT_AGENT_MODEL_LOCATION
    )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> GodViewSettings:
        """Load settings from environment variables.

        Raises ValueError if GODVIEW_ENV is invalid, if court_max_agents > 40 (A1),
        or if required budget env vars are missing in 'gcp' or 'local' mode (A9).
        """
        source = env if env is not None else os.environ
        if "GODVIEW_ENV" not in source:
            raw_env = "hermetic"
        else:
            raw_env = source["GODVIEW_ENV"].strip().lower()
            if raw_env not in ("hermetic", "local", "gcp"):
                raise ValueError(
                    f"Invalid GODVIEW_ENV={source['GODVIEW_ENV']!r}; must be one of 'hermetic', 'local', 'gcp'"
                )
        max_agents = int(source.get("GODVIEW_COURT_MAX_AGENTS", str(DEFAULT_COURT_MAX_AGENTS)))
        if max_agents < 1 or max_agents > DEFAULT_COURT_MAX_AGENTS:
            raise ValueError(
                f"GODVIEW_COURT_MAX_AGENTS={max_agents} violates hard cap of {DEFAULT_COURT_MAX_AGENTS} (Decision A1)"
            )
        hint_repos = parse_csv_env(source.get("GODVIEW_HINT_REPO_URLS"))
        default_excl = parse_csv_env(DEFAULT_HINT_EXCLUDE)
        extra_excl = parse_csv_env(source.get("GODVIEW_HINT_EXCLUDE"))
        hint_excl = tuple(dict.fromkeys(default_excl + extra_excl))
        warm_max = int(source.get("GODVIEW_WARM_AGENTS_MAX", "2"))
        chat_enabled = source.get("GODVIEW_CHAT_ENABLED", "false").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        reserve = Decimal(
            source.get(
                "GODVIEW_ESCALATION_RESERVE_FRACTION",
                str(DEFAULT_ESCALATION_RESERVE_FRACTION),
            )
        )
        required_budget_keys = tuple(DEFAULT_AGENT_DAILY_BUDGETS_USD.keys())
        if raw_env in ("gcp", "local"):
            missing = [
                f"GODVIEW_BUDGET_{k.upper()}_USD"
                for k in required_budget_keys
                if f"GODVIEW_BUDGET_{k.upper()}_USD" not in source
            ]
            ceiling_raw = source.get("GODVIEW_COURT_DAILY_CEILING_USD")
            if ceiling_raw is None:
                missing.append("GODVIEW_COURT_DAILY_CEILING_USD")
            if missing:
                raise ValueError(
                    f"Token budget environment variables required in '{raw_env}' mode (Decision A9): missing {missing}"
                )
            court_ceiling = Decimal(str(ceiling_raw))
            budgets = {
                k: Decimal(source[f"GODVIEW_BUDGET_{k.upper()}_USD"]) for k in required_budget_keys
            }
        else:
            court_ceiling = Decimal(
                source.get(
                    "GODVIEW_COURT_DAILY_CEILING_USD",
                    str(DEFAULT_COURT_DAILY_CEILING_USD),
                )
            )
            budgets = dict(DEFAULT_AGENT_DAILY_BUDGETS_USD)
            for key in required_budget_keys:
                env_key = f"GODVIEW_BUDGET_{key.upper()}_USD"
                if env_key in source:
                    budgets[key] = Decimal(source[env_key])

        if court_ceiling <= Decimal("0") or any(v <= Decimal("0") for v in budgets.values()):
            raise ValueError("All daily token budgets and ceiling must be strictly positive (> 0)")

        return cls(
            env_mode=raw_env,  # type: ignore[arg-type]
            court_max_agents=max_agents,
            warm_agents_max=warm_max,
            hint_repo_urls=hint_repos,
            hint_exclude=hint_excl,
            chat_enabled=chat_enabled,
            court_daily_ceiling_usd=court_ceiling,
            escalation_reserve_fraction=reserve,
            agent_daily_budgets_usd=budgets,
        )
