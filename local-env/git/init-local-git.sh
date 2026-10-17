#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="${LOCAL_GIT_BASE_DIR:-${SCRIPT_DIR}/repos}"
BARE_REPO_DIR="${BASE_DIR}/god-view.git"
WORK_CLONE_DIR="${BASE_DIR}/worktree"

mkdir -p "${BASE_DIR}"

if [ ! -d "${BARE_REPO_DIR}" ]; then
  git init --bare --initial-branch=main "${BARE_REPO_DIR}"
fi

rm -rf "${WORK_CLONE_DIR}"
git clone "${BARE_REPO_DIR}" "${WORK_CLONE_DIR}" >/dev/null 2>&1 || true

cd "${WORK_CLONE_DIR}"
git config user.name "local-git-seed"
git config user.email "seed@cymbal.local"

mkdir -p cymbal/services/underwriting-los-svc/app
cat > cymbal/services/underwriting-los-svc/app/rules_engine.py <<'PYEOF'
"""Baseline underwriting rules for local Git remote parity."""
MIN_CIBIL_SCORE = 680
MAX_FOIR_PCT = 60.0
PYEOF

git add -A
if ! git diff --cached --quiet; then
  git commit -m "chore: initial baseline rules_engine (FOIR<=60, CIBIL>=680)"
  git push origin HEAD:main
fi

echo "Initialized local bare Git remote at ${BARE_REPO_DIR}"
