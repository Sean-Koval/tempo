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
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Iterable

import lancedb
import pyarrow as pa
import yaml
from fastembed import TextEmbedding

from .paths import data_dir, repo_root

_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
_EMBED_DIM = 384
_TABLE_NAME = "knowledge"
_CHUNK_WORDS = 500
_CHUNK_OVERLAP_WORDS = 50

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


def _open_table(vectors_dir: Path) -> lancedb.table.LanceTable:
    vectors_dir.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(vectors_dir))
    if _TABLE_NAME in db.table_names():
        return db.open_table(_TABLE_NAME)
    return db.create_table(_TABLE_NAME, schema=_schema())


def _existing_hashes(table: lancedb.table.LanceTable) -> dict[str, str]:
    """Return {path: file_hash} — one entry per indexed file (any chunk)."""
    if table.count_rows() == 0:
        return {}
    arr = table.to_arrow().select(["path", "file_hash"])
    out: dict[str, str] = {}
    for path, fh in zip(arr["path"].to_pylist(), arr["file_hash"].to_pylist(), strict=True):
        out[path] = fh
    return out


def _model() -> TextEmbedding:
    return TextEmbedding(model_name=_EMBED_MODEL)


def _chunks_to_rows(chunks: list[Chunk], model: TextEmbedding) -> list[dict]:
    if not chunks:
        return []
    vectors = list(model.embed([c.text for c in chunks]))
    return [
        {
            "id": c.id,
            "text": c.text,
            "vector": vec.tolist(),
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
) -> EmbedStats:
    """(Re)embed knowledge docs into ``knowledge.lance``.

    - ``paths``: limit to specific .md files (post-commit hook path).
    - ``force``: re-embed even if file hash matches.
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

    for md in _iter_targets(kroot, paths):
        stats.files_scanned += 1
        try:
            rel = md.relative_to(root).as_posix()
        except ValueError:
            rel = md.as_posix()
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

    model = _model()
    for md, chunks in files_to_embed:
        rel = chunks[0].path
        if rel in existing:
            deleted = table.count_rows(f"path = '{_sql_quote(rel)}'")
            table.delete(f"path = '{_sql_quote(rel)}'")
            stats.chunks_deleted += deleted
        rows = _chunks_to_rows(chunks, model)
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
) -> list[SearchHit]:
    """Semantic search against knowledge.lance.

    ``credibility_min``: keep only rows whose credibility rank is <= that of
    the given level (i.e., *at least as credible*). ``unvetted`` accepts all.
    """
    vdir = vectors_dir or (data_dir() / "vectors")
    db = lancedb.connect(str(vdir))
    if _TABLE_NAME not in db.table_names():
        return []
    table = db.open_table(_TABLE_NAME)
    if table.count_rows() == 0:
        return []

    model = _model()
    qvec = list(model.embed([query]))[0].tolist()

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
