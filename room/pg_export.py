"""
pg_export.py — aggregator for the print-friendly cable-run exporter.

This file is the aggregator.  The exporter was originally a single
~2000-line module; it has been split into thirteen submodules by
concern, mirroring the split pg_arrows.py uses for the wall→plan
escape-arrow field:

    pg_export_textures    the four print-friendly hatch patterns
                          (void, step, room-interior, and the two
                          drawing-side textures)
    pg_export_linearize   anchor graph → renderable data.  The
                          collinear-anchor filter, the true-cable
                          linearizer, the run grouper, the wall-run
                          layout, the segment directory, and the
                          per-pill JSON metadata builder
    pg_export_primitives  the drawing atoms every renderer shares:
                          the badge, the arrow style table, the path
                          trims, the arrow sampler, the shared label
                          geometry constants, and refineLeaderOffsets
    pg_export_geometry    the pure distance and segment-pair atoms,
                          plus the segment-conflict sweep
    pg_export_snapshot    _buildLayout and its consumers — the one
                          build per candidate that keeps the hot
                          evaluate() loop cheap, and the conflict
                          counter the optimiser's pass loop uses to
                          skip clean leaders
    pg_export_penalties   the tier-list penalty functions:
                          boundary, wire (parallel vs. crossing),
                          wall-edge, and corner features
    pg_export_style       the style-conflict graph — the parallel-
                          proximity detector that drives the
                          solid/dashed alternation, and the
                          bipartite/greedy vertex cover
    pg_export_optimizer   the leader-geometry optimiser: five move
                          families (bevel, jog, dive mode, channel-Y
                          slide, offsetA slide), the exhaustive tier
                          list in evaluate(), the jog-direction guard
    pg_export_pillpush    the escape valve — push a leader's own pill
                          or a conflicting foreign pill to open a
                          highway — and the polish loop that
                          alternates the optimiser and the push
    pg_export_labels      the vertex label placement and draw pass
    pg_export_render      the crop, the rescale, and the main
                          renderCableRunToCanvas
    pg_export_preview     the preview page — one card per cable,
                          the download-all buttons, the collinear
                          filter checkbox
    pg_export_entry       the export button and the entry point

Each module is a Python string.  The aggregator concatenates them
into one <script> tag, so the JS lives in a single flat scope — same
functions, same names, same call sites as before the split.  Only
the Python packaging changed; there is no semantic difference.

The full rationale for the leader-routing algorithm — the exhaustive
overlap-class enumeration, the tier weights, the optimiser move
vocabulary, the pill push, the polish loop — lives in
pg_export_optimizer.py and pg_export_pillpush.py.  This file's job is
just to name the split and stitch the pieces together.

Deliberately NOT translated (in any module of this split):

    • wall-segment names — W1, W3, C1.W, SR.edge[1] — these are IDs.
    • pill contents — "V<n>", "h <cm>", "W <cm> . E <cm>".
    • the "..." run-gap separator.

Every user-facing string routes through T() in pg_i18n.py.
"""

from pg_export_textures  import TEXTURES_JS
from pg_export_linearize import LINEARIZE_JS
from pg_export_primitives import PRIMITIVES_JS
from pg_export_geometry  import GEO_JS
from pg_export_snapshot  import SNAPSHOT_JS
from pg_export_penalties import PENALTIES_JS
from pg_export_style     import STYLE_JS
from pg_export_optimizer import OPTIMIZER_JS
from pg_export_pillpush  import PILLPUSH_JS
from pg_export_labels    import LABELS_JS
from pg_export_render    import RENDER_JS
from pg_export_preview   import PREVIEW_JS
from pg_export_entry     import ENTRY_JS


EXPORT_JS = (
      TEXTURES_JS
    + LINEARIZE_JS
    + PRIMITIVES_JS
    + GEO_JS
    + SNAPSHOT_JS
    + PENALTIES_JS
    + STYLE_JS
    + OPTIMIZER_JS
    + PILLPUSH_JS
    + LABELS_JS
    + RENDER_JS
    + PREVIEW_JS
    + ENTRY_JS
)
