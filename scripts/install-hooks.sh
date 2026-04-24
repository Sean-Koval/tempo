#!/usr/bin/env bash
# Point git at .githooks/ so Tempo's post-commit auto-embedding runs.
# Idempotent — safe to re-run.

set -eu

cd "$(git rev-parse --show-toplevel)"
git config core.hooksPath .githooks
echo "[tempo] core.hooksPath = $(git config core.hooksPath)"
echo "[tempo] hooks installed from .githooks/"
