#!/usr/bin/env python3
"""Brief assembler for the midpoint-review skill.

Thin shim — actual composition lives in ``tempo.briefs.midpoint_review_brief``.

Usage:
    uv run python .claude/skills/midpoint-review/preflight.py [--week YYYY-Www] [--plan-id <id>]

Writes the JSON brief to stdout. Exits 2 if the plan can't be resolved or
the requested week falls outside any phase.
"""

from __future__ import annotations

import argparse
import json
import sys

from tempo.briefs import NoActivePlanError, midpoint_review_brief
from tempo.plans import MultiplePlansError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Assemble the midpoint-review brief — adherence, wellness, load, "
            "threshold provenance, and signals across the current phase up "
            "to and including --week."
        ),
    )
    parser.add_argument(
        "--week",
        default=None,
        help="ISO week id (YYYY-Www). Defaults to the ISO week of today.",
    )
    parser.add_argument(
        "--plan-id",
        default=None,
        help="Plan id (e.g. 2027-half-ironman). Defaults to auto-detect.",
    )
    parser.add_argument("--pretty", action="store_true", help="Indent the JSON output.")
    args = parser.parse_args(argv)

    try:
        brief = midpoint_review_brief(week_id=args.week, plan_id=args.plan_id)
    except (NoActivePlanError, MultiplePlansError) as e:
        sys.stderr.write(f"midpoint-review: {e}\n")
        return 2

    json.dump(brief, sys.stdout, indent=2 if args.pretty else None, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - entry
    raise SystemExit(main())
