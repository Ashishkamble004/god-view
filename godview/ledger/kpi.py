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
    "approval_rate",
    "step_conversion",
    "ticket_size_inr",
    "rupees_per_hour",
    "emi_mean",
    "emi_stddev",
    "disbursement_latency_ms",
    "policy_issue_rate",
)

# Canonical BigQuery SQL expressions computing (metric_value, sample_n) per window
KPI_SQL_TEMPLATES: dict[KpiName, str] = {
    "approval_rate": """
WITH current_win AS (
  SELECT
    SAFE_DIVIDE(COUNTIF(decision = 'APPROVED'), COUNT(*)) AS val,
    COUNT(*) AS sample_n
  FROM `{dataset}.events`
  WHERE event_type = 'loan.decided'
    AND COALESCE(source, 'otel') != 'probe'
    AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 300 SECOND)
),
baseline_hourly AS (
  SELECT
    DATE(event_time) AS d,
    SAFE_DIVIDE(COUNTIF(decision = 'APPROVED'), COUNT(*)) AS day_val
  FROM `{dataset}.events`
  WHERE event_type = 'loan.decided'
    AND COALESCE(source, 'otel') != 'probe'
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
  'approval_rate' AS kpi,
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
      COUNT(DISTINCT IF(event_type = 'disbursement.settled', journey_id, NULL)),
      COUNT(DISTINCT IF(event_type = 'kyc.verified', journey_id, NULL))
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
      COUNT(DISTINCT IF(event_type = 'disbursement.settled', journey_id, NULL)),
      COUNT(DISTINCT IF(event_type = 'kyc.verified', journey_id, NULL))
    ) AS day_val
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
    "ticket_size_inr": """
WITH current_win AS (
  SELECT
    AVG(CAST(amount_inr AS FLOAT64)) AS val,
    COUNT(*) AS sample_n
  FROM `{dataset}.events`
  WHERE event_type = 'loan.decided'
    AND amount_inr IS NOT NULL
    AND COALESCE(source, 'otel') != 'probe'
    AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 300 SECOND)
),
baseline_hourly AS (
  SELECT
    DATE(event_time) AS d,
    AVG(CAST(amount_inr AS FLOAT64)) AS day_val
  FROM `{dataset}.events`
  WHERE event_type = 'loan.decided'
    AND amount_inr IS NOT NULL
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
  'ticket_size_inr' AS kpi,
  COALESCE(c.val, 0.0) AS value,
  COALESCE(b.mean_val, 0.0) AS baseline_value,
  SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0)) AS zscore,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0))) AS cusum,
  c.sample_n AS sample_n,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0))) >= 3.0 AS breach
FROM current_win AS c
CROSS JOIN baseline_stats AS b
""",
    "rupees_per_hour": """
WITH current_win AS (
  SELECT
    SUM(CAST(amount_inr AS FLOAT64)) * 12.0 AS val,
    COUNT(*) AS sample_n
  FROM `{dataset}.events`
  WHERE event_type = 'disbursement.settled'
    AND amount_inr IS NOT NULL
    AND COALESCE(source, 'otel') != 'probe'
    AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 300 SECOND)
),
baseline_hourly AS (
  SELECT
    DATE(event_time) AS d,
    SUM(CAST(amount_inr AS FLOAT64)) AS day_val
  FROM `{dataset}.events`
  WHERE event_type = 'disbursement.settled'
    AND amount_inr IS NOT NULL
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
  'rupees_per_hour' AS kpi,
  COALESCE(c.val, 0.0) AS value,
  COALESCE(b.mean_val, 0.0) AS baseline_value,
  SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0)) AS zscore,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0))) AS cusum,
  c.sample_n AS sample_n,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0))) >= 3.0 AS breach
FROM current_win AS c
CROSS JOIN baseline_stats AS b
""",
    "emi_mean": """
WITH current_win AS (
  SELECT
    AVG(CAST(emi_inr AS FLOAT64)) AS val,
    COUNT(*) AS sample_n
  FROM `{dataset}.events`
  WHERE event_type = 'loan.decided'
    AND emi_inr IS NOT NULL
    AND COALESCE(source, 'otel') != 'probe'
    AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 300 SECOND)
),
baseline_hourly AS (
  SELECT
    DATE(event_time) AS d,
    AVG(CAST(emi_inr AS FLOAT64)) AS day_val
  FROM `{dataset}.events`
  WHERE event_type = 'loan.decided'
    AND emi_inr IS NOT NULL
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
  'emi_mean' AS kpi,
  COALESCE(c.val, 0.0) AS value,
  COALESCE(b.mean_val, 0.0) AS baseline_value,
  SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0)) AS zscore,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0))) AS cusum,
  c.sample_n AS sample_n,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0))) >= 3.0 AS breach
