"""Walk ``knowledge/`` and embed chunks into ``data/vectors/knowledge.lance``.

The retrieval substrate for coach-db's ``search_knowledge`` tool (Phase 3).
Kept intentionally small: one table, one model, one rebuild entry point.

Idempotency:
- Each row stores the source file's sha256. A file whose hash matches the
  currently-indexed hash is skipped.
- When a file changes, its existing rows are deleted by ``path`` before
  the new chunks are inserted. No stale chunks survive.

Credibility:
- Computed as the *weakest* (lowest-rank) credibility across the doc's
  ``sources`` frontmatter, looked up in ``knowledge/sources.yaml``.
- Any source id not in the registry promotes the whole doc to ``unvetted``
  — the agent should flag that in retrieval.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

import lancedb
import pyarrow as pa
import yaml

from .paths import data_dir, repo_root

Embedder = Callable[[list[str]], list[list[float]]]

_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
_EMBED_DIM = 384
_TABLE_NAME = "knowledge"
_MEMORY_TABLE_NAME = "memory"
_SESSIONS_TABLE_NAME = "sessions"
_CHUNK_WORDS = 500
_CHUNK_OVERLAP_WORDS = 50

_SPORT_SECTION_RE = re.compile(r"^##\s+(?P<heading>.+?)\s*$", re.MULTILINE)
_SESSION_HEADING_RE = re.compile(r"^###\s+`(?P<id>[a-z0-9_]+)`\s*$", re.MULTILINE)
_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[–-]\s*(\d+(?:\.\d+)?)")
_SINGLE_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)")
_SPORT_CANONICAL = {
    "swim": "swim",
    "bike": "bike",
    "ride": "bike",
    "run": "run",
    "brick & combined": "brick",
    "brick": "brick",
    "strength": "strength",
}

_CHANGELOG_ENTRY_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_JOURNAL_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")

_CREDIBILITY_RANK = {
    "peer_reviewed": 1,
    "expert_practitioner": 2,
    "evidence_based_journalism": 3,
    "experiential": 4,
    "unvetted": 5,
}

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<yaml>.*?)\n---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    path: str
    topic: str
    credibility: str
    source_ids: list[str]
    phase: str
    chunk_idx: int
    file_hash: str


@dataclass
class EmbedStats:
    files_scanned: int = 0
    files_embedded: int = 0
    files_skipped: int = 0
    chunks_written: int = 0
    chunks_deleted: int = 0
    duration_ms: int = 0
    paths_indexed: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SearchHit:
    id: str
    text: str
    path: str
    topic: str
    credibility: str
    source_ids: list[str]
    phase: str
    score: float


@dataclass(frozen=True)
class MemoryRow:
    id: str
    text: str
    source: str
    scope: str
    kind: str
    timestamp: str
    file_path: str
    chunk_idx: int
    entry_hash: str
    key: str


@dataclass
class MemoryEmbedStats:
    sources_scanned: int = 0
    sources_embedded: int = 0
    sources_skipped: int = 0
    rows_written: int = 0
    rows_deleted: int = 0
    duration_ms: int = 0


@dataclass(frozen=True)
class MemoryHit:
    id: str
    text: str
    source: str
    scope: str
    kind: str
    timestamp: str
    file_path: str
    score: float


@dataclass(frozen=True)
class SessionEntry:
    id: str
    text: str
    sport: str
    duration_min_lo: int | None
    duration_min_hi: int | None
    tss_lo: int | None
    tss_hi: int | None
    purpose: str
    file_hash: str


@dataclass
class SessionEmbedStats:
    entries_scanned: int = 0
    entries_embedded: int = 0
    entries_skipped: int = 0
    rows_deleted: int = 0
    duration_ms: int = 0


@dataclass(frozen=True)
class SessionMatch:
    id: str
    text: str
    sport: str
    purpose: str
    duration_min_lo: int | None
    duration_min_hi: int | None
    tss_lo: int | None
    tss_hi: int | None
    score: float


def _load_sources(sources_yaml: Path) -> dict[str, str]:
    if not sources_yaml.is_file():
        return {}
    with sources_yaml.open(encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    out: dict[str, str] = {}
    for s in doc.get("sources", []) or []:
        if isinstance(s, dict) and "id" in s:
            out[str(s["id"])] = str(s.get("credibility", "experiential"))
    return out


def _parse_doc(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm = yaml.safe_load(m.group("yaml")) or {}
    return fm if isinstance(fm, dict) else {}, m.group("body")


def _chunk_body(
    body: str,
    words: int = _CHUNK_WORDS,
    overlap: int = _CHUNK_OVERLAP_WORDS,
) -> list[str]:
    tokens = body.split()
    if not tokens:
        return []
    step = max(1, words - overlap)
    chunks: list[str] = []
    i = 0
    while i < len(tokens):
        chunks.append(" ".join(tokens[i : i + words]))
        if i + words >= len(tokens):
            break
        i += step
    return chunks


def _resolve_credibility(sources: list[str], registry: dict[str, str]) -> str:
    if not sources:
        return "unvetted"
    worst_rank = 0
    worst_label = "peer_reviewed"
    for sid in sources:
        cred = registry.get(sid)
        if cred is None:
            return "unvetted"
        rank = _CREDIBILITY_RANK.get(cred, 5)
        if rank > worst_rank:
            worst_rank = rank
            worst_label = cred
    return worst_label


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _enumerate_chunks(
    path: Path,
    rel: str,
    registry: dict[str, str],
) -> list[Chunk]:
    fm, body = _parse_doc(path)
    chunk_texts = _chunk_body(body)
    if not chunk_texts:
        return []
    topic = fm.get("topic", "")
    phase = fm.get("phase") or fm.get("phases") or ""
    if isinstance(phase, list):
        phase = ",".join(str(p) for p in phase)
    sources_raw = fm.get("sources") or []
    sources = [str(s) for s in sources_raw] if isinstance(sources_raw, list) else []
    credibility = _resolve_credibility(sources, registry)
    file_hash = _file_hash(path)
    return [
        Chunk(
            id=f"{rel}#{i}",
            text=text,
            path=rel,
            topic=str(topic),
            credibility=credibility,
            source_ids=sources,
            phase=str(phase),
            chunk_idx=i,
            file_hash=file_hash,
        )
        for i, text in enumerate(chunk_texts)
    ]


def _schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("id", pa.string()),
            pa.field("text", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), _EMBED_DIM)),
            pa.field("path", pa.string()),
            pa.field("topic", pa.string()),
            pa.field("credibility", pa.string()),
            pa.field("source_ids", pa.list_(pa.string())),
            pa.field("phase", pa.string()),
            pa.field("chunk_idx", pa.int32()),
            pa.field("file_hash", pa.string()),
        ]
    )


def _list_table_names(db: lancedb.DBConnection) -> list[str]:
    """LanceDB 0.30's ``list_tables`` returns a response wrapper, not a list."""
    result: Any = db.list_tables()
    if hasattr(result, "tables"):
        return [str(x) for x in result.tables]
    return [str(x) for x in result]


