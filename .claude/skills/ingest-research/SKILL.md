---
name: ingest-research
description: Fetch a URL or local PDF, paraphrase into knowledge/research/YYYY/MM/<slug>.md with credibility-tagged frontmatter, and re-embed the corpus.
trigger: /ingest-research <url-or-path>
---

# Skill: ingest-research

You are extending Sean's coaching corpus. Given a URL or a local PDF path,
produce a paraphrased knowledge note under `knowledge/research/YYYY/MM/`,
tag it against `knowledge/sources.yaml`, and re-embed it into the vector
index so future planning sessions retrieve it.

**This skill ingests, it does not discover.** Sean picks the source. If
something is bad or off-topic, refuse and explain why — don't silently water
it down into a note.

## Step 1 — Run preflight

```bash
uv run python .claude/skills/ingest-research/preflight.py --source <url-or-path>
```

Read the JSON brief from stdout. If the script exits non-zero (404,
unsupported file type, parse failure), surface the error to the user and
stop — don't guess a fallback.

The brief contains: `source`, `source_kind` (`url|pdf`), `detected_title`,
`detected_authors`, `detected_date`, `word_count`, `excerpt` (first 800
chars), `source_sha256`, `matched_source` (registered entry from
sources.yaml or `null`), `suggested_slug`, `target_path`, `duplicate_of`.

## Step 2 — Bail on duplicates

If `duplicate_of` is set, the same source has already been ingested. Stop
and report the existing path. Do **not** re-ingest, do **not** overwrite.

## Step 3 — Read the excerpt critically

Before composing, read the `excerpt` and consider:

- Is this actually about endurance training / nutrition / physiology /
  recovery? Off-topic content (paywalled marketing pages, login walls, error
  pages) → refuse, don't ingest noise.
- Does the source pass the smell test? An obvious AI-generated content farm
  isn't worth ingesting even if it parses cleanly.

If the source isn't worth keeping, stop and tell Sean why. He chose it; he
gets to argue back.

## Step 4 — Compose the paraphrased note

Write to the brief's `target_path`. Create parent dirs as needed. Format:

```markdown
---
type: research
source: <full URL or "file: path/relative/to/repo">
credibility: <from matched_source.credibility OR 'unvetted'>
topic: [<3-6 short topic tags — reuse matched_source.topics where possible>]
phases: [<base|build|peak|taper|race-week — only those that apply>]
key_claims:
  - <bullet 1 — the actionable assertion, not the headline>
  - <bullet 2>
  - <3 to 8 total>
ingested: <YYYY-MM-DD from brief.ingested>
source_sha256: <from brief.source_sha256>
detected_title: <from brief.detected_title, may be null>
detected_authors: [<from brief.detected_authors>]
detected_date: <from brief.detected_date, may be null>
---

# <title in your own words — short, descriptive>

## Summary

<2–3 sentences. The single most important takeaway, paraphrased.>

## Key claims

- <Claim 1 — paraphrased, not quoted. Include the magnitude/context that
  makes it actionable: "polarized 80/20 outperforms threshold-heavy at
  similar TSS over a 12-wk block in already-trained athletes".>
- <Claim 2>
- <…>

## How this applies to Sean

<One paragraph. Tie this to current plan phase, athlete preferences, prior
decisions, or `athlete-tested.yaml` entries when relevant. If it conflicts
with athlete-tested.yaml on a fueling claim, flag the conflict — don't
silently override either side. If it doesn't apply, say "not directly
applicable to current phase but useful as <reason>".>

## Caveats

<What this source does NOT claim. Population it studied (elite vs.
recreational), any conflicts of interest, sample size if reported.
Useful when the agent retrieves this snippet 6 months from now and needs
to know how confidently to apply it.>
```

## Step 5 — Re-embed

```bash
uv run coach vectors rebuild --paths <target_path>
```

This updates `data/vectors/knowledge.lance`. The next `search_knowledge`
call will return chunks from the new note.

## Step 6 — Log the decision

Call the `coach-db` MCP `log_decision` tool:

- `scope`: `research:<slug>` (e.g. `research:jeukendrup-multi-transportable-carbs`)
- `kind`: `ingest`
- `rationale`: one paragraph — what was ingested, why it's relevant now,
  the most important key_claim. Reference `source_sha256` from the brief.
- `changed_files`: `[<target_path>]`

## Invariants (enforce — do not bend)

- **Paraphrase only.** Direct quotes are rare and must be `> blockquoted`
  with attribution. Wholesale copying is a copyright violation and a
  retrieval anti-pattern (the index ends up indexing the source's voice
  instead of the actionable claim).
- **Every required frontmatter field must be set.** Missing
  `credibility`/`topic`/`key_claims` → don't write the file.
- **Unvetted sources stay unvetted.** Don't promote credibility to make
  retrieval cleaner. If the source isn't in `sources.yaml`, set
  `credibility: unvetted` and tell Sean — he can register it via
  `knowledge/sources.yaml` if he wants future retrievals to trust it more.
- **Duplicates are never re-ingested.** Honor `duplicate_of` from the brief.
- **Athlete-tested overrides literature.** If a key_claim contradicts an
  entry in `knowledge/nutrition/athlete-tested.yaml`, surface the conflict
  in the "How this applies to Sean" section. Do not silently defer to the
  literature — Sean's gut wins.

## Verification

- `/ingest-research <url>` produces `knowledge/research/YYYY/MM/<slug>.md`
  with all frontmatter fields populated.
- `uv run coach vectors search "<topic from key_claims>"` returns the new
  note in the top results.
- Re-running `/ingest-research <same-url>` reports duplicate and exits
  without writing a second copy.
