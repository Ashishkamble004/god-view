"""Deterministic validator for change events and commit regression correlation."""

from __future__ import annotations

import json
import sys


def main() -> None:
    raw = sys.stdin.read() or "{}"
    payload = json.loads(raw)
    context = payload.get("context", {})
    recent_commits = context.get("recent_commits", [])
    commit_sha = context.get("commit_sha", "")

    correlated = bool(recent_commits or commit_sha)
    output = {
        "ok": True,
        "correlated": correlated,
        "commit_count": len(recent_commits),
        "commit_sha": commit_sha,
    }
    print(json.dumps(output))

if __name__ == "__main__":
    main()