def _open_table(vectors_dir: Path) -> Any:
    vectors_dir.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(vectors_dir))
    if _TABLE_NAME in _list_table_names(db):
        return db.open_table(_TABLE_NAME)
    return db.create_table(_TABLE_NAME, schema=_schema())


def _existing_hashes(table: Any) -> dict[str, str]:
    """Return {path: file_hash} — one entry per indexed file (any chunk)."""
    if table.count_rows() == 0:
        return {}
    arr = table.to_arrow().select(["path", "file_hash"])
    out: dict[str, str] = {}
    for path, fh in zip(arr["path"].to_pylist(), arr["file_hash"].to_pylist(), strict=True):
        out[path] = fh
    return out


def _default_embedder() -> Embedder:
    """Lazy-loaded fastembed BGE-small embedder. Downloads on first call."""
    from fastembed import TextEmbedding

    model = TextEmbedding(model_name=_EMBED_MODEL)

    def _embed(texts: list[str]) -> list[list[float]]:
        return [vec.tolist() for vec in model.embed(texts)]

    return _embed


def _chunks_to_rows(chunks: list[Chunk], embedder: Embedder) -> list[dict]:
    if not chunks:
        return []
    vectors = embedder([c.text for c in chunks])
    return [
        {
            "id": c.id,
            "text": c.text,
            "vector": vec,
            "path": c.path,
            "topic": c.topic,
            "credibility": c.credibility,
            "source_ids": list(c.source_ids),
            "phase": c.phase,
            "chunk_idx": c.chunk_idx,
            "file_hash": c.file_hash,
        }
        for c, vec in zip(chunks, vectors, strict=True)
    ]


