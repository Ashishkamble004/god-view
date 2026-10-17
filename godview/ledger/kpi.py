"""KPI SQL, 7-day same-hour-of-day baselines, and CUSUM / z-score change-point detection (TDD §2.2, §4 UC-06/UC-07, Decision A8)."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from godview_shared.config import DEFAULT_SENTINEL_BASELINE_WINDOW_DAYS
from godview_shared.contracts import KpiName, KpiRollupRow
from godview_shared.fakes import InMemoryBigQuery

ALL_KPIS: tuple[KpiName, ...] = (
    "completion_rate",
    "step_conversion",
    "throughput_per_hour",
    "latency_mean_ms",
    "latency_stddev_ms",
)

KPI_SQL_TEMPLATES: dict[str, str] = {
    "completion_rate": """
WITH current_win AS (
  SELECT
    SAFE_DIVIDE(COUNTIF(state_to IN {success_states}), COUNT(*)) AS val,
    COUNT(*) AS sample_n
  FROM `{dataset}.events`
  WHERE COALESCE(source, 'otel') != 'probe'
    AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 300 SECOND)
),
baseline_hourly AS (
  SELECT
    DATE(event_time) AS d,
    SAFE_DIVIDE(COUNTIF(state_to IN {success_states}), COUNT(*)) AS day_val
  FROM `{dataset}.events`
  WHERE COALESCE(source, 'otel') != 'probe'
    AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {baseline_days} DAY)
    AND event_time < TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), HOUR)
    AND EXTRACT(HOUR FROM event_time) = EXTRACT(HOUR FROM CURRENT_TIMESTAMP())
  GROUP BY d
),
baseline_stats AS (
  SELECT
    AVG(day_val) AS mean_val,
    STDDEV_POP(day_val) AS std_val
  FROM baseline_hourly
)
SELECT
  'completion_rate' AS kpi,
  COALESCE(c.val, 0.0) AS value,
  COALESCE(b.mean_val, 0.0) AS baseline_value,
  SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 0.01), 0.01)) AS zscore,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 0.01), 0.01))) AS cusum,
  c.sample_n AS sample_n,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 0.01), 0.01))) >= 3.0 AS breach
FROM current_win AS c
CROSS JOIN baseline_stats AS b
""",
    "step_conversion": """
WITH current_win AS (
  SELECT
    SAFE_DIVIDE(
      COUNT(DISTINCT IF(state_to IN {success_states}, journey_id, NULL)),
      COUNT(DISTINCT journey_id)
    ) AS val,
    COUNT(DISTINCT journey_id) AS sample_n
  FROM `{dataset}.events`
  WHERE COALESCE(source, 'otel') != 'probe'
    AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 300 SECOND)
),
baseline_hourly AS (
  SELECT
    DATE(event_time) AS d,
    SAFE_DIVIDE(
      COUNT(DISTINCT IF(state_to IN {success_states}, journey_id, NULL)),
      COUNT(DISTINCT journey_id)
    ) AS day_val
  FROM `{dataset}.events`
  WHERE COALESCE(source, 'otel') != 'probe'
    AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {baseline_days} DAY)
    AND event_time < TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), HOUR)
    AND EXTRACT(HOUR FROM event_time) = EXTRACT(HOUR FROM CURRENT_TIMESTAMP())
  GROUP BY d
),
baseline_stats AS (
  SELECT
    AVG(day_val) AS mean_val,
    STDDEV_POP(day_val) AS std_val
  FROM baseline_hourly
)
SELECT
  'step_conversion' AS kpi,
  COALESCE(c.val, 0.0) AS value,
  COALESCE(b.mean_val, 0.0) AS baseline_value,
  SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 0.01), 0.01)) AS zscore,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 0.01), 0.01))) AS cusum,
  c.sample_n AS sample_n,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 0.01), 0.01))) >= 3.0 AS breach
FROM current_win AS c
CROSS JOIN baseline_stats AS b
""",
    "latency_mean_ms": """
