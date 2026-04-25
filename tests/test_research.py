"""Tests for tempo.research — source extraction + duplicate detection (tempo-7ue.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tempo import research

SAMPLE_HTML = """\
<!doctype html>
<html>
<head>
  <title>Multi-transportable carbohydrates and endurance</title>
  <meta name="author" content="Asker Jeukendrup">
  <meta property="article:published_time" content="2014-05-12T08:00:00Z">
  <style>.x { color: red; }</style>
  <script>console.log('noise');</script>
</head>
<body>
  <header>nav junk</header>
  <article>
    <p>Combining glucose and fructose at a 2:1 ratio raises in-race
    carbohydrate oxidation rates above the single-transporter ceiling
    of about 60g/hr.</p>
    <p>Athletes can target 80–100g/hr with multi-transportable carbs
    after gut-training.</p>
  </article>
  <footer>(c) 2014 mysportscience</footer>
</body>
</html>
"""


def test_slugify_basic() -> None:
    assert research.slugify("Multi-Transportable Carbohydrates!") == "multi-transportable-carbohydrates"


def test_slugify_empty_falls_back() -> None:
    assert research.slugify("") == "untitled"
    assert research.slugify("   ---  ") == "untitled"


def test_slugify_truncates() -> None:
    long = "a" * 200
    out = research.slugify(long, max_len=20)
    assert len(out) <= 20


def test_extract_html_pulls_title_author_date_and_text() -> None:
    extracted = research.extract_html(SAMPLE_HTML)
    assert extracted.detected_title == "Multi-transportable carbohydrates and endurance"
    assert "Jeukendrup" in extracted.detected_authors[0]
    assert extracted.detected_date == "2014-05-12"
    assert "glucose and fructose" in extracted.text
    # Script + style stripped.
    assert "console.log" not in extracted.text
    assert ".x { color: red" not in extracted.text
    assert extracted.word_count > 5
    assert extracted.excerpt
    assert len(extracted.sha256) == 64


def test_extract_html_deterministic_sha() -> None:
    a = research.extract_html(SAMPLE_HTML)
    b = research.extract_html(SAMPLE_HTML)
    assert a.sha256 == b.sha256


def test_match_source_by_title_token(tmp_path: Path) -> None:
    sources = [
        {
            "id": "jeukendrup-mysportscience",
            "name": "Asker Jeukendrup — mysportscience.com",
            "credibility": "peer_reviewed",
            "topics": ["nutrition", "carb_loading"],
        },
        {
            "id": "friel-blog",
            "name": "Joe Friel's Blog",
            "credibility": "expert_practitioner",
            "topics": ["periodization"],
        },
    ]
    matched = research.match_source(
        url=None,
        title="Asker Jeukendrup on multi-transportable carbs",
        sources=sources,
    )
    assert matched is not None
    assert matched.id == "jeukendrup-mysportscience"


def test_match_source_unknown_returns_none() -> None:
    matched = research.match_source(
        url="https://random-marketing-blog.example/post",
        title="10 Tips for Faster Marathons",
        sources=[
            {
                "id": "friel-blog",
                "name": "Joe Friel's Blog",
                "credibility": "expert_practitioner",
                "topics": ["periodization"],
            }
        ],
    )
    assert matched is None


def test_target_path_uses_detected_date(tmp_path: Path) -> None:
    path = research.target_path_for(
        slug="my-note",
        detected_date="2024-03-09",
        today_iso="2026-04-24",
        root=tmp_path,
    )
    assert path == tmp_path / "knowledge" / "research" / "2024" / "03" / "my-note.md"


def test_target_path_falls_back_to_today(tmp_path: Path) -> None:
    path = research.target_path_for(
        slug="my-note",
        detected_date=None,
        today_iso="2026-04-24",
        root=tmp_path,
    )
    assert path == tmp_path / "knowledge" / "research" / "2026" / "04" / "my-note.md"


def test_find_duplicate_matches_sha(tmp_path: Path) -> None:
    note_dir = tmp_path / "knowledge" / "research" / "2024" / "03"
    note_dir.mkdir(parents=True)
    target = note_dir / "earlier.md"
    target.write_text(
        "---\n"
        "type: research\n"
        "source_sha256: deadbeef\n"
        "---\n\n"
        "# Earlier\n",
        encoding="utf-8",
    )
    found = research.find_duplicate("deadbeef", root=tmp_path)
    assert found == target

    assert research.find_duplicate("nope", root=tmp_path) is None


def test_find_duplicate_handles_no_research_dir(tmp_path: Path) -> None:
    assert research.find_duplicate("anything", root=tmp_path) is None


def test_find_duplicate_skips_invalid_yaml(tmp_path: Path) -> None:
    note_dir = tmp_path / "knowledge" / "research" / "2024" / "03"
    note_dir.mkdir(parents=True)
    (note_dir / "broken.md").write_text(
        "---\n: not valid yaml :::\n---\n# x\n", encoding="utf-8"
    )
    assert research.find_duplicate("anything", root=tmp_path) is None


def test_load_sources_yaml_missing_returns_empty(tmp_path: Path) -> None:
    assert research.load_sources_yaml(root=tmp_path) == []


def test_extract_pdf_reads_text_and_metadata(tmp_path: Path) -> None:
    """Round-trip through pypdf — write a tiny PDF, read it back."""
    pypdf = pytest.importorskip("pypdf")
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_metadata(
        {
            "/Title": "Test Periodization Article",
            "/Author": "Joe Friel; J. Daniels",
        }
    )
    pdf_path = tmp_path / "sample.pdf"
    with pdf_path.open("wb") as f:
        writer.write(f)

    extracted = research.extract_pdf(pdf_path)
    assert extracted.detected_title == "Test Periodization Article"
    assert "Joe Friel" in extracted.detected_authors
    assert "J. Daniels" in extracted.detected_authors
    # blank page → empty text body but stable hash regardless.
    assert len(extracted.sha256) == 64
    assert pypdf  # silence unused-import warning under typecheck
