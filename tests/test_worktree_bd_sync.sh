#!/usr/bin/env bash
# Verify scripts/worktree-bd-sync.sh and .githooks/pre-commit refresh a
# worktree's tracked .beads/issues.jsonl from canonical bd state.
#
# Run from the main repo root (or any clone of it) — the script creates
# a throwaway worktree under .claude/worktrees/_test-l35-* and tears it
# down on exit.
#
# Exits 0 on success, 1 on the first failed assertion.

set -eu

# Resolve the script + hook from THIS branch's worktree (the one containing
# the test file), not from the main worktree which may not have them yet.
TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "$TEST_DIR/.." && pwd)"
SYNC_SCRIPT="$SOURCE_ROOT/scripts/worktree-bd-sync.sh"
PRECOMMIT_HOOK="$SOURCE_ROOT/.githooks/pre-commit"

REPO_ROOT="$(git rev-parse --show-toplevel)"

# Refuse to run from inside an existing worktree — git won't let us nest
# worktrees by name and the "main worktree no-op" check below would lie.
if [ "$(cd "$(git rev-parse --git-dir)" && pwd)" != "$(cd "$(git rev-parse --git-common-dir)" && pwd)" ]; then
    echo "[test] must run from the main worktree, not a sub-worktree" >&2
    exit 2
fi

WORKTREE_NAME="_test-l35-$$"
WORKTREE_PATH="$REPO_ROOT/.claude/worktrees/$WORKTREE_NAME"
BRANCH="test-l35-$$"

cleanup() {
    git worktree remove --force "$WORKTREE_PATH" 2>/dev/null || true
    git branch -D "$BRANCH" 2>/dev/null || true
}
trap cleanup EXIT

fail() { echo "[test] FAIL: $*" >&2; exit 1; }
pass() { echo "[test] ok: $*"; }

# ---- Setup: ensure canonical state, then create worktree --------------------

bd sync --flush-only >/dev/null 2>&1 || true
git worktree add -b "$BRANCH" "$WORKTREE_PATH" HEAD >/dev/null

# A freshly-added worktree gets HEAD's committed snapshot of issues.jsonl.
# That may already differ from canonical bd state if `bd sync --flush-only`
# has happened since HEAD was committed (e.g. another agent updated a
# ticket). That divergence IS the bug this hook fixes — don't assert
# equality before running the hook.

# ---- Test 1: SessionStart script no-ops in main worktree --------------------

bash "$SYNC_SCRIPT"
pass "SessionStart script no-ops in main worktree (exit 0)"

# ---- Test 2: SessionStart script refreshes a stale worktree snapshot --------

# Force drift even if HEAD-snapshot already happens to match canonical.
echo '{"id":"_test_drift","_drift":true}' >> "$WORKTREE_PATH/.beads/issues.jsonl"
cmp -s "$REPO_ROOT/.beads/issues.jsonl" "$WORKTREE_PATH/.beads/issues.jsonl" \
    && fail "drift not actually applied"

(cd "$WORKTREE_PATH" && bash "$SYNC_SCRIPT")
cmp -s "$REPO_ROOT/.beads/issues.jsonl" "$WORKTREE_PATH/.beads/issues.jsonl" \
    || fail "SessionStart hook did not refresh worktree jsonl"
pass "SessionStart hook refreshes worktree jsonl from canonical"

# ---- Test 3: pre-commit hook also refreshes (belt-and-suspenders) -----------

# Re-introduce drift, then invoke the pre-commit hook from inside the
# worktree. Skip if .githooks/pre-commit is missing on this branch.
if [ -x "$PRECOMMIT_HOOK" ]; then
    echo '{"id":"_test_drift2","_drift":true}' >> "$WORKTREE_PATH/.beads/issues.jsonl"
    (cd "$WORKTREE_PATH" && "$PRECOMMIT_HOOK") >/dev/null 2>&1 \
        || true  # gitleaks may exit non-zero if installed; bd path runs first
    cmp -s "$REPO_ROOT/.beads/issues.jsonl" "$WORKTREE_PATH/.beads/issues.jsonl" \
        || fail "pre-commit hook did not refresh worktree jsonl"
    pass "pre-commit hook refreshes worktree jsonl from canonical"
else
    echo "[test] skip: .githooks/pre-commit not present"
fi

echo "[test] all assertions passed"
