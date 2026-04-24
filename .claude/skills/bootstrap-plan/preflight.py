#!/usr/bin/env python3
"""Brief assembler for the bootstrap-plan skill.

Thin shim — actual composition lives in ``tempo.briefs.bootstrap_plan_brief``.

Usage:
    uv run python .claude/skills/bootstrap-plan/preflight.py --goal-id 2026-ironman-lake-placid

Writes the JSON brief to stdout. Exits 2 if the goal id is unknown.
"""

from __future__ import annotations

import argparse
import json
import sys

from tempo.briefs import UnknownGoalError, bootstrap_plan_brief


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble the bootstrap-plan brief for an agent to reason over.",
    )
    parser.add_argument(
        "--goal-id",
        required=True,
        help="Race or goal id (e.g. 2026-ironman-lake-placid).",
    )
    parser.add_argument("--pretty", action="store_true", help="Indent the JSON output.")
    args = parser.parse_args(argv)

    try:
        brief = bootstrap_plan_brief(args.goal_id)
    except UnknownGoalError as e:
        sys.stderr.write(f"bootstrap-plan: {e}\n")
        return 2

    json.dump(brief, sys.stdout, indent=2 if args.pretty else None, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - entry
    raise SystemExit(main())