WITH current_win AS (
  SELECT
    AVG(CAST(duration_ms AS FLOAT64)) AS val,
    COUNT(*) AS sample_n
  FROM `{dataset}.events`
  WHERE duration_ms IS NOT NULL
    AND COALESCE(source, 'otel') != 'probe'
    AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 300 SECOND)
),
baseline_hourly AS (
  SELECT
    DATE(event_time) AS d,
    AVG(CAST(duration_ms AS FLOAT64)) AS day_val
  FROM `{dataset}.events`
  WHERE duration_ms IS NOT NULL
    AND COALESCE(source, 'otel') != 'probe'
    AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {baseline_days} DAY)
    AND event_time < TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), HOUR)
    AND EXTRACT(HOUR FROM event_time) = EXTRACT(HOUR FROM CURRENT_TIMESTAMP())
  GROUP BY d
),
baseline_stats AS (
  SELECT AVG(day_val) AS mean_val, STDDEV_POP(day_val) AS std_val FROM baseline_hourly
)
SELECT
  'latency_mean_ms' AS kpi,
  COALESCE(c.val, 0.0) AS value,
  COALESCE(b.mean_val, 0.0) AS baseline_value,
  SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0)) AS zscore,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0))) AS cusum,
  c.sample_n AS sample_n,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0))) >= 3.0 AS breach
FROM current_win AS c
CROSS JOIN baseline_stats AS b
""",
    "latency_stddev_ms": """
WITH current_win AS (
  SELECT
    STDDEV_POP(CAST(duration_ms AS FLOAT64)) AS val,
    COUNT(*) AS sample_n
  FROM `{dataset}.events`
  WHERE duration_ms IS NOT NULL
    AND COALESCE(source, 'otel') != 'probe'
    AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 300 SECOND)
),
baseline_hourly AS (
  SELECT
    DATE(event_time) AS d,
    STDDEV_POP(CAST(duration_ms AS FLOAT64)) AS day_val
  FROM `{dataset}.events`
  WHERE duration_ms IS NOT NULL
    AND COALESCE(source, 'otel') != 'probe'
    AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {baseline_days} DAY)
    AND event_time < TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), HOUR)
    AND EXTRACT(HOUR FROM event_time) = EXTRACT(HOUR FROM CURRENT_TIMESTAMP())
  GROUP BY d
),
baseline_stats AS (
  SELECT AVG(day_val) AS mean_val, STDDEV_POP(day_val) AS std_val FROM baseline_hourly
)
SELECT
  'latency_stddev_ms' AS kpi,
  COALESCE(c.val, 0.0) AS value,
  COALESCE(b.mean_val, 0.0) AS baseline_value,
  SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0)) AS zscore,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0))) AS cusum,
  c.sample_n AS sample_n,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0))) >= 3.0 AS breach
FROM current_win AS c
CROSS JOIN baseline_stats AS b
""",
    "throughput_per_hour": """
WITH current_win AS (
  SELECT
    COUNT(*) * 12.0 AS val,
    COUNT(*) AS sample_n
  FROM `{dataset}.events`
  WHERE COALESCE(source, 'otel') != 'probe'
    AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 300 SECOND)
),
baseline_hourly AS (
  SELECT
    DATE(event_time) AS d,
    COUNT(*) AS day_val
  FROM `{dataset}.events`
  WHERE COALESCE(source, 'otel') != 'probe'
    AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {baseline_days} DAY)
    AND event_time < TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), HOUR)
    AND EXTRACT(HOUR FROM event_time) = EXTRACT(HOUR FROM CURRENT_TIMESTAMP())
  GROUP BY d
),
baseline_stats AS (
  SELECT AVG(day_val) AS mean_val, STDDEV_POP(day_val) AS std_val FROM baseline_hourly
)
SELECT
  'throughput_per_hour' AS kpi,
  COALESCE(c.val, 0.0) AS value,
  COALESCE(b.mean_val, 0.0) AS baseline_value,
  SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0)) AS zscore,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0))) AS cusum,
  c.sample_n AS sample_n,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0))) >= 3.0 AS breach
FROM current_win AS c
CROSS JOIN baseline_stats AS b
""",
}


def build_kpi_rollup_sql(
    kpi: str,
    *,
    dataset: str = "godview_ledger",
    baseline_days: int = DEFAULT_SENTINEL_BASELINE_WINDOW_DAYS,
    custom_template: str | None = None,
    success_states: Sequence[str] | None = None,
) -> str:
    """Return the BigQuery Standard SQL query for a given KPI with same-hour baseline."""
    states_tuple = tuple(str(s).upper() for s in (success_states or ("COMPLETED", "RESOLVED", "SUCCESS", "OK")))
    states_clause = (
        f"({', '.join(repr(s) for s in states_tuple)})"
        if len(states_tuple) > 1
        else f"('{states_tuple[0]}')" if states_tuple else "('COMPLETED')"
    )
    if custom_template:
        return custom_template.strip().format(
            dataset=dataset, baseline_days=baseline_days, kpi=kpi, success_states=states_clause
        )
    if kpi in KPI_SQL_TEMPLATES:
        return KPI_SQL_TEMPLATES[kpi].strip().format(
            dataset=dataset, baseline_days=baseline_days, success_states=states_clause
        )
    # Generic fallback SQL template
    return f"""
