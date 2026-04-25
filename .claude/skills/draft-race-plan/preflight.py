"""Brief assembler for the draft-race-plan skill.

Thin shim — actual composition lives in ``tempo.briefs.race_plan_brief``.

Usage:
    uv run python .claude/skills/draft-race-plan/preflight.py [--race-id <id>] [--within-days N]

Writes the JSON brief to stdout. Exits 2 if no race can be resolved.
"""

from __future__ import annotations

import argparse
import json
import sys

from tempo.briefs import NoUpcomingRaceError, race_plan_brief


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble the draft-race-plan brief for an agent to reason over.",
    )
    parser.add_argument(
        "--race-id",
        default=None,
        help="Race id from athlete/race-calendar.yaml. Defaults to next A-priority race within --within-days.",
    )
    parser.add_argument(
        "--within-days",
        type=int,
        default=28,
        help="Auto-pick horizon when --race-id is omitted. Default: 28.",
    )
    parser.add_argument("--pretty", action="store_true", help="Indent the JSON output.")
    args = parser.parse_args(argv)

    try:
        brief = race_plan_brief(race_id=args.race_id, within_days=args.within_days)
    except NoUpcomingRaceError as e:
        sys.stderr.write(f"draft-race-plan: {e}\n")
        return 2

    json.dump(brief, sys.stdout, indent=2 if args.pretty else None, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - entry
    raise SystemExit(main())
