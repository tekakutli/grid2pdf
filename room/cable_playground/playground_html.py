"""
playground_html.py — assembles the HTML/JS bundle from pg_core + pg_live + pg_export.

pg_core.py    holds everything that defines the model, the storage format,
              and the passive infrastructure.
pg_live.py    holds everything interactive — UX, gestures, on-screen rendering.
pg_export.py  holds the printable cable-run diagram exporter.

This file concatenates them and injects the geometry dict as `const GEOMETRY`.

The split exists so we can iterate on the playground's UX by editing only
pg_live.py — pg_core.py stays put unless the model itself changes — and on
the printable export by editing only pg_export.py.
"""

import json

from pg_core   import HTML_HEAD, CORE_JS, BOOT_JS, HTML_TAIL
from pg_live   import LIVE_JS
from pg_export import EXPORT_JS


def render(geom):
    """Return the final HTML document with `geom` baked in."""
    return (
        HTML_HEAD
        + "<script>\n"
        + '"use strict";\n'
        + "const GEOMETRY = "
        + json.dumps(geom, separators=(",", ":"))
        + ";\n"
        + CORE_JS
        + LIVE_JS
        + EXPORT_JS
        + BOOT_JS
        + HTML_TAIL
    )
