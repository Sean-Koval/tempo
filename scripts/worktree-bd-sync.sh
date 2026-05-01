#!/usr/bin/env bash
# Sync the current worktree's .beads/issues.jsonl to canonical bd state.
#
# Worktrees share .beads/beads.db (it lives in the main worktree root)
# but each worktree branch has its own committed snapshot of
# .beads/issues.jsonl. Without intervention the snapshot is stale at
# branch-creation time, which causes spurious merge conflicts when the
# worktree branch lands and a confused agent that sees outdated tickets
# in `git diff`.
#
# This script:
#   1. No-ops outside a worktree (main worktree already canonical).
#   2. `bd sync --flush-only` so the canonical jsonl reflects the daemon.
#   3. Copies canonical jsonl into this worktree's .beads/issues.jsonl.
#
# Wired as a SessionStart hook (.claude/settings.json) and reused by
# .githooks/pre-commit for belt-and-suspenders coverage.

set -u

if ! command -v bd >/dev/null 2>&1; then
    exit 0
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    exit 0
fi

GIT_DIR="$(git rev-parse --git-dir)"
GIT_COMMON_DIR="$(git rev-parse --git-common-dir)"

# Resolve to absolute paths so the equality check is meaningful from any cwd.
GIT_DIR_ABS="$(cd "$GIT_DIR" && pwd)"
GIT_COMMON_DIR_ABS="$(cd "$GIT_COMMON_DIR" && pwd)"

if [ "$GIT_DIR_ABS" = "$GIT_COMMON_DIR_ABS" ]; then
    exit 0
fi

MAIN_ROOT="$(dirname "$GIT_COMMON_DIR_ABS")"
CANONICAL_JSONL="$MAIN_ROOT/.beads/issues.jsonl"
WORKTREE_ROOT="$(git rev-parse --show-toplevel)"
WORKTREE_JSONL="$WORKTREE_ROOT/.beads/issues.jsonl"

if [ ! -f "$CANONICAL_JSONL" ] || [ ! -d "$WORKTREE_ROOT/.beads" ]; then
    exit 0
fi

bd sync --flush-only >/dev/null 2>&1 || true

if ! cmp -s "$CANONICAL_JSONL" "$WORKTREE_JSONL"; then
    cp "$CANONICAL_JSONL" "$WORKTREE_JSONL"
fi

exit 0