def _iter_targets(knowledge_root: Path, paths: list[Path] | None) -> Iterable[Path]:
    if paths is not None:
        for p in paths:
            p = p.resolve()
            if p.is_file() and p.suffix == ".md":
                yield p
        return
    yield from sorted(knowledge_root.rglob("*.md"))


def rebuild(
    knowledge_root: Path | None = None,
    vectors_dir: Path | None = None,
    paths: list[Path] | None = None,
    force: bool = False,
    embedder: Embedder | None = None,
) -> EmbedStats:
    """(Re)embed knowledge docs into ``knowledge.lance``.

    - ``paths``: limit to specific .md files (post-commit hook path).
    - ``force``: re-embed even if file hash matches.
    - ``embedder``: optional callable ``[str] -> [[float]]`` for tests /
      custom embedding backends. Defaults to fastembed BGE-small.
    """
    t0 = perf_counter()
    root = repo_root()
    kroot = knowledge_root or (root / "knowledge")
    vdir = vectors_dir or (data_dir() / "vectors")

    registry = _load_sources(kroot / "sources.yaml")
    table = _open_table(vdir)
    existing = _existing_hashes(table)

    stats = EmbedStats()
    files_to_embed: list[tuple[Path, list[Chunk]]] = []
    rel_base = kroot.parent.resolve()

    for md in _iter_targets(kroot, paths):
        stats.files_scanned += 1
        md_res = md.resolve()
        try:
            rel = md_res.relative_to(rel_base).as_posix()
        except ValueError:
            rel = md_res.as_posix()
        fh = _file_hash(md)
        if not force and existing.get(rel) == fh:
            stats.files_skipped += 1
            continue
        chunks = _enumerate_chunks(md, rel, registry)
        if not chunks:
            continue
        files_to_embed.append((md, chunks))

    if not files_to_embed:
        stats.duration_ms = int((perf_counter() - t0) * 1000)
        return stats

    embed_fn = embedder or _default_embedder()
    for _md, chunks in files_to_embed:
        rel = chunks[0].path
        if rel in existing:
            deleted = table.count_rows(f"path = '{_sql_quote(rel)}'")
            table.delete(f"path = '{_sql_quote(rel)}'")
            stats.chunks_deleted += deleted
        rows = _chunks_to_rows(chunks, embed_fn)
        table.add(rows)
        stats.files_embedded += 1
        stats.chunks_written += len(rows)
        stats.paths_indexed.append(rel)

    stats.duration_ms = int((perf_counter() - t0) * 1000)
    return stats


def _sql_quote(s: str) -> str:
    """Escape a single-quoted SQL string literal."""
    return s.replace("'", "''")


