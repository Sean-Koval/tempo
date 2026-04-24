# Tempo Architecture — MCP vs Skills vs CLI

**Status:** load-bearing for Phase 4+. Revisit if a surface starts blurring.
**Owner:** whoever touches Phase 4 skills or adds an MCP tool.
**Authoritative spec:** [`/home/seanm/.claude/plans/take-this-project-and-temporal-tarjan.md`](/home/seanm/.claude/plans/take-this-project-and-temporal-tarjan.md) — when scope is unclear, the plan file wins on *what*; this doc wins on *how the pieces fit*.

---

## Three entry-point layers

| Layer | What it is | Caller | Cost model |
| --- | --- | --- | --- |
| **MCP tools** | Typed capabilities over external systems + internal DB | Agent, mid-reasoning | Always-loaded schema tax (~60 tools × 100–200 tok each) |
| **Skills** | Canonical procedures — SKILL.md + optional `preflight.py` | User (slash/CLI) or agent when the task matches | Loaded on invocation only; preflight is pure Python |
| **CLI / scripts** | Deterministic, no-LLM state changes | User, explicitly typed | Zero LLM cost |

**Microservices analogy** (load-bearing): MCP = services, Skills = use-cases / orchestration, markdown files (`plans/`, `athlete/`, `knowledge/`) = domain artifacts. Don't blur the layers — that's where designs rot.

## When to use which

**MCP tool** when:
- Operation is called ad-hoc across many conversations ("what was my TSB on March 3?")
- It's a primitive over shared state — DB query, vector search, external API write
- The typed surface is the point (agent composing queries)
- Multiple skills will reuse it

**Skill** when:
- The procedure has a canonical sequence ("read injury-log → pull 14d adherence → validate against decision-rules → write week file → log_decision")
- You'd want to hand it to a new coach and say *"always do it this way"*
- Preflight needs to assemble a structured brief from 4+ sources — cheaper in Python than 4 MCP round-trips
- The output shape is opinionated (a week file, a review note, a race countdown)

**CLI** when:
- Deterministic, no judgment needed (`coach sync`, `coach push-week`)
- Hot path — don't pay LLM latency for what a script does
- Side-effectful on shared systems (explicit user action)

## The token-tax rule

Every MCP tool description is loaded into **every** coaching conversation. Currently ~60 tools (intervals 50, coach-db 9, strava ~25). That's real context budget. Skills don't pay this tax — their bodies only load on invocation. Therefore:

- **Keep the MCP surface small and primitive.** The nine coach-db tools are the right shape. Don't add `draft_week` or `review_week` as MCP tools — those are procedures, not capabilities.
- **Push complexity into `preflight.py`.** It runs in-process, imports tempo/coach-db Python directly, emits a structured brief. Agent reasons over 2–3KB of focused JSON instead of making 15 MCP calls.

---

## Domain-logic invariant: single source, thin surfaces

```
src/tempo/                                 ← domain (imported by everything)
  ├── db.py, sync.py, derive.py, embed.py, …
  └── queries.py                           ← (Phase 4 refactor) shared read functions
       │
       ├── src/tempo/cli.py                — Typer wrapper (display helpers in display.py)
       ├── mcp-servers/coach-db/.../sql.py — FastMCP wrapper
       └── .claude/skills/*/preflight.py   — brief assembler
```

If two surfaces reimplement the same query (e.g., "last 14 days adherence"), the model is broken. Preflight, MCP tools, and CLI status all want a superset of the same reads — they must hit the same Python functions.

Allowed import directions:
- `coach_db_mcp.*` → `tempo.*` (MCP package already declares tempo as an editable path dep).
- `.claude/skills/*/preflight.py` → `tempo.*` (never the reverse).
- `tempo.*` imports nothing from skills or MCP packages. Ever.

## How preflight gets its data

| Option | Verdict |
| --- | --- |
| Duplicate SQL / logic in preflight | No — drift guaranteed |
| Shell out to `coach ...` and parse stdout | No — cold Python every call, text parsing, wasted Rich formatting, permission friction |
| Call MCP tools over JSON-RPC | No — same Python process; the overhead buys nothing |
| Import `tempo.queries` (+ `tempo.embed.search*` where needed) directly | **Yes** |

Preflight is a **thin** script: argparse → call `tempo.queries.plan_week_brief(week_id)` → print JSON to stdout. Real work stays in the library. Keep the brief under a few KB — the agent reads it, then reasons.

