"""Process mining over godview_ledger: Directly-Follows Graph (DFG) & Conformance Classification (TDD §2.2, §4 UC-05, UC-08, UC-16)."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from godview_shared.contracts import (
    JourneyConformance,
    LedgerJourneyRow,
    StateTransitionRow,
)

DEFAULT_INTENDED_ORDER: tuple[str, ...] = ()
EXTENDED_INTENDED_ORDER: tuple[str, ...] = ()

TERMINAL_JOURNEY_STATUSES: frozenset[str] = frozenset(
    {
        "COMPLETED",
        "RESOLVED",
        "SUCCESS",
        "FAILED",
        "CANCELLED",
        "REJECTED",
    }
)


def canonical_step_name(
    *,
    event_type: str | None = None,
    operation: str | None = None,
    service: str | None = None,
    status_to: str | None = None,
) -> str:
    """Map an event_type, operation, service, or status_to onto a canonical workflow step token."""
    raw = event_type or operation or status_to or service or "UNKNOWN"
    return str(raw).strip().upper()


def classify_journey_conformance(
    observed_steps: Sequence[str],
    *,
    final_status: str = "IN_PROGRESS",
    started_at: datetime | None = None,
    last_step_at: datetime | None = None,
    now: datetime | None = None,
    stall_threshold_ms: int = 30_000,
    max_step_dwell_ms: int = 0,
    intended_order: Sequence[str] = DEFAULT_INTENDED_ORDER,
) -> JourneyConformance:
    """Classify a journey sequence into CONFORMANT, SKIPPED, REORDERED, LOOPED, or STALLED.

    Rules (TDD §2.2 & §4):
    - LOOPED: Any step appears more than once in the observed sequence.
    - REORDERED: Observed steps violate the relative ordering of the intended sequence.
    - SKIPPED: A later step in the intended sequence executes while an earlier required step was omitted.
    - STALLED: An incomplete journey whose dwell time since the last step (or max step dwell)
      exceeds stall_threshold_ms (e.g. UC-16 gce_vm_latency_504).
    - CONFORMANT: Follows the intended order (complete or valid in-flight prefix within threshold).
    """
    steps = [s.strip() for s in observed_steps if s and s.strip()]
    if not steps:
        return "STALLED"

    # 1. LOOPED check: repeated step in sequence
    if len(set(steps)) < len(steps):
        return "LOOPED"

    # Resolve intended sequence
    effective_order = list(intended_order)

    order_index = {step: idx for idx, step in enumerate(effective_order)}
    known_indices = [order_index[s] for s in steps if s in order_index]

    if effective_order and not known_indices:
        return "SKIPPED"

    if known_indices:
        # 2. REORDERED check: known step indices must be strictly increasing even if extra steps exist
        for prev_idx, next_idx in zip(known_indices, known_indices[1:], strict=False):
            if next_idx < prev_idx:
                return "REORDERED"

        # 3. SKIPPED check: known step indices must form a contiguous prefix 0, 1, ..., k-1
        expected_prefix = list(range(len(known_indices)))
        if known_indices != expected_prefix:
            return "SKIPPED"

    # If marked terminal/complete, all required steps through the final step must have been executed
    if (
        final_status in ("COMPLETED", "RESOLVED", "SUCCESS")
        and effective_order
        and len(known_indices) < len(effective_order)
    ):
        return "SKIPPED"

    # 4. STALLED check for non-terminal journeys
    is_terminal = final_status in TERMINAL_JOURNEY_STATUSES or (
        bool(known_indices and effective_order and known_indices[-1] == len(effective_order) - 1)
    )
    if not is_terminal:
        if max_step_dwell_ms >= stall_threshold_ms:
            return "STALLED"
        ref_now = now if now is not None else datetime.now(tz=UTC)
        anchor = last_step_at or started_at
        if anchor is not None:
            elapsed_ms = (ref_now - anchor).total_seconds() * 1000.0
            if elapsed_ms >= stall_threshold_ms:
                return "STALLED"

    return "CONFORMANT"


@dataclass
class DirectlyFollowsGraph:
    """Mined Directly-Follows Graph (DFG) and conformance summary for a workflow."""

    intended_order: tuple[str, ...]
    edge_counts: dict[tuple[str, str], int] = field(default_factory=dict)
    transition_probabilities: dict[tuple[str, str], float] = field(default_factory=dict)
    directly_follows_map: dict[str, list[str]] = field(default_factory=dict)
    start_steps: dict[str, int] = field(default_factory=dict)
    end_steps: dict[str, int] = field(default_factory=dict)
    conformance_counts: dict[JourneyConformance, int] = field(default_factory=dict)
    total_journeys: int = 0
    mean_duration_ms: float = 0.0


class DirectlyFollowsMiner:
    """Process miner that extracts Directly-Follows Graphs and conformance metrics from ledger rows."""

    def __init__(
        self,
        intended_order: Sequence[str] = DEFAULT_INTENDED_ORDER,
        stall_threshold_ms: int = 30_000,
    ) -> None:
        self.intended_order = tuple(intended_order)
        self.stall_threshold_ms = stall_threshold_ms

    def mine_from_sequences(
        self,
        sequences: Sequence[tuple[str, Sequence[str], str, int]],
        *,
        now: datetime | None = None,
    ) -> DirectlyFollowsGraph:
        """Mine a DirectlyFollowsGraph from tuples of (journey_id, steps, final_status, duration_ms)."""
        edge_counter: Counter[tuple[str, str]] = Counter()
        outgoing_totals: Counter[str] = Counter()
        adj_set: dict[str, list[str]] = defaultdict(list)
        start_counter: Counter[str] = Counter()
        end_counter: Counter[str] = Counter()
        conformance_counter: Counter[JourneyConformance] = Counter(
            {
                "CONFORMANT": 0,
                "SKIPPED": 0,
                "REORDERED": 0,
                "LOOPED": 0,
                "STALLED": 0,
            }
        )
        durations: list[int] = []

        for _journey_id, steps_raw, final_status, duration_ms in sequences:
            steps = [s for s in steps_raw if s]
            if steps:
                start_counter[steps[0]] += 1
                end_counter[steps[-1]] += 1
                for src, dst in zip(steps, steps[1:], strict=False):
                    edge_counter[(src, dst)] += 1
                    outgoing_totals[src] += 1
                    if dst not in adj_set[src]:
                        adj_set[src].append(dst)

            verdict = classify_journey_conformance(
                steps,
                final_status=final_status,
                max_step_dwell_ms=duration_ms
                if final_status not in TERMINAL_JOURNEY_STATUSES
                else 0,
                now=now,
                stall_threshold_ms=self.stall_threshold_ms,
                intended_order=self.intended_order,
            )
            conformance_counter[verdict] += 1
            durations.append(int(duration_ms))

        probs: dict[tuple[str, str], float] = {}
        for (src, dst), count in edge_counter.items():
            total_out = outgoing_totals[src]
            probs[(src, dst)] = round(count / total_out, 4) if total_out > 0 else 0.0

        mean_dur = sum(durations) / len(durations) if durations else 0.0
        return DirectlyFollowsGraph(
            intended_order=self.intended_order,
            edge_counts=dict(edge_counter),
            transition_probabilities=probs,
            directly_follows_map={k: list(v) for k, v in adj_set.items()},
            start_steps=dict(start_counter),
            end_steps=dict(end_counter),
            conformance_counts=dict(conformance_counter),
            total_journeys=len(sequences),
            mean_duration_ms=round(mean_dur, 2),
        )

    def mine_from_journeys(
        self,
        journeys: Sequence[LedgerJourneyRow | Mapping[str, Any]],
        *,
        now: datetime | None = None,
    ) -> DirectlyFollowsGraph:
        """Mine a DirectlyFollowsGraph from LedgerJourneyRow objects or dicts."""
        parsed_sequences: list[tuple[str, Sequence[str], str, int]] = []
        for item in journeys:
            data = item.model_dump() if isinstance(item, LedgerJourneyRow) else dict(item)
            j_id = str(data.get("journey_id", ""))
            obs_seq = str(data.get("observed_sequence", ""))
            if obs_seq:
                steps = [tok.strip() for tok in obs_seq.split("->") if tok.strip()]
            else:
                raw_steps = data.get("steps") or []
                steps = [
                    canonical_step_name(
                        operation=s.get("operation"),
                        service=s.get("service"),
                        status_to=s.get("status"),
                    )
                    for s in raw_steps
                ]
            final_status = str(data.get("final_status", "IN_PROGRESS"))
            dur_ms = int(data.get("total_duration_ms") or 0)
            parsed_sequences.append((j_id, steps, final_status, dur_ms))
        return self.mine_from_sequences(parsed_sequences, now=now)

    def mine_from_transitions(
        self,
        transitions: Sequence[StateTransitionRow | Mapping[str, Any]],
        *,
        now: datetime | None = None,
    ) -> DirectlyFollowsGraph:
        """Group state_transitions by journey_id and mine the DirectlyFollowsGraph."""
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in transitions:
            data = item.model_dump() if isinstance(item, StateTransitionRow) else dict(item)
            j_id = str(data.get("journey_id") or "")
            if j_id:
                grouped[j_id].append(data)

        sequences: list[tuple[str, Sequence[str], str, int]] = []
        for j_id, rows in grouped.items():
            sorted_rows = sorted(
                rows,
                key=lambda r: r.get("transition_time") or datetime.min.replace(tzinfo=UTC),
            )
            steps: list[str] = []
            total_dwell = 0
            for r in sorted_rows:
                step = canonical_step_name(
                    service=r.get("actor_service"),
                    status_to=r.get("status_to"),
                )
                if not steps or steps[-1] != step:
                    steps.append(step)
                total_dwell += int(r.get("dwell_ms") or 0)
            final_status = str(sorted_rows[-1].get("status_to") or "IN_PROGRESS")
            sequences.append((j_id, steps, final_status, total_dwell))
        return self.mine_from_sequences(sequences, now=now)
