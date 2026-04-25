"""Source extraction + duplicate detection for the ingest-research skill.

Boundary: this module touches the network (HTTP fetches), the filesystem
(reads PDFs and existing knowledge notes), and parses HTML — but it does NOT
compose markdown. Composition is the agent's job in SKILL.md.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from .paths import repo_root

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WS_RE = re.compile(r"\s+")
_USER_AGENT = "tempo-coach/0.1 (+https://github.com/Sean-Koval/tempo)"


@dataclass
class ExtractedSource:
    """What we pulled out of a URL or PDF before the agent paraphrases it."""

    text: str
    sha256: str
    detected_title: str | None = None
    detected_authors: list[str] = field(default_factory=list)
    detected_date: str | None = None
    word_count: int = 0
    excerpt: str = ""


@dataclass
class MatchedSource:
    """A registered entry from knowledge/sources.yaml."""

    id: str
    name: str
    credibility: str
    topics: list[str]
    type: str | None = None


def slugify(text: str, *, max_len: int = 80) -> str:
    """Lower-kebab a string for use as a filesystem slug."""
    if not text:
        return "untitled"
    cleaned = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    if not cleaned:
        return "untitled"
    return cleaned[:max_len].rstrip("-") or "untitled"


def fetch_url(url: str, *, timeout: float = 30.0) -> tuple[str, str]:
    """Return (html_text, content_type) for a URL. Network call."""
    import httpx

    headers = {"User-Agent": _USER_AGENT}
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
        resp = client.get(url)
    resp.raise_for_status()
    ctype = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    return resp.text, ctype


def extract_html(html: str) -> ExtractedSource:
    """Naive readable-text extraction. Stripped of script/style/nav junk."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside"]):
        tag.decompose()

    title_tag = soup.find("title")
    detected_title = (title_tag.get_text(strip=True) if title_tag else None) or None

    detected_authors: list[str] = []
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or meta.get("property") or "").lower()  # type: ignore[union-attr]
        if name in {"author", "article:author", "dc.creator"}:
            content = meta.get("content")  # type: ignore[union-attr]
            if isinstance(content, str) and content.strip():
                detected_authors.append(content.strip())

    detected_date = None
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or meta.get("property") or "").lower()  # type: ignore[union-attr]
        if name in {"article:published_time", "datepublished", "date", "dc.date"}:
            content = meta.get("content")  # type: ignore[union-attr]
            if isinstance(content, str) and content.strip():
                detected_date = content.strip()[:10]
                break

    body = soup.find("article") or soup.find("main") or soup.body or soup
    text = _WS_RE.sub(" ", body.get_text(" ", strip=True))

    return _finalize(text, detected_title, detected_authors, detected_date)


def extract_pdf(path: Path) -> ExtractedSource:
    """Extract text + metadata from a local PDF."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    chunks = [page.extract_text() or "" for page in reader.pages]
    text = _WS_RE.sub(" ", " ".join(chunks)).strip()

    info: Any = reader.metadata
    title_raw = getattr(info, "title", None) if info else None
    author_raw = getattr(info, "author", None) if info else None
    detected_title = (title_raw or "").strip() or path.stem
    raw_author = (author_raw or "").strip()
    authors = [a.strip() for a in re.split(r"[;,]", raw_author) if a.strip()]
    raw_date = None
    if info is not None:
        raw_date = getattr(info, "creation_date", None) or getattr(
            info, "modification_date", None
        )
    detected_date = raw_date.date().isoformat() if raw_date else None

    return _finalize(text, detected_title, authors, detected_date)


def _finalize(
    text: str,
    title: str | None,
    authors: list[str],
    date: str | None,
) -> ExtractedSource:
    word_count = len(text.split())
    excerpt = text[:800]
    sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ExtractedSource(
        text=text,
        sha256=sha256,
        detected_title=title,
        detected_authors=authors,
        detected_date=date,
        word_count=word_count,
        excerpt=excerpt,
    )


def load_sources_yaml(*, root: Path | None = None) -> list[dict[str, Any]]:
    """Read knowledge/sources.yaml. Returns the ``sources`` list (may be empty)."""
    path = (root or repo_root()) / "knowledge" / "sources.yaml"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    return doc.get("sources") or []


def match_source(
    *,
    url: str | None,
    title: str | None,
    sources: list[dict[str, Any]],
) -> MatchedSource | None:
    """Best-effort match of a URL/title against the sources registry.

    Strategy:
      1. Domain hit — registered ``url`` field (rare; not all sources have one).
      2. Name token overlap with the page title (case-insensitive).
      3. None.
    """
    domain = _domain_of(url) if url else None

    for src in sources:
        registered_url = src.get("url")
        if domain and registered_url and domain in registered_url.lower():
            return _to_match(src)

    if title:
        norm_title = title.lower()
        for src in sources:
            name = (src.get("name") or "").lower()
            if not name:
                continue
            tokens = [t for t in re.split(r"[^a-z0-9]+", name) if len(t) >= 4]
            if tokens and any(tok in norm_title for tok in tokens):
                return _to_match(src)

    if domain:
        for src in sources:
            name = (src.get("name") or "").lower()
            if domain.split(".", 1)[0] in name:
                return _to_match(src)

    return None


def _to_match(src: dict[str, Any]) -> MatchedSource:
    return MatchedSource(
        id=str(src.get("id") or ""),
        name=str(src.get("name") or ""),
        credibility=str(src.get("credibility") or "unvetted"),
        topics=list(src.get("topics") or []),
        type=src.get("type"),
    )


def _domain_of(url: str) -> str | None:
    try:
        host = urlparse(url).hostname
    except ValueError:
        return None
    if not host:
        return None
    return host.removeprefix("www.").lower()


def find_duplicate(
    sha256: str, *, root: Path | None = None
) -> Path | None:
    """Scan knowledge/research/ for a note whose frontmatter has matching source_sha256."""
    base = (root or repo_root()) / "knowledge" / "research"
    if not base.is_dir():
        return None
    for md_path in base.rglob("*.md"):
        try:
            head = md_path.read_text(encoding="utf-8")[:2048]
        except OSError:
            continue
        m = _FRONTMATTER_RE.match(head)
        if not m:
            continue
        try:
            front = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            continue
        if isinstance(front, dict) and front.get("source_sha256") == sha256:
            return md_path
    return None


def target_path_for(
    *,
    slug: str,
    detected_date: str | None,
    today_iso: str,
    root: Path | None = None,
) -> Path:
    """``knowledge/research/YYYY/MM/<slug>.md`` keyed on detected_date or today."""
    iso = detected_date or today_iso
    year, month = iso[:4], iso[5:7]
    return (root or repo_root()) / "knowledge" / "research" / year / month / f"{slug}.md"


__all__ = [
    "ExtractedSource",
    "MatchedSource",
    "extract_html",
    "extract_pdf",
    "fetch_url",
    "find_duplicate",
    "load_sources_yaml",
    "match_source",
    "slugify",
    "target_path_for",
]