WITH current_win AS (
  SELECT
    COUNT(*) AS val,
    COUNT(*) AS sample_n
  FROM `{dataset}.events`
  WHERE COALESCE(source, 'otel') != 'probe'
    AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 300 SECOND)
),
baseline_hourly AS (
  SELECT
    DATE(event_time) AS d,
    COUNT(*) AS day_val
  FROM `{dataset}.events`
  WHERE COALESCE(source, 'otel') != 'probe'
    AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {baseline_days} DAY)
    AND event_time < TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), HOUR)
    AND EXTRACT(HOUR FROM event_time) = EXTRACT(HOUR FROM CURRENT_TIMESTAMP())
  GROUP BY d
),
baseline_stats AS (
  SELECT AVG(day_val) AS mean_val, STDDEV_POP(day_val) AS std_val FROM baseline_hourly
)
SELECT
  '{kpi}' AS kpi,
  COALESCE(c.val, 0.0) AS value,
  COALESCE(b.mean_val, 0.0) AS baseline_value,
  SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0)) AS zscore,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0))) AS cusum,
  c.sample_n AS sample_n,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0))) >= 3.0 AS breach
FROM current_win AS c
CROSS JOIN baseline_stats AS b
""".strip()


@dataclass
class CusumState:
    """Two-sided tabular CUSUM accumulator for a single KPI."""

    s_high: float = 0.0
    s_low: float = 0.0

    def update(self, zscore: float, *, k: float = 0.5) -> float:
        self.s_high = max(0.0, self.s_high + (zscore - k))
        self.s_low = max(0.0, self.s_low + (-zscore - k))
        return max(self.s_high, self.s_low)


def _mean_and_stddev(values: Sequence[float], *, min_floor: float = 1e-4) -> tuple[float, float]:
    if not values:
        return 0.0, min_floor
    mean_v = sum(values) / len(values)
    if len(values) < 2:
        return mean_v, max(abs(mean_v) * 0.05, min_floor)
    var = sum((v - mean_v) ** 2 for v in values) / len(values)
    std_v = math.sqrt(var)
    return mean_v, max(std_v, min_floor)


def _compute_kpi_from_events(
    kpi: str,
    events: Sequence[dict[str, Any]],
    *,
    window_hours: float = 1.0,
    success_states: Sequence[str] | None = None,
) -> tuple[float, int]:
    """Compute a single KPI's observed value and sample_n from a slice of non-probe events."""
    if not events:
        return 0.0, 0
    kpi_name = kpi.lower() if isinstance(kpi, str) else str(kpi)
    n = len(events)
    valid_success = set(
        str(s).upper() for s in (success_states or ("COMPLETED", "RESOLVED", "SUCCESS", "OK"))
    )

    if kpi_name == "completion_rate" or kpi_name.endswith("_rate"):
        completed = [
            e
            for e in events
            if str(e.get("state_to") or e.get("status_to") or "").upper() in valid_success
        ]
        return len(completed) / n, n

    if kpi_name == "step_conversion":
        total_j = {e.get("journey_id") for e in events if e.get("journey_id")}
        completed_j = {
            e.get("journey_id")
            for e in events
            if e.get("journey_id")
            and str(e.get("state_to") or e.get("status_to") or "").upper() in valid_success
        }
        return (len(completed_j) / len(total_j)) if total_j else 0.0, len(total_j)

    if kpi_name.endswith("_mean") or kpi_name.endswith("_ms") or kpi_name in ("latency_mean_ms",):
        durations = [float(e["duration_ms"]) for e in events if e.get("duration_ms") is not None]
        return (sum(durations) / len(durations) if durations else 0.0), len(durations)

    if kpi_name.endswith("_stddev") or kpi_name in ("latency_stddev_ms",):
        durations = [float(e["duration_ms"]) for e in events if e.get("duration_ms") is not None]
        if len(durations) < 2:
            return 0.0, len(durations)
        mean_d = sum(durations) / len(durations)
        std_d = math.sqrt(sum((x - mean_d) ** 2 for x in durations) / len(durations))
        return std_d, len(durations)

    if kpi_name.endswith("_per_hour") or kpi_name == "throughput_per_hour":
        scale = 1.0 / max(window_hours, 1e-6)
        return float(n) * scale, n

    # Check numeric_attributes
    matching = [
        float(e.get("numeric_attributes", {}).get(kpi_name))
        for e in events
        if isinstance(e.get("numeric_attributes"), dict) and kpi_name in e["numeric_attributes"]
    ]
    if matching:
        return sum(matching) / len(matching), len(matching)

    return float(n), n


