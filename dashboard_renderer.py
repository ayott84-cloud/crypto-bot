"""Dashboard Jinja2 renderer — Phase D foundation.

Loads templates from `templates/` and inlines `static/css/*.css` +
`static/js/*.js` into a single self-contained HTML document. The Render
deploy model is preserved: build_dashboard() emits one `dashboard.html`
the dashboard-push.timer force-pushes to the render-dashboard branch.

Feature-flagged behind `DASHBOARD_V2` (env var). Default false → legacy
f-string renderer keeps shipping. Set DASHBOARD_V2=true on the droplet
to opt in.

This module is intentionally small. The template tree under templates/
is where the actual presentation logic lives.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("crypto_bot.dashboard_renderer")

_BOT_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _BOT_DIR / "templates"
_STATIC_DIR = _BOT_DIR / "static"


def dashboard_v2_enabled() -> bool:
    """Feature flag — default off. Flip via env: DASHBOARD_V2=true."""
    return os.getenv("DASHBOARD_V2", "false").lower() in ("true", "1", "yes")


def _lazy_env():
    """Construct a Jinja2 Environment on demand (lazy import for legacy-mode users)."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "html.j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _inline_static(html: str) -> str:
    """Replace <link rel="stylesheet" href="/static/css/X.css"> with inlined CSS.

    Same pattern for <script src="/static/js/X.js"> → inlined JS. The Render
    deploy serves a single static file, so we must inline everything at build
    time rather than relying on separate asset URLs.
    """
    import re

    def _read(rel_path: str) -> str:
        p = _STATIC_DIR / rel_path
        if not p.exists():
            logger.warning("Static asset not found: %s", p)
            return ""
        return p.read_text(encoding="utf-8")

    def _css_repl(m):
        href = m.group(1)
        # Strip the leading "/static/" or "static/"
        rel = href.replace("/static/", "", 1).replace("static/", "", 1)
        return f"<style>\n{_read(rel)}\n</style>"

    def _js_repl(m):
        src = m.group(1)
        rel = src.replace("/static/", "", 1).replace("static/", "", 1)
        return f"<script>\n{_read(rel)}\n</script>"

    html = re.sub(
        r'<link\s+rel="stylesheet"\s+href="([^"]+)"\s*/?>',
        _css_repl, html,
    )
    html = re.sub(
        r'<script\s+src="([^"]+)"></script>',
        _js_repl, html,
    )
    return html


def render(template_name: str, context: Dict[str, Any]) -> str:
    """Render `templates/<template_name>` with `context`, inline static assets."""
    env = _lazy_env()
    template = env.get_template(template_name)
    rendered = template.render(**context)
    return _inline_static(rendered)
