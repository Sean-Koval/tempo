"""Brief assembler for the ingest-research skill.

Thin shim — actual fetching/parsing/matching lives in ``tempo.briefs.ingest_research_brief``
(which delegates to ``tempo.research``).

Usage:
    uv run python .claude/skills/ingest-research/preflight.py --source <url-or-path>

Writes the JSON brief to stdout. Exits 2 if the source can't be fetched/parsed.
"""

from __future__ import annotations

import argparse
import json
import sys

from tempo.briefs import IngestSourceError, ingest_research_brief


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble the ingest-research brief for an agent to reason over.",
    )
    parser.add_argument(
        "--source",
        required=True,
        help="A URL (http/https) or a local PDF path.",
    )
    parser.add_argument("--pretty", action="store_true", help="Indent the JSON output.")
    args = parser.parse_args(argv)

    try:
        brief = ingest_research_brief(args.source)
    except IngestSourceError as e:
        sys.stderr.write(f"ingest-research: {e}\n")
        return 2

    json.dump(brief, sys.stdout, indent=2 if args.pretty else None, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - entry
    raise SystemExit(main())
