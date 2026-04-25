"""HTML dashboard renderers — Phase 5.

Pure rendering over coach.db + plans/. No LLM involvement, no network calls
at render time (the macro Gantt embeds a Mermaid CDN script — that fires in
the *viewer's* browser, not at render).

Each renderer returns a complete HTML document string. The CLI verb under
``coach dashboard <kind>`` wraps the renderer with file I/O and the optional
``--open`` browser launch.
"""

from .decisions import render_decisions
from .macro import render_macro
from .week import render_week

__all__ = ["render_decisions", "render_macro", "render_week"]