def search(
    query: str,
    k: int = 5,
    topic: str | None = None,
    credibility_min: str | None = None,
    vectors_dir: Path | None = None,
    embedder: Embedder | None = None,
) -> list[SearchHit]:
    """Semantic search against knowledge.lance.

    ``credibility_min``: keep only rows whose credibility rank is <= that of
    the given level (i.e., *at least as credible*). ``unvetted`` accepts all.
    """
    vdir = vectors_dir or (data_dir() / "vectors")
    db = lancedb.connect(str(vdir))
    if _TABLE_NAME not in _list_table_names(db):
        return []
    table = db.open_table(_TABLE_NAME)
    if table.count_rows() == 0:
        return []

    embed_fn = embedder or _default_embedder()
    qvec = embed_fn([query])[0]

    q = table.search(qvec).limit(k * 4 if topic or credibility_min else k)
    if topic:
        q = q.where(f"topic = '{_sql_quote(topic)}'")
    rows = q.to_list()

    min_rank = _CREDIBILITY_RANK.get(credibility_min, 5) if credibility_min else 5
    hits: list[SearchHit] = []
    for r in rows:
        rank = _CREDIBILITY_RANK.get(r.get("credibility", "unvetted"), 5)
        if rank > min_rank:
            continue
        dist = float(r.get("_distance", 0.0))
        hits.append(
            SearchHit(
                id=r["id"],
                text=r["text"],
                path=r["path"],
                topic=r.get("topic", ""),
                credibility=r.get("credibility", "unvetted"),
                source_ids=list(r.get("source_ids", []) or []),
                phase=r.get("phase", ""),
                score=1.0 - min(dist, 2.0) / 2.0,
            )
        )
        if len(hits) >= k:
            break
    return hits


# ---------------------------------------------------------------------------
# memory.lance — decisions + journals + changelog entries
# ---------------------------------------------------------------------------


def _memory_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("id", pa.string()),
            pa.field("text", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), _EMBED_DIM)),
            pa.field("source", pa.string()),
            pa.field("scope", pa.string()),
            pa.field("kind", pa.string()),
            pa.field("timestamp", pa.string()),
            pa.field("file_path", pa.string()),
            pa.field("chunk_idx", pa.int32()),
            pa.field("entry_hash", pa.string()),
            pa.field("key", pa.string()),
        ]
    )


def _open_memory_table(vectors_dir: Path) -> Any:
    vectors_dir.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(vectors_dir))
    if _MEMORY_TABLE_NAME in _list_table_names(db):
        return db.open_table(_MEMORY_TABLE_NAME)
    return db.create_table(_MEMORY_TABLE_NAME, schema=_memory_schema())


def _existing_memory_hashes(table: Any) -> dict[str, str]:
    """Return ``{key: entry_hash}`` across the memory table.

    ``key`` identifies the source (file path for journals/changelogs,
    ``decision:{id}`` for decisions). All rows sharing a key also share an
    entry_hash; we read the first one.
    """
    if table.count_rows() == 0:
        return {}
    arr = table.to_arrow().select(["key", "entry_hash"])
    out: dict[str, str] = {}
    for key, eh in zip(arr["key"].to_pylist(), arr["entry_hash"].to_pylist(), strict=True):
        out.setdefault(key, eh)
    return out


def _iter_decision_rows(conn: sqlite3.Connection) -> Iterable[MemoryRow]:
    cur = conn.execute(
        "SELECT id, timestamp, scope, kind, rationale FROM decisions ORDER BY id"
    )
    for row in cur.fetchall():
        text = str(row["rationale"])
        entry_hash = hashlib.sha256(
            f"{row['timestamp']}|{row['scope']}|{row['kind']}|{text}".encode()
        ).hexdigest()
        yield MemoryRow(
            id=f"decision:{row['id']}",
            text=text,
            source="decision",
            scope=str(row["scope"]),
            kind=str(row["kind"]),
            timestamp=str(row["timestamp"]),
            file_path="",
            chunk_idx=0,
            entry_hash=entry_hash,
            key=f"decision:{row['id']}",
        )


def _iter_journal_rows(journal_root: Path, rel_base: Path) -> Iterable[MemoryRow]:
    if not journal_root.is_dir():
        return
    for md in sorted(journal_root.rglob("*.md")):
        stem = md.stem
        date_m = _JOURNAL_DATE_RE.match(stem)
        iso_date = date_m.group(1) if date_m else ""
        scope = f"journal:{iso_date}" if iso_date else "journal"
        file_hash = _file_hash(md)
        rel = _rel_posix(md, rel_base)
        body = md.read_text(encoding="utf-8")
        chunks = _chunk_body(body)
        if not chunks:
            continue
        for idx, chunk_text in enumerate(chunks):
            yield MemoryRow(
                id=f"journal:{rel}#{idx}",
                text=chunk_text,
                source="journal",
                scope=scope,
                kind="",
                timestamp=iso_date,
                file_path=rel,
                chunk_idx=idx,
                entry_hash=file_hash,
                key=f"journal:{rel}",
            )


