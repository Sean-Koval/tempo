#!/usr/bin/env python3
"""Brief assembler for the review-week skill.

Thin shim — actual composition lives in ``tempo.briefs.review_week_brief``.

Usage:
    uv run python .claude/skills/review-week/preflight.py [--week YYYY-Www] [--plan-id <id>]

Writes the JSON brief to stdout. Exits 2 if the plan can't be resolved.
"""

from __future__ import annotations

import argparse
import json
import sys

from tempo.briefs import NoActivePlanError, review_week_brief
from tempo.plans import MultiplePlansError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble the review-week brief for an agent to reason over.",
    )
    parser.add_argument(
        "--week",
        default=None,
        help="ISO week id (YYYY-Www). Defaults to the week ending 7 days ago.",
    )
    parser.add_argument(
        "--plan-id",
        default=None,
        help="Plan id (e.g. 2026-ironman-lake-placid). Defaults to auto-detect.",
    )
    parser.add_argument("--pretty", action="store_true", help="Indent the JSON output.")
    args = parser.parse_args(argv)

    try:
        brief = review_week_brief(week_id=args.week, plan_id=args.plan_id)
    except (NoActivePlanError, MultiplePlansError) as e:
        sys.stderr.write(f"review-week: {e}\n")
        return 2

    json.dump(brief, sys.stdout, indent=2 if args.pretty else None, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - entry
    raise SystemExit(main())
