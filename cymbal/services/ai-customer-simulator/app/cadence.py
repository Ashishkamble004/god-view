"""Cadence maths, business-window checks, jitter, and exact 1-in-10 KYC mode selection.

Implements Coordinator Decision A16, BR-01, and TDD §6.2 #9:
- Business window: 09:00–17:00 IST (Asia/Kolkata), 8 hours = 480 one-minute Scheduler ticks.
- Target daily journeys: 300 journeys/day (~37.5/hour, mean interval 96.0s ≈ 100s).
- Per-tick launch probability: 300 / 480 = 0.625 (5/8).
- Exact 1-in-10 IMAGE selection: every 10-journey window has 1 IMAGE and 9 TEXT_ONLY.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import KycMode

IST = ZoneInfo("Asia/Kolkata")

WINDOW_START_HOUR_IST: int = 9
WINDOW_END_HOUR_IST: int = 17
WINDOW_HOURS: int = WINDOW_END_HOUR_IST - WINDOW_START_HOUR_IST  # 8 hours
WINDOW_MINUTES: int = WINDOW_HOURS * 60  # 480 minutes / scheduler ticks per day

TARGET_JOURNEYS_PER_DAY: int = 300
TICK_LAUNCH_PROBABILITY: float = TARGET_JOURNEYS_PER_DAY / WINDOW_MINUTES  # 0.625 (300/480)
EXPECTED_JOURNEYS_PER_HOUR: float = TARGET_JOURNEYS_PER_DAY / WINDOW_HOURS  # 37.5
MEAN_INTERVAL_SECONDS: float = (WINDOW_MINUTES * 60.0) / TARGET_JOURNEYS_PER_DAY  # 96.0s
MAX_TICK_JITTER_SECONDS: float = 15.0
IMAGE_SELECTION_MODULO: int = 10


def now_ist(now: datetime | None = None) -> datetime:
    """Return timezone-aware datetime in Asia/Kolkata (IST)."""
    if now is None:
        return datetime.now(tz=IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def is_within_business_window(
    now: datetime | None = None,
    *,
    weekdays_only: bool = False,
) -> bool:
    """Return True iff `now` falls inside 09:00–17:00 IST [09:00, 17:00)."""
    current = now_ist(now)
    if weekdays_only and current.weekday() >= 5:
        return False
    return WINDOW_START_HOUR_IST <= current.hour < WINDOW_END_HOUR_IST


def should_launch_on_tick(
    random_sample: float | None = None,
    *,
    probability: float = TICK_LAUNCH_PROBABILITY,
    rng: random.Random | None = None,
) -> bool:
    """Return True with probability `300/480 = 0.625` per one-minute scheduler tick."""
    if not (0.0 <= probability <= 1.0):
        raise ValueError(f"probability must be in [0, 1], got {probability}")
    if random_sample is not None:
        if not (0.0 <= random_sample <= 1.0):
            raise ValueError(f"random_sample must be in [0, 1], got {random_sample}")
        sample = random_sample
    elif rng is not None:
        sample = rng.random()
    else:
        sample = random.random()
    return sample < probability


def compute_jitter_seconds(
    *,
    seed: int | str | None = None,
    max_jitter_s: float = MAX_TICK_JITTER_SECONDS,
) -> float:
    """Compute bounded jitter in [0.0, max_jitter_s] seconds to desynchronize tick arrivals."""
    if max_jitter_s <= 0.0:
        return 0.0
    if seed is not None:
        digest = hashlib.sha256(str(seed).encode("utf-8")).digest()
        unit = int.from_bytes(digest[:8], "big") / float(1 << 64)
        return round(unit * max_jitter_s, 4)
    return round(random.uniform(0.0, max_jitter_s), 4)


def select_kyc_mode(sequence_number: int) -> KycMode:
    """Select KycMode with exact 1-in-10 IMAGE ratio.

    Using `sequence_number % 10 == 1` guarantees:
    - Exactly 1 IMAGE and 9 TEXT_ONLY in every contiguous 10-item block for both
      0-indexed [0..999] and 1-indexed [1..1000] sequences (100 IMAGE / 900 TEXT_ONLY).
    - The first journey (sequence_number == 1) exercises the IMAGE path immediately.
    """
    if sequence_number < 0:
        raise ValueError(f"sequence_number must be non-negative, got {sequence_number}")
    if sequence_number % IMAGE_SELECTION_MODULO == 1:
        return KycMode.IMAGE
    return KycMode.TEXT_ONLY