FROM current_win AS c
CROSS JOIN baseline_stats AS b
""",
    "emi_stddev": """
WITH current_win AS (
  SELECT
    STDDEV_POP(CAST(emi_inr AS FLOAT64)) AS val,
    COUNT(*) AS sample_n
  FROM `{dataset}.events`
  WHERE event_type = 'loan.decided'
    AND emi_inr IS NOT NULL
    AND COALESCE(source, 'otel') != 'probe'
    AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 300 SECOND)
),
baseline_hourly AS (
  SELECT
    DATE(event_time) AS d,
    STDDEV_POP(CAST(emi_inr AS FLOAT64)) AS day_val
  FROM `{dataset}.events`
  WHERE event_type = 'loan.decided'
    AND emi_inr IS NOT NULL
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
  'emi_stddev' AS kpi,
  COALESCE(c.val, 0.0) AS value,
  COALESCE(b.mean_val, 0.0) AS baseline_value,
  SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0)) AS zscore,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0))) AS cusum,
  c.sample_n AS sample_n,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0))) >= 3.0 AS breach
FROM current_win AS c
CROSS JOIN baseline_stats AS b
""",
    "disbursement_latency_ms": """
WITH current_win AS (
  SELECT
    AVG(CAST(duration_ms AS FLOAT64)) AS val,
    COUNT(*) AS sample_n
  FROM `{dataset}.events`
  WHERE event_type = 'disbursement.settled'
    AND duration_ms IS NOT NULL
    AND COALESCE(source, 'otel') != 'probe'
    AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 300 SECOND)
),
baseline_hourly AS (
  SELECT
    DATE(event_time) AS d,
    AVG(CAST(duration_ms AS FLOAT64)) AS day_val
  FROM `{dataset}.events`
  WHERE event_type = 'disbursement.settled'
    AND duration_ms IS NOT NULL
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
  'disbursement_latency_ms' AS kpi,
  COALESCE(c.val, 0.0) AS value,
  COALESCE(b.mean_val, 0.0) AS baseline_value,
  SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0)) AS zscore,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0))) AS cusum,
  c.sample_n AS sample_n,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 1.0), 1.0))) >= 3.0 AS breach
FROM current_win AS c
CROSS JOIN baseline_stats AS b
""",
    "policy_issue_rate": """
WITH current_win AS (
  SELECT
    SAFE_DIVIDE(
      COUNT(DISTINCT IF(event_type = 'insurance.issued', journey_id, NULL)),
      COUNT(DISTINCT IF(event_type = 'loan.decided' AND decision = 'APPROVED', journey_id, NULL))
    ) AS val,
    COUNT(DISTINCT IF(event_type = 'loan.decided' AND decision = 'APPROVED', journey_id, NULL)) AS sample_n
  FROM `{dataset}.events`
  WHERE COALESCE(source, 'otel') != 'probe'
    AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 300 SECOND)
),
baseline_hourly AS (
  SELECT
    DATE(event_time) AS d,
    SAFE_DIVIDE(
      COUNT(DISTINCT IF(event_type = 'insurance.issued', journey_id, NULL)),
      COUNT(DISTINCT IF(event_type = 'loan.decided' AND decision = 'APPROVED', journey_id, NULL))
    ) AS day_val
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
  'policy_issue_rate' AS kpi,
  COALESCE(c.val, 0.0) AS value,
  COALESCE(b.mean_val, 0.0) AS baseline_value,
  SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 0.01), 0.01)) AS zscore,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 0.01), 0.01))) AS cusum,
  c.sample_n AS sample_n,
  ABS(SAFE_DIVIDE(c.val - b.mean_val, GREATEST(COALESCE(b.std_val, 0.01), 0.01))) >= 3.0 AS breach
