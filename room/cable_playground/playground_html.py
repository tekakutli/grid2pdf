"""
playground_html.py — assembles the HTML/JS bundle from pg_core + pg_live
+ pg_arrows + pg_export.

pg_core.py    holds everything that defines the model, the storage format,
              and the passive infrastructure.
pg_live.py    holds everything interactive — UX, gestures, on-screen
              rendering, minus the escape-arrow field.
pg_arrows.py  holds the wall→plan escape-arrow field — placement, drawing,
              and hit testing.  Isolated so the arrow algorithm can be
              iterated on without touching the rest of the playground.
pg_export.py  holds the printable cable-run diagram exporter.

This file concatenates them and injects the geometry dict as `const GEOMETRY`.

The split exists so we can iterate on the playground's UX by editing only
pg_live.py, on the escape-arrow field by editing only pg_arrows.py, and on
the printable export by editing only pg_export.py — while pg_core.py stays
put unless the model itself changes.
"""

import json

from pg_core   import HTML_HEAD, CORE_JS, BOOT_JS, HTML_TAIL
from pg_live   import LIVE_JS
from pg_arrows import ARROWS_JS
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
        + ARROWS_JS
        + EXPORT_JS
        + BOOT_JS
        + HTML_TAIL
    )
