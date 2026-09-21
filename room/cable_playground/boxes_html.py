"""
boxes_html.py — assembles the HTML/JS bundle from boxes_panel + boxes_live.

Concatenation order (all inside one <script>):

    1. HTML_HEAD       — the panel markup, empty canvas, light base CSS
    2. const GEOMETRY  — the geometry dict, JSON-encoded
    3. LIVE_JS         — the concatenation from boxes_live.py, which
                         starts with the model (bx_core.CORE_JS)
    4. BOOT_JS         — wires button onclick handlers, kicks off load
    5. HTML_TAIL       — closes the script tag and body

The model is NOT injected here separately.  It is the first thing in
LIVE_JS, so that bx_base's load-time statements
(viewFloor.zoom = 1; viewWall.zoom = 1; mouse.shift = false; …) find
the objects they extend.  An earlier revision pulled a placeholder
CORE_JS from boxes_panel and injected it here, which left bx_core's
real model out of the bundle entirely — the browser threw
"viewFloor is not defined" on bx_base's first line and nothing after
it ran.
"""

import json

from boxes_panel import HTML_HEAD, BOOT_JS, HTML_TAIL
from boxes_live  import LIVE_JS


def render(geom):
    return (
        HTML_HEAD
        + "<script>\n"
        + '"use strict";\n'
        + "const GEOMETRY = "
        + json.dumps(geom, separators=(",", ":"))
        + ";\n"
        + LIVE_JS
        + BOOT_JS
        + HTML_TAIL
    )