FROM current_win AS c
CROSS JOIN baseline_stats AS b
""",
}


def build_kpi_rollup_sql(
    kpi: KpiName,
    *,
    dataset: str = "godview_ledger",
    baseline_days: int = DEFAULT_SENTINEL_BASELINE_WINDOW_DAYS,
) -> str:
    """Return the BigQuery Standard SQL query for a given KPI with same-hour baseline."""
    if kpi not in KPI_SQL_TEMPLATES:
        raise ValueError(f"Unknown KPI '{kpi}' (allowed: {ALL_KPIS})")
    return KPI_SQL_TEMPLATES[kpi].strip().format(dataset=dataset, baseline_days=baseline_days)


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
    kpi: KpiName,
    events: Sequence[dict[str, Any]],
    *,
    window_hours: float = 1.0,
) -> tuple[float, int]:
    """Compute a single KPI's observed value and sample_n from a slice of non-probe events."""
    decided = [e for e in events if e.get("event_type") == "loan.decided"]
    approved = [e for e in decided if e.get("decision") == "APPROVED"]
    disbursed = [e for e in events if e.get("event_type") == "disbursement.settled"]
    insured = [e for e in events if e.get("event_type") == "insurance.issued"]
    verified_events = [e for e in events if e.get("event_type") == "kyc.verified"]

    if kpi == "approval_rate":
        n = len(decided)
        return (len(approved) / n if n > 0 else 0.0), n

    if kpi == "step_conversion":
        verified_j = {e.get("journey_id") for e in verified_events if e.get("journey_id")}
        disb_j = {e.get("journey_id") for e in disbursed if e.get("journey_id")}
        n = len(verified_j)
        return (len(disb_j) / n if n > 0 else 0.0), n

    if kpi == "ticket_size_inr":
        amounts = [float(e["amount_inr"]) for e in decided if e.get("amount_inr") is not None]
        n = len(amounts)
        return (sum(amounts) / n if n > 0 else 0.0), n

    if kpi == "rupees_per_hour":
        amounts = [float(e["amount_inr"]) for e in disbursed if e.get("amount_inr") is not None]
        n = len(amounts)
        scale = 1.0 / max(window_hours, 1e-6)
        return (sum(amounts) * scale if n > 0 else 0.0), n

    if kpi == "emi_mean":
        emis = [float(e["emi_inr"]) for e in decided if e.get("emi_inr") is not None]
        n = len(emis)
        return (sum(emis) / n if n > 0 else 0.0), n

    if kpi == "emi_stddev":
        emis = [float(e["emi_inr"]) for e in decided if e.get("emi_inr") is not None]
        n = len(emis)
        if n < 2:
            return 0.0, n
        mean_e = sum(emis) / n
        std_e = math.sqrt(sum((x - mean_e) ** 2 for x in emis) / n)
        return std_e, n

    if kpi == "disbursement_latency_ms":
        lats = [float(e["duration_ms"]) for e in disbursed if e.get("duration_ms") is not None]
        n = len(lats)
        return (sum(lats) / n if n > 0 else 0.0), n

    if kpi == "policy_issue_rate":
        app_j = {e.get("journey_id") for e in approved if e.get("journey_id")}
        ins_j = {e.get("journey_id") for e in insured if e.get("journey_id")}
        n = len(app_j)
        return (len(ins_j & app_j) / n if n > 0 else 1.0), n

    raise ValueError(f"Unsupported KPI '{kpi}'")


class KpiEngine:
    """Evaluates all 8 business KPIs against 7-day same-hour-of-day baselines with CUSUM and z-score."""

    def __init__(
        self,
        bigquery: InMemoryBigQuery,
        *,
        env_id: str = "local-compose",
        journey_name: str = "vehicle_loan_origination",
        baseline_days: int = DEFAULT_SENTINEL_BASELINE_WINDOW_DAYS,
        z_threshold: float = 3.0,
        cusum_threshold: float = 4.0,
        cusum_slack_k: float = 0.5,
    ) -> None:
        self._bigquery = bigquery
        self._env_id = env_id
        self._journey_name = journey_name
        self._baseline_days = baseline_days
        self._z_threshold = z_threshold
        self._cusum_threshold = cusum_threshold
        self._cusum_slack_k = cusum_slack_k
        self._cusum_states: dict[KpiName, CusumState] = defaultdict(CusumState)

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

        for kpi in ALL_KPIS:
            obs_val, sample_n = _compute_kpi_from_events(kpi, win_events, window_hours=win_hours)
            day_vals = [
                _compute_kpi_from_events(kpi, day_events, window_hours=1.0)[0]
                for day_events in hist_by_day.values()
                if day_events
            ]
            # Minimum floor scales with metric magnitude so zero-variance baselines (e.g. 100% policy_issue_rate) stay well-conditioned
            min_std = (
                0.02
                if kpi in ("approval_rate", "step_conversion", "policy_issue_rate")
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

        self._bigquery.insert_rows("kpi_rollups", [r.model_dump() for r in rollups])
        return rollups