class KpiEngine:
    """Evaluates business KPIs against 7-day same-hour-of-day baselines with CUSUM and z-score."""

    def __init__(
        self,
        bigquery: InMemoryBigQuery,
        *,
        env_id: str = "local-compose",
        journey_name: str = "default_journey",
        kpis: Sequence[str] | None = None,
        baseline_days: int = DEFAULT_SENTINEL_BASELINE_WINDOW_DAYS,
        z_threshold: float = 3.0,
        cusum_threshold: float = 4.0,
        cusum_slack_k: float = 0.5,
    ) -> None:
        self._bigquery = bigquery
        self._env_id = env_id
        self._journey_name = journey_name
        self._kpis: tuple[str, ...] = tuple(kpis) if kpis is not None else tuple(ALL_KPIS)
        self._baseline_days = baseline_days
        self._z_threshold = z_threshold
        self._cusum_threshold = cusum_threshold
        self._cusum_slack_k = cusum_slack_k
        self._cusum_states: dict[str, CusumState] = defaultdict(CusumState)

    def evaluate_window(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        segment: str = "all",
    ) -> list[KpiRollupRow]:
        """Compute KPI rollups for [window_start, window_end) against the trailing 7d same-hour baseline."""
        all_events = [
            e
            for e in self._bigquery.get_table("events")
            if str(e.get("source") or "otel").lower() != "probe"
            and (not e.get("env_id") or e.get("env_id") == self._env_id)
        ]
        target_hour = window_start.hour
        current_hour_start = window_start.replace(minute=0, second=0, microsecond=0)
        baseline_cutoff = window_start - timedelta(days=self._baseline_days)

        win_events: list[dict[str, Any]] = []
        hist_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for ev in all_events:
            ev_time = ev.get("event_time")
            if isinstance(ev_time, str):
                ev_time = datetime.fromisoformat(ev_time)
            if ev_time is None:
                continue
            if ev_time.tzinfo is None:
                ev_time = ev_time.replace(tzinfo=UTC)

            if window_start <= ev_time < window_end:
                win_events.append(ev)
            elif baseline_cutoff <= ev_time < current_hour_start and ev_time.hour == target_hour:
                day_key = ev_time.date().isoformat()
                hist_by_day[day_key].append(ev)

        win_hours = max((window_end - window_start).total_seconds() / 3600.0, 1.0 / 60.0)
        rollups: list[KpiRollupRow] = []

        for kpi in self._kpis:
            obs_val, sample_n = _compute_kpi_from_events(kpi, win_events, window_hours=win_hours)
            day_vals = [
                _compute_kpi_from_events(kpi, day_events, window_hours=1.0)[0]
                for day_events in hist_by_day.values()
                if day_events
            ]
            min_std = (
                0.02
                if kpi.endswith("_rate") or kpi in ("completion_rate", "approval_rate", "step_conversion")
                else max(abs(obs_val) * 0.02, 1.0)
            )
            base_mean, base_std = _mean_and_stddev(day_vals, min_floor=min_std)

            if day_vals and sample_n > 0:
                zscore = (obs_val - base_mean) / base_std
            else:
                zscore = 0.0

            cusum_val = self._cusum_states[kpi].update(zscore, k=self._cusum_slack_k)
            is_breach = bool(
                sample_n > 0
                and day_vals
                and (abs(zscore) >= self._z_threshold or cusum_val >= self._cusum_threshold)
            )

            row = KpiRollupRow(
                window_start=window_start,
                window_end=window_end,
                env_id=self._env_id,
                journey_name=self._journey_name,
                kpi=kpi,
                segment=segment,
                value=Decimal(f"{obs_val:.4f}"),
                baseline_value=Decimal(f"{base_mean:.4f}"),
                zscore=Decimal(f"{zscore:.4f}"),
                cusum=Decimal(f"{cusum_val:.4f}"),
                sample_n=sample_n,
                breach=is_breach,
            )
            rollups.append(row)

        return rollups