---

## Phase 4 entry-point decision — option B (slash commands)

The plan file lists CLI verbs `coach plan week`, `coach review week`, `coach bootstrap-plan`, `coach check-in`. Two honest options:

### Option A — CLI verb runs preflight, stops there *(deferred — future dev)*

`coach plan week --next` writes `data/briefs/plan-week-2026-W18.json` and prints "open Claude Code and run /plan-training-week." Deterministic side doesn't invoke an LLM; agent side is Claude Code's job.

**Revisit when:** shell muscle-memory genuinely hurts ("I'm always slashing into Claude Code anyway"), OR preflight becomes expensive enough that caching briefs to disk between invocations pays off, OR Tempo grows a non-Claude-Code surface (cron, scripts, other agents).

### Option B — skip the CLI verb, slash command is the entry point *(chosen for Phase 4)*

User types `/plan-training-week` in Claude Code. The skill's own preflight runs there. CLI only handles the truly deterministic verbs (`sync`, `status`, `push-week`, `vectors …`, `check-in`).

**Why B wins now:**
- No UX fork. One path per operation.
- Adding `coach plan week` that only runs preflight is a CLI verb that does nothing visible to the user — weak payoff.
- Slash commands surface the skill list directly in Claude Code — discoverable.
- Preflight-only CLI duplicates the first half of the skill, locking that boundary early before we've seen real usage patterns.

`coach push-week` stays CLI — it's the explicit deterministic write to intervals, always was designed that way.

`coach check-in` is borderline. Plan file calls it a "structured prompt" CLI verb AND a skill. It's small enough to be both — the CLI verb prompts, writes to intervals + DB, no LLM. The skill is the agent-facing variant if we want chat-style check-in. Start with CLI; add skill only if a real need emerges.

---

## Structural moves for Phase 4

**1. Extract `src/tempo/queries.py` before any skill lands.**
Move read functions from `coach_db_mcp/sql.py` (`query_activities`, `get_load_curve`, `get_readiness`, `get_adherence`, `compare_plan_to_actual`) into `tempo.queries`. Have `coach_db_mcp.server` and future preflights import from there. Keep Pydantic models in `coach_db_mcp.models` — those are MCP-serialization concerns, not domain concerns.

**2. Move display helpers out of `cli.py`.**
`_print_load`, `_print_week`, `_print_wellness`, `_print_active_injuries` → `src/tempo/display.py`. Rich-formatting is a presentation concern, separate from command dispatch.

**3. Don't split `cli.py` into a package directory yet.**
359 lines is fine. It'll grow by Phase 4 but stay under the threshold where a `cli/` package pays for itself (~600 lines, or nontrivial shared command state). Defer.

**4. Skill directory layout (co-located preflight).**
```
.claude/skills/plan-training-week/
   SKILL.md              — procedure, invariants, output contract
   preflight.py          — thin: argparse → tempo.queries.* → JSON
   schemas/brief.json    — (optional) JSON schema for the brief
```
Preflight belongs with SKILL.md, not in `src/tempo/`. It's harness config, not a Python package. Claude Code expects it under `.claude/skills/`.

---

## Anti-patterns to avoid

- **Skill-ifying Q&A.** "What does my load look like?" is conversation, not procedure. No SKILL.md.
- **MCP-ifying procedures.** If it has a 5-step canonical order with branching, a tool description can't express that; a SKILL.md can.
- **Duplicating state in the MCP surface.** `coach.db` is a read model over `data/raw/` + `plans/`. If a new tool implies storing canonical data only in the DB, reconsider — `data/raw/` + `plans/` must stay rebuildable.
- **Heavy preflight that embeds judgment.** Preflight *gathers*; the agent *reasons*. If preflight starts computing "recommended TSS," that's the agent's job.
- **Thin skills that wrap one MCP tool.** Dead weight. A Skill earns its keep when it sequences multiple calls + writes artifacts + logs decisions.
- **Preflight shelling out to `coach` CLI.** Subprocess cold-start + text parsing + wasted Rich formatting. Import the Python.

---

## The one-line summary

Single domain package (`src/tempo/`) with three thin exposure surfaces (CLI, MCP, preflight). MCP = stateful primitives. Skills = procedures. CLI = deterministic state changes. All three read through `tempo.queries`. Phase 4 enters through slash commands (option B); option A stays on the shelf.