def _iter_changelog_rows(plans_root: Path, rel_base: Path) -> Iterable[MemoryRow]:
    if not plans_root.is_dir():
        return
    for changelog in sorted(plans_root.glob("*/changelog.md")):
        plan_id = changelog.parent.name
        body = changelog.read_text(encoding="utf-8")
        file_hash = _file_hash(changelog)
        rel = _rel_posix(changelog, rel_base)
        # Split on ## headings; each section becomes one row.
        matches = list(_CHANGELOG_ENTRY_RE.finditer(body))
        if not matches:
            continue
        for idx, m in enumerate(matches):
            heading = m.group(1).strip()
            start = m.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
            section_body = body[start:end].strip()
            text = f"{heading}\n\n{section_body}" if section_body else heading
            yield MemoryRow(
                id=f"changelog:{plan_id}#{idx}",
                text=text,
                source="changelog",
                scope=f"plan:{plan_id}",
                kind="",
                timestamp="",
                file_path=rel,
                chunk_idx=idx,
                entry_hash=file_hash,
                key=f"changelog:{rel}",
            )


def _rel_posix(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _memory_row_to_dict(row: MemoryRow, vec: list[float]) -> dict:
    return {
        "id": row.id,
        "text": row.text,
        "vector": vec,
        "source": row.source,
        "scope": row.scope,
        "kind": row.kind,
        "timestamp": row.timestamp,
        "file_path": row.file_path,
        "chunk_idx": row.chunk_idx,
        "entry_hash": row.entry_hash,
        "key": row.key,
    }


def rebuild_memory(
    journal_root: Path | None = None,
    plans_root: Path | None = None,
    decisions_conn: sqlite3.Connection | None = None,
    vectors_dir: Path | None = None,
    force: bool = False,
    embedder: Embedder | None = None,
) -> MemoryEmbedStats:
    """(Re)embed decisions + journals + changelog entries into ``memory.lance``.

    Each source has a stable ``key`` (``decision:{id}`` / journal file path /
    changelog file path). Rows are grouped by key for idempotency: if the
    per-key ``entry_hash`` matches the indexed one we skip the whole source;
    otherwise we delete all rows with that key and re-insert fresh ones.
    """
    t0 = perf_counter()
    root = repo_root()
    jroot = journal_root if journal_root is not None else (root / "journal")
    proot = plans_root if plans_root is not None else (root / "plans")
    vdir = vectors_dir or (data_dir() / "vectors")
    rel_base = root.resolve()

    owns_conn = decisions_conn is None
    if decisions_conn is None:
        from .db import connect, init_schema

        conn = connect()
        init_schema(conn)
    else:
        conn = decisions_conn

    try:
        table = _open_memory_table(vdir)
        existing = _existing_memory_hashes(table)

        # Group pending rows by source key so we delete+re-insert atomically per key.
        pending: dict[str, list[MemoryRow]] = {}
        sources: list[MemoryRow] = []
        sources.extend(_iter_decision_rows(conn))
        sources.extend(_iter_journal_rows(jroot, rel_base))
        sources.extend(_iter_changelog_rows(proot, rel_base))

        for row in sources:
            pending.setdefault(row.key, []).append(row)

        stats = MemoryEmbedStats(sources_scanned=len(pending))
        to_embed: list[list[MemoryRow]] = []
        for key, rows in pending.items():
            existing_hash = existing.get(key)
            new_hash = rows[0].entry_hash
            if not force and existing_hash == new_hash:
                stats.sources_skipped += 1
                continue
            to_embed.append(rows)

        if not to_embed:
            stats.duration_ms = int((perf_counter() - t0) * 1000)
            return stats

        embed_fn = embedder or _default_embedder()
        for rows in to_embed:
            key = rows[0].key
            if key in existing:
                deleted = table.count_rows(f"key = '{_sql_quote(key)}'")
                table.delete(f"key = '{_sql_quote(key)}'")
                stats.rows_deleted += deleted
            vectors = embed_fn([r.text for r in rows])
            dicts = [_memory_row_to_dict(r, v) for r, v in zip(rows, vectors, strict=True)]
            table.add(dicts)
            stats.sources_embedded += 1
            stats.rows_written += len(dicts)

        stats.duration_ms = int((perf_counter() - t0) * 1000)
        return stats
    finally:
        if owns_conn:
            conn.close()


def search_memory(
    query: str,
    k: int = 5,
    since: str | None = None,
    scope: str | None = None,
    kind: str | None = None,
    vectors_dir: Path | None = None,
    embedder: Embedder | None = None,
) -> list[MemoryHit]:
    """Semantic search against memory.lance.

    - ``since``: ISO date; keeps hits with ``timestamp >= since`` (lex-compare
      works because timestamps are ISO).
    - ``scope``: exact-prefix match on the ``scope`` column (e.g. ``week:``).
    - ``kind``: exact match on ``decisions.kind`` (only meaningful for
      ``source='decision'`` rows).
    """
    vdir = vectors_dir or (data_dir() / "vectors")
    db = lancedb.connect(str(vdir))
    if _MEMORY_TABLE_NAME not in _list_table_names(db):
        return []
    table = db.open_table(_MEMORY_TABLE_NAME)
    if table.count_rows() == 0:
        return []

    embed_fn = embedder or _default_embedder()
    qvec = embed_fn([query])[0]

    overfetch = k * 4 if (since or scope or kind) else k
    q = table.search(qvec).limit(overfetch)
    if kind:
        q = q.where(f"kind = '{_sql_quote(kind)}'")
    rows = q.to_list()

    out: list[MemoryHit] = []
    for r in rows:
        ts = str(r.get("timestamp") or "")
        sc = str(r.get("scope") or "")
        if since and ts and ts < since:
            continue
        if scope and not sc.startswith(scope):
            continue
        dist = float(r.get("_distance", 0.0))
        out.append(
            MemoryHit(
                id=str(r["id"]),
                text=str(r["text"]),
                source=str(r.get("source") or ""),
                scope=sc,
                kind=str(r.get("kind") or ""),
                timestamp=ts,
                file_path=str(r.get("file_path") or ""),
                score=1.0 - min(dist, 2.0) / 2.0,
            )
        )
        if len(out) >= k:
            break
    return out


def embed_single_decision(
    decision_id: int,
    scope: str,
    kind: str,
    rationale: str,
    timestamp: str,
    vectors_dir: Path | None = None,
    embedder: Embedder | None = None,
) -> bool:
    """Append a single decision row to memory.lance. Returns True on success.

    Used by ``coach-db.log_decision`` to keep newly-logged decisions
    searchable within the same session without a full rebuild.
    """
    vdir = vectors_dir or (data_dir() / "vectors")
    table = _open_memory_table(vdir)
    entry_hash = hashlib.sha256(
        f"{timestamp}|{scope}|{kind}|{rationale}".encode()
    ).hexdigest()
    key = f"decision:{decision_id}"
    # Idempotent on repeated calls.
    if table.count_rows(f"key = '{_sql_quote(key)}'"):
        table.delete(f"key = '{_sql_quote(key)}'")
    embed_fn = embedder or _default_embedder()
    vec = embed_fn([rationale])[0]
    row = MemoryRow(
        id=key,
        text=rationale,
        source="decision",
        scope=scope,
        kind=kind,
        timestamp=timestamp,
        file_path="",
        chunk_idx=0,
        entry_hash=entry_hash,
        key=key,
    )
    table.add([_memory_row_to_dict(row, vec)])
    return True


# ---------------------------------------------------------------------------
# sessions.lance — session library dedup / similar-session matching
# ---------------------------------------------------------------------------


def _sessions_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("id", pa.string()),
            pa.field("text", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), _EMBED_DIM)),
            pa.field("sport", pa.string()),
            pa.field("purpose", pa.string()),
            pa.field("duration_min_lo", pa.int32()),
            pa.field("duration_min_hi", pa.int32()),
            pa.field("tss_lo", pa.int32()),
            pa.field("tss_hi", pa.int32()),
            pa.field("file_hash", pa.string()),
        ]
    )


