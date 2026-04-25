"""Shared HTML scaffolding for dashboards: layout, CSS, output paths."""

from __future__ import annotations

from datetime import date
from html import escape
from pathlib import Path

from ..paths import repo_root

_CSS = """
:root {
  color-scheme: light dark;
  --fg: #1a1a1a;
  --muted: #666;
  --bg: #fafafa;
  --card: #fff;
  --border: #e2e2e2;
  --accent: #2563eb;
  --good: #16a34a;
  --warn: #ca8a04;
  --bad: #dc2626;
}
@media (prefers-color-scheme: dark) {
  :root {
    --fg: #ededed;
    --muted: #9aa0a6;
    --bg: #111;
    --card: #1c1c1c;
    --border: #303030;
    --accent: #60a5fa;
    --good: #4ade80;
    --warn: #facc15;
    --bad: #f87171;
  }
}
* { box-sizing: border-box; }
body {
  font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  color: var(--fg);
  background: var(--bg);
  margin: 0;
  padding: 24px;
}
header.page {
  margin-bottom: 24px;
}
h1 {
  font-size: 22px;
  margin: 0 0 4px;
}
h2 {
  font-size: 16px;
  margin: 24px 0 8px;
  color: var(--muted);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.subtitle {
  color: var(--muted);
  font-size: 13px;
}
.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 14px 16px;
  margin-bottom: 12px;
}
.kv {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 4px 16px;
  font-variant-numeric: tabular-nums;
}
.kv dt { color: var(--muted); }
.kv dd { margin: 0; }
table {
  border-collapse: collapse;
  width: 100%;
  font-variant-numeric: tabular-nums;
}
th, td {
  text-align: left;
  padding: 6px 10px;
  border-bottom: 1px solid var(--border);
}
th {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--muted);
  font-weight: 600;
}
tr.completed td { color: var(--fg); }
tr.completed td.status { color: var(--good); }
tr.moved   td.status { color: var(--warn); }
tr.skipped td.status { color: var(--bad); }
tr.pending td { color: var(--muted); }
.spark {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  font-variant-numeric: tabular-nums;
}
.spark svg { vertical-align: middle; }
.spark .label { color: var(--muted); width: 8em; display: inline-block; }
.spark .value { font-weight: 600; }
.notice {
  color: var(--muted);
  font-style: italic;
  padding: 12px 0;
}
footer.page {
  margin-top: 32px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 12px;
}
pre.changelog {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 10px 12px;
  white-space: pre-wrap;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.4;
  overflow-x: auto;
}
details summary {
  cursor: pointer;
  font-weight: 600;
}
.chip {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 11px;
  background: var(--bg);
  border: 1px solid var(--border);
  color: var(--muted);
  margin-right: 4px;
}
"""


def page(title: str, body: str, *, footer_note: str | None = None) -> str:
    """Wrap a rendered body in the standard HTML envelope."""
    rendered_at = date.today().isoformat()
    foot = (
        f"<footer class='page'>{escape(footer_note)} · rendered {rendered_at}</footer>"
        if footer_note
        else f"<footer class='page'>rendered {rendered_at}</footer>"
    )
    return (
        "<!doctype html>\n"
        "<html lang='en'>\n"
        "<head>\n"
        "  <meta charset='utf-8'>\n"
        "  <meta name='viewport' content='width=device-width, initial-scale=1'>\n"
        f"  <title>{escape(title)}</title>\n"
        f"  <style>{_CSS}</style>\n"
        "</head>\n"
        f"<body>\n{body}\n{foot}\n</body>\n</html>\n"
    )


def dashboards_dir(*, root: Path | None = None) -> Path:
    """Return ``<repo>/dashboards/`` (gitignored output dir). Created on access."""
    d = (root or repo_root()) / "dashboards"
    d.mkdir(parents=True, exist_ok=True)
    return d


def output_path(
    kind: str,
    scope: str,
    *,
    today: date | None = None,
    root: Path | None = None,
) -> Path:
    """Build ``dashboards/<kind>-<scope>-<YYYYMMDD>.html``.

    ``scope`` is sanitized — colons and slashes become hyphens so paths stay portable.
    """
    safe_scope = scope.replace(":", "-").replace("/", "-")
    stamp = (today or date.today()).strftime("%Y%m%d")
    return dashboards_dir(root=root) / f"{kind}-{safe_scope}-{stamp}.html"


def write_html(html: str, path: Path) -> Path:
    """Write the HTML doc and return the path (for CLI to print)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def fmt_num(value: float | int | None, *, precision: int = 1) -> str:
    """Format a number, falling back to em-dash for None/NaN."""
    if value is None:
        return "—"
    try:
        return f"{float(value):.{precision}f}"
    except (TypeError, ValueError):
        return "—"


def fmt_signed(value: float | int | None, *, precision: int = 1) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):+.{precision}f}"
    except (TypeError, ValueError):
        return "—"


__all__ = [
    "dashboards_dir",
    "fmt_num",
    "fmt_signed",
    "output_path",
    "page",
    "write_html",
]
