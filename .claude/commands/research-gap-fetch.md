---
name: research-gap-fetch
description: Close the US-4 loop — detect a knowledge gap, run constrained WebSearch over sources.yaml-derived queries, get explicit approval, and feed approved URLs through /ingest-research.
trigger: /research-gap-fetch <query> [--topic <t>] [--top-k <n>]
---

# /research-gap-fetch

Closes the US-4 escalation loop. Given a query that the local corpus probably
can't answer (a niche-injury, race-condition, or fueling protocol question),
this command:

1. Asks the CLI for a gap brief.
2. Runs WebSearch **only** over the brief's pre-built site-scoped queries —
   never free-form. This preserves the credibility-leak protection.
3. Surfaces the URL list with credibility tags via an AskUserQuestion-style
   approval prompt. Cancel = nothing written.
4. On approval, feeds each URL through `/ingest-research` and stamps the
   resulting note's frontmatter with `ingest_via: research-gap`,
   `gap_query`, `suggestion`, and `source_id`.

## Hard rules — do not bend

- **WebSearch queries are constrained.** Pass the brief's `suggestions[i].query`
  string verbatim to WebSearch. Do not paraphrase, do not strip the `site:`
  filter, do not add the gap query as a separate free-form search. If a
  suggestion is malformed, drop it — don't repair it by guessing.
- **No fetch without approval.** Even if WebSearch returns a perfect-looking
  PubMed hit, `/ingest-research` does not run until the user has explicitly
  approved the URL.
- **Cancel writes nothing.** If the user declines (no URLs selected, or
  cancels the prompt), do not call `/ingest-research`, do not write
  frontmatter, do not log a decision. Report cancellation cleanly.
- **Credibility tags pass through.** Each approval-prompt entry MUST show the
  credibility tag from the brief alongside the URL. Use the brief's
  `suggestions[i].credibility` (peer_reviewed, expert_practitioner, etc.) —
  do not re-derive it.
- **Unlisted sources stay unvetted.** If WebSearch surfaces a URL whose
  domain is not in `knowledge/sources.yaml`, mark it `credibility: unvetted`
  in the approval prompt and in the resulting note. Do not promote it to
  match the suggestion's credibility just because the search ran under a
  trusted-source query — `site:` is a hint, not a guarantee.

## Step 1 — Get the brief

```bash
uv run coach research-gap "$ARG_QUERY" --execute --top-k 3 [--topic <t>]
```

Read the JSON. If `gap_detected` is false, stop — the local corpus answered
the question. Show the user the top hits and exit.

If `suggestions` is non-empty, follow the constrained-search runbook in
Steps 2–6 below.

If `suggestions` is empty AND the brief sets `discovery_required: true`,
the topic has no registered source. Switch to the **Discovery branch** at
the bottom of this file (Step 2D) — that's the only path on which an
unconstrained WebSearch is permitted, and the gap reason for `log_decision`
becomes `no_registered_sources`.

## Step 2 — Run constrained WebSearch

For each entry in `suggestions[]`, call the WebSearch tool with the entry's
`query` field exactly as given. Collect URL + title (+ snippet if available)
into a candidate list. Carry the suggestion's `source_id`, `credibility`,
and `source_name` forward — those tags travel with the URL.

Cap candidates at ~5 per suggestion to keep the approval prompt scannable.

## Step 3 — Approval gate

Use AskUserQuestion to present the candidate list. Each row MUST show:

```
[<credibility>] <source_name> — <title>
  <url>
```

Let the user select 0..N URLs. An empty selection = cancel.

## Step 4 — Ingest each approved URL

For each approved URL, invoke the `ingest-research` skill (`/ingest-research <url>`).
After the skill writes its note, append these fields to the note's frontmatter:

```yaml
ingest_via: research-gap
gap_query: "<original query the user passed in>"
suggestion: "<the exact site-scoped query that surfaced this URL>"
source_id: "<source_id from sources.yaml, or 'unlisted' if WebSearch returned an off-registry domain>"
```

If the URL's domain is not in `sources.yaml`, the ingest skill will already
set `credibility: unvetted` — do not override it.

## Step 5 — Log the decision

After all approved URLs are ingested, call the `coach-db` MCP `log_decision`
tool with:

- `scope`: `research-gap:<slug-of-query>`
- `kind`: `research_gap_closed`
- `rationale`: one paragraph that MUST start with the gap reason verbatim
  (`no_hits` / `low_score` / `low_credibility` / `thin_coverage`) and the
  query that triggered it, then names the suggestions that ran, the URLs
  approved, and the resulting note paths. The gap reason is what a future
  search-memory query keys off — without it, the decision becomes a
  blob of prose that's much harder to retrieve later.
- `changed_files`: list of every note `/ingest-research` produced.

## Step 6 — Report

Tell the user, terse:

- Gap reason (`no_hits` / `low_score` / etc.).
- Suggestion queries that ran.
- URLs approved + ingested (with paths).
- URLs declined.

If cancelled at the approval gate, just say so — no decision logged, no
files written.

## Step 2D — Discovery branch (no registered source matched)

This branch fires only when the brief from Step 1 has
`discovery_required: true` (i.e. `suggestions == []`). It is the single
exception to the "constrained queries only" rule. The whole point of the
exception: the user has just told us the local registry has no source
for this topic, so a `site:`-scoped search would return nothing useful.
Compensating controls keep the credibility model honest:

- The unconstrained search runs **once**, with the raw gap query.
- Every URL surfaced gets a tentative classification by domain heuristic
  (`.gov` / `.edu` / known peer-review publisher → `peer_reviewed`;
  mass-media TLD → `evidence_based_journalism` (vetted_needed);
  forum/blog host → `unvetted`; matched registered domain → its
  registered tag).
- Tentative tags stay tentative. Ingested notes are written `unvetted`
  unless the user explicitly upgrades a domain during approval.
- Domains the user marks "register" are appended to
  `knowledge/sources-pending.yaml`. **Never** to `sources.yaml` — that
  promotion remains a deliberate human act.

### Discovery runbook

1. Call WebSearch ONCE with the raw `query` field from the brief
   (no `site:`, no paraphrase, no extra terms).
2. For each result, classify the domain. The CLI emits
   `constraints.unconstrained_query` so the slash command knows what
   query to fire and `constraints.log_decision_gap_reason_prefix` so the
   later decision call uses the right key.
3. AskUserQuestion presents each result as:

   ```
   [<tentative-credibility>] <domain> — <title>
     <url>
     why: <classification rationale>
     [ ] ingest this URL
     [ ] register <domain> as a draft source (sources-pending.yaml)
   ```

4. On approval, for each ingest-checked URL run `/ingest-research <url>`.
   Stamp the resulting note's frontmatter with:

   ```yaml
   ingest_via: research-gap-discovery
   gap_query: "<original query>"
   suggestion: "<original query>"
   source_id: unlisted
   domain_classification: "<rationale>"
   ```

   Credibility starts `unvetted` unless the user explicitly upgraded.
5. For each register-checked domain, append a draft entry to
   `knowledge/sources-pending.yaml` (use
   `tempo.gap_search.write_pending_sources` from a small Python shim, or
   the equivalent CLI verb if Sean adds one later — DO NOT edit
   `sources.yaml`).
6. `log_decision` rationale MUST start with the verbatim string
   `no_registered_sources` followed by the gap query, the URLs ingested,
   the domains proposed for registration, and the resulting note paths.
   Future `search_memory` queries key off that prefix to find
   first-time-on-topic cases.
7. If the user cancels (no URLs ingested, no domains registered), write
   nothing — no notes, no pending entries, no decision.