def _open_sessions_table(vectors_dir: Path) -> Any:
    vectors_dir.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(vectors_dir))
    if _SESSIONS_TABLE_NAME in _list_table_names(db):
        return db.open_table(_SESSIONS_TABLE_NAME)
    return db.create_table(_SESSIONS_TABLE_NAME, schema=_sessions_schema())


def _parse_session_line_range(line: str) -> tuple[float | None, float | None]:
    """Return (lo, hi) as floats. Caller converts units + int-rounds."""
    m = _RANGE_RE.search(line)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    m1 = _SINGLE_NUM_RE.search(line)
    if m1:
        v = float(m1.group(1))
        return (v, v)
    return (None, None)


def _parse_session_body(body: str) -> tuple[str, tuple[int | None, int | None], tuple[int | None, int | None]]:
    """Return (purpose, duration_min_range, tss_range) from a session's bullets."""
    purpose = ""
    dur: tuple[int | None, int | None] = (None, None)
    tss: tuple[int | None, int | None] = (None, None)
    for raw in body.splitlines():
        line = raw.strip()
        low = line.lower()
        if low.startswith("- **purpose:**"):
            purpose = line.split(":**", 1)[-1].strip() if ":**" in line else line
        elif low.startswith("- **duration:**"):
            lo_f, hi_f = _parse_session_line_range(line)
            multiplier = 60.0 if "hour" in low else 1.0
            lo = int(round(lo_f * multiplier)) if lo_f is not None else None
            hi = int(round(hi_f * multiplier)) if hi_f is not None else None
            dur = (lo, hi)
        elif low.startswith("- **tss:**"):
            lo_f, hi_f = _parse_session_line_range(line)
            lo = int(round(lo_f)) if lo_f is not None else None
            hi = int(round(hi_f)) if hi_f is not None else None
            tss = (lo, hi)
    return purpose, dur, tss


