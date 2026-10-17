# Copyright 2026 Harness² Authors & Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Two-layer token budget ledger B = (R, P) (Section 3.1)."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from godview.harness.core.spec import BudgetSpec


class BudgetError(RuntimeError):
    """Raised when either R or P is exhausted."""


class BudgetExhausted(BudgetError):
    """Raised when either rollout budget R or proposer budget P is exhausted."""


@dataclass
class LedgerEntry:
    """Single token consumption record in the two-layer budget ledger."""

    ts: float
    pool: str  # "R" or "P"
    tag: str
    tokens: int
    note: str = ""


@dataclass
class Ledger:
    """Tracks rollout (R) and proposer (P) token spend against a BudgetSpec."""

    spec: BudgetSpec = field(default_factory=BudgetSpec)
    r_used: int = 0
    p_used: int = 0
    entries: list[LedgerEntry] = field(default_factory=list)
    _persist_path: Path | None = None
    strict: bool = False

    @property
    def r_limit(self) -> int:
        return self.spec.R * 1_000

    @property
    def p_limit(self) -> int:
        return self.spec.P * 1_000

    @property
    def r_remaining(self) -> int:
        return max(0, self.r_limit - self.r_used)

    @property
    def p_remaining(self) -> int:
        return max(0, self.p_limit - self.p_used)

    def can_rollout(self) -> bool:
        return self.r_used < self.r_limit

    def can_propose(self) -> bool:
        return self.p_used < self.p_limit

    def charge_r(self, tokens: int, tag: str, note: str = "") -> None:
        if self.strict and self.r_used + max(0, int(tokens)) > self.r_limit:
            raise BudgetExhausted(
                f"Rollout budget R exhausted: {self.r_used + int(tokens)} > {self.r_limit}"
            )
        self.r_used += max(0, int(tokens))
        self.entries.append(LedgerEntry(time.time(), "R", tag, int(tokens), note))
        self._flush()

    def charge_p(self, tokens: int, tag: str, note: str = "") -> None:
        if self.strict and self.p_used + max(0, int(tokens)) > self.p_limit:
            raise BudgetExhausted(
                f"Proposer budget P exhausted: {self.p_used + int(tokens)} > {self.p_limit}"
            )
        self.p_used += max(0, int(tokens))
        self.entries.append(LedgerEntry(time.time(), "P", tag, int(tokens), note))
        self._flush()

    def bind(self, path: Path) -> Ledger:
        self._persist_path = path
        self._flush()
        return self

    def summary(self) -> dict[str, int | float]:
        return {
            "R_used": self.r_used,
            "R_limit": self.r_limit,
            "R_pct": round(100.0 * self.r_used / self.r_limit, 2) if self.r_limit else 0.0,
            "P_used": self.p_used,
            "P_limit": self.p_limit,
            "P_pct": round(100.0 * self.p_used / self.p_limit, 2) if self.p_limit else 0.0,
        }

    def _flush(self) -> None:
        if not self._persist_path:
            return
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            **self.summary(),
            "entries": [asdict(e) for e in self.entries],
        }
        self._persist_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class BudgetLedger(Ledger):
    """Strict two-layer token budget ledger B = (R, P) raising BudgetExhausted on overflow."""

    def __init__(
        self,
        spec: BudgetSpec | None = None,
        *,
        r_limit_tokens: int | None = None,
        p_limit_tokens: int | None = None,
        strict: bool = True,
    ) -> None:
        resolved_spec = spec or BudgetSpec()
        super().__init__(spec=resolved_spec, strict=strict)
        self._r_limit_override = r_limit_tokens
        self._p_limit_override = p_limit_tokens

    @property
    def r_limit(self) -> int:
        if self._r_limit_override is not None:
            return self._r_limit_override
        return super().r_limit

    @property
    def p_limit(self) -> int:
        if self._p_limit_override is not None:
            return self._p_limit_override
        return super().p_limit
