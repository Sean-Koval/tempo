# Tempo

Local-first endurance coaching agent built on [intervals.icu](https://intervals.icu) and [Claude Code](https://docs.claude.com/en/docs/claude-code). Tempo sits on top of intervals.icu (the source of truth for raw data) and adds the reasoning layer — methodology, plan artifacts, a derived-metrics store, and a vector index for coaching knowledge and agent memory.

Design doc / authoritative spec: [`take-this-project-and-temporal-tarjan.md`](/home/seanm/.claude/plans/take-this-project-and-temporal-tarjan.md).

## What it does

- Takes a goal (race from `athlete/race-calendar.yaml` or non-race goal from `athlete/goals.yaml`) and drafts a full periodized plan with phase-by-phase structure and rationale.
- Drafts a week at a time based on recent adherence, wellness, load, and any active injury flags. Writes plans as git-versioned markdown so every adjustment is a diff with a changelog entry.
- Pushes planned sessions to intervals.icu only after you review the diff (no autonomous writes).
- Retrieves trusted coaching knowledge (Friel, Seiler, Jeukendrup, CTS, …) via semantic search; ingests new articles into a vetted corpus.
- Remembers why it made past decisions via a persistent decisions table + vector memory — so "why did we cut volume in March?" is answerable six months later.

## Architecture at a glance

| Substrate | Stores | Why |
| --- | --- | --- |
| SQLite (`data/coach.db`) | activities, wellness, load, adherence, decisions | stable schema, fast aggregation, disposable |
| Markdown + YAML frontmatter | plans, weeks, methodology, journals | git-diffable, both queryable and LLM-friendly |
| LanceDB (`data/vectors/`) | knowledge, memory, session library | semantic retrieval where structure doesn't cut it |
| JSONL (`data/raw/`) | every API response, gzipped weekly | audit trail; makes the DB rebuildable |

MCP servers wired in: **intervals** (forked from eddmann/intervals-icu-mcp), **coach-db** (built here — typed surface over `coach.db` + LanceDB), **strava** (r-huijts upstream).

## Quickstart

```bash
# 1. clone with submodules
git clone --recurse-submodules <your-fork-of-this-repo> tempo
cd tempo

# 2. set credentials
cp .env.example .env
# edit .env — get API_KEY/ATHLETE_ID from https://intervals.icu/settings

# 3. sync submodule deps
cd mcp-servers/intervals-icu-mcp && uv sync && cd ../..

# 4. fill in athlete profile
$EDITOR athlete/profile.yaml   # FTP, zones, weight, thresholds
$EDITOR athlete/preferences.md # coaching style, schedule, constraints

# 5. declare a goal (or a race)
$EDITOR athlete/race-calendar.yaml
# OR
$EDITOR athlete/goals.yaml

# 6. open Claude Code in this directory and start planning
#    — .claude/CLAUDE.md loads automatically and the intervals MCP is wired up

# (optional) enable auto-embedding of knowledge/ changes on commit
bash scripts/install-hooks.sh
```

At this point you can have useful planning conversations with intervals data live. The deterministic CLI (`coach sync`, `coach status`), SQLite/LanceDB layers, Skills, and dashboards land in later phases — see the plan file.

## Repository layout

```
athlete/       Who Sean is right now (profile, goals, injuries, prefs)
knowledge/     Coaching corpus (methodology, nutrition, research, sources)
plans/         Plans in flight — one directory per plan-id
journal/       Daily notes and decisions
data/          Derived metrics, vector indexes, raw JSONL dumps (gitignored)
mcp-servers/   MCP servers: intervals (submodule), strava, coach-db
scripts/       Deterministic scripts (sync, derive, embed, coach CLI)
.claude/       System prompt, skills, slash commands, MCP settings
```

## Build phases

- **Phase 0** — scaffold, intervals MCP wired, athlete+knowledge stubs. Conversational planning works.
- **Phase 1** — SQLite schema + `coach sync`/`coach status`. Fast local queries.
- **Phase 2** — LanceDB + knowledge corpus + auto-embed on commit.
- **Phase 3** (you are here) — `coach-db` MCP server: `query_activities`, `get_load_curve`, `get_readiness`, `get_adherence`, `compare_plan_to_actual`, `search_knowledge`, `search_memory`, `find_similar_session`, `log_decision`. Strava wired (r-huijts).
- **Phase 4** — `bootstrap-plan`, `plan-training-week`, `review-week`, `morning-check-in` Skills. Full coaching loop.
- **Phase 5** — Dashboards (week / macro / decisions).
- **Phase 6** — `ingest-research`, `draft-race-plan`, nutrition deep-dive.

## Design invariants

- Intervals.icu is the source of truth for raw data. `coach.db` is always rebuildable.
- Writes to intervals are explicit. Agent drafts; user reviews diff; user runs `coach push-week`.
- Every plan adjustment produces a changelog entry with rationale + a `log_decision` call.
- Structured where structure exists (SQL), semantic where it doesn't (vectors), narrative where neither fits (markdown).
- Skills are for procedures. Conversation is for judgment. Don't skill-ify Q&A.