def _iter_session_entries(session_library: Path) -> Iterable[SessionEntry]:
    if not session_library.is_file():
        return
    text = session_library.read_text(encoding="utf-8")
    file_hash = _file_hash(session_library)

    # Map position → sport by walking ## headings.
    sport_spans: list[tuple[int, str]] = []  # (start_offset, canonical_sport)
    for m in _SPORT_SECTION_RE.finditer(text):
        heading = m.group("heading").strip().lower()
        if heading in _SPORT_CANONICAL:
            sport_spans.append((m.end(), _SPORT_CANONICAL[heading]))

    def _sport_at(pos: int) -> str:
        current = "unknown"
        for start, sport in sport_spans:
            if start <= pos:
                current = sport
            else:
                break
        return current

    matches = list(_SESSION_HEADING_RE.finditer(text))
    for idx, m in enumerate(matches):
        lib_id = m.group("id")
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        section_body = text[start:end].strip()
        purpose, dur, tss = _parse_session_body(section_body)
        sport = _sport_at(m.start())
        embed_text = (
            f"[{sport}] {lib_id}\n"
            f"Purpose: {purpose}\n\n"
            f"{section_body}"
        )
        yield SessionEntry(
            id=lib_id,
            text=embed_text,
            sport=sport,
            duration_min_lo=dur[0],
            duration_min_hi=dur[1],
            tss_lo=tss[0],
            tss_hi=tss[1],
            purpose=purpose,
            file_hash=file_hash,
        )


def _session_entry_to_dict(e: SessionEntry, vec: list[float]) -> dict:
    return {
        "id": e.id,
        "text": e.text,
        "vector": vec,
        "sport": e.sport,
        "purpose": e.purpose,
        "duration_min_lo": e.duration_min_lo if e.duration_min_lo is not None else -1,
        "duration_min_hi": e.duration_min_hi if e.duration_min_hi is not None else -1,
        "tss_lo": e.tss_lo if e.tss_lo is not None else -1,
        "tss_hi": e.tss_hi if e.tss_hi is not None else -1,
        "file_hash": e.file_hash,
    }


def rebuild_sessions(
    session_library: Path | None = None,
    vectors_dir: Path | None = None,
    force: bool = False,
    embedder: Embedder | None = None,
) -> SessionEmbedStats:
    """(Re)embed session-library.md into ``sessions.lance``.

    Idempotency is per-file: the whole library shares one file_hash. Matching
    hash → skip. Otherwise delete the table contents and re-insert.
    """
    t0 = perf_counter()
    root = repo_root()
    lib = session_library or (root / "knowledge" / "methodology" / "session-library.md")
    vdir = vectors_dir or (data_dir() / "vectors")

    table = _open_sessions_table(vdir)
    entries = list(_iter_session_entries(lib))
    stats = SessionEmbedStats(entries_scanned=len(entries))
    if not entries:
        stats.duration_ms = int((perf_counter() - t0) * 1000)
        return stats

    current_hash = entries[0].file_hash
    if not force and table.count_rows() > 0:
        existing_hash_row = table.to_arrow().select(["file_hash"]).to_pylist()
        existing_hash = existing_hash_row[0]["file_hash"] if existing_hash_row else ""
        if existing_hash == current_hash:
            stats.entries_skipped = len(entries)
            stats.duration_ms = int((perf_counter() - t0) * 1000)
            return stats

    if table.count_rows() > 0:
        deleted = table.count_rows()
        table.delete("file_hash IS NOT NULL")
        stats.rows_deleted += deleted

    embed_fn = embedder or _default_embedder()
    vectors = embed_fn([e.text for e in entries])
    rows = [_session_entry_to_dict(e, v) for e, v in zip(entries, vectors, strict=True)]
    table.add(rows)
    stats.entries_embedded = len(rows)
    stats.duration_ms = int((perf_counter() - t0) * 1000)
    return stats


def search_sessions(
    description: str,
    k: int = 3,
    sport: str | None = None,
    vectors_dir: Path | None = None,
    embedder: Embedder | None = None,
) -> list[SessionMatch]:
    """Semantic match against sessions.lance. ``sport`` is an exact filter."""
    vdir = vectors_dir or (data_dir() / "vectors")
    db = lancedb.connect(str(vdir))
    if _SESSIONS_TABLE_NAME not in _list_table_names(db):
        return []
    table = db.open_table(_SESSIONS_TABLE_NAME)
    if table.count_rows() == 0:
        return []

    embed_fn = embedder or _default_embedder()
    qvec = embed_fn([description])[0]

    q = table.search(qvec).limit(k * 4 if sport else k)
    if sport:
        q = q.where(f"sport = '{_sql_quote(sport)}'")
    rows = q.to_list()

    out: list[SessionMatch] = []
    for r in rows:
        dist = float(r.get("_distance", 0.0))
        out.append(
            SessionMatch(
                id=str(r["id"]),
                text=str(r["text"]),
                sport=str(r.get("sport") or ""),
                purpose=str(r.get("purpose") or ""),
                duration_min_lo=_none_if_neg(r.get("duration_min_lo")),
                duration_min_hi=_none_if_neg(r.get("duration_min_hi")),
                tss_lo=_none_if_neg(r.get("tss_lo")),
                tss_hi=_none_if_neg(r.get("tss_hi")),
                score=1.0 - min(dist, 2.0) / 2.0,
            )
        )
        if len(out) >= k:
            break
    return out


def _none_if_neg(v: Any) -> int | None:
    if v is None:
        return None
    iv = int(v)
    return None if iv < 0 else iv
