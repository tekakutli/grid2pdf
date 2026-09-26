"""""
floor_plan_draw.py — SVG string generation for the floor plan.

Reads the layout dict from floor_plan_layout and renders it.  No
routing or ruler resolution happens here — floor_plan_layout owns
the label positions, the leader geometries, and the ruler segments
because those are mutually constrained and the resolution has to
happen before drawing.

Coordinate handling: source space (from room.py) has +Y pointing
north, i.e. up on a printed plan.  SVG has +Y pointing down.  Every
source point is mapped through `to_screen`, which flips Y and scales
by PRINT_SCALE at once.  Nothing is emitted inside a group transform,
which is what keeps text upright.

Margins
-------
The whole drawing sits inside a uniform empty border of MARGIN_MM
millimetres on every edge.  The final SVG's width and height are

    W = (bbox_width  in source units) * PRINT_SCALE + 2 * MARGIN_MM
    H = (bbox_height in source units) * PRINT_SCALE + 2 * MARGIN_MM

and every drawn point is placed at

    screen_x = (x - minx) * PRINT_SCALE + MARGIN_MM
    screen_y = (maxy - y) * PRINT_SCALE + MARGIN_MM

so the drawing's bbox spans [MARGIN_MM, W - MARGIN_MM] ×
[MARGIN_MM, H - MARGIN_MM] and nothing touches the SVG edges.

The bbox itself is computed by _bbox_of, which collects every
element the renderer will draw — plan faces, step edges (solid and
dashed), column outlines, all four dimension-line arrays and the
tick array, every label, and every leader segment.  Labels get an
extra font-proportional pad (LABEL_BBOX_PAD_FRAC) because their
collision-box half-extents are CHAR_ASPECT-based estimates, and a
wide display face can render wider than the estimate.

Layer colours:

    ink   structural — plan outline, step risers, column outlines,
                       labels, leaders
    red   dimension lines only — the "rulers"

Line weights
------------
The six _W_* constants are the printed widths, in millimetres.  Their
relative sizes are what make a plan readable: the wall silhouette
outranks the column and step outlines, which outrank the dimension
lines, which outrank the leaders.

    _W_PLAN       1.2   outer wall silhouette — the strongest mark
    _W_COL        1.1   column footprint outline
    _W_DIM_SMALL  1.0   small-room rulers
    _W_STEP       0.9   step riser outline
    _W_DIM_MAIN   0.6   main-wall rulers
    _W_TEXT       0.6   leader lines and arrowhead chevrons

Step edges — solid vs. dashed
-----------------------------
A step polygon edge is drawn as one of two things:

    step_lines_solid   the edge is coincident with a wall face, so
                       it reinforces the wall's own boundary.  Drawn
                       solid, same weight as the other step edges.

    step_lines_dashed  the edge is a free-standing riser against
                       open floor.  Drawn dashed, so a reader can
                       tell "this is a step" apart from "this is a
                       wall boundary".

Edges that lie interior to a merged surface (two same-height
neighbouring steps) are not present in either list — the layout
stage drops them.

Label tips
----------
Each label carries a `tip` field:

    "arrow"   the leader ends in a small open chevron — two short
              solid segments.  Used for labels that point at a
              specific coordinate: a dimension tag's mid, a door
              label's jamb.

    "dot"     the leader ends in a small filled circle.  Used for
              labels that point at a shape: a column tag, a STEP
              label.

Every leader is a single continuous line.  No dashes anywhere.

Self-check
----------
After assembly, `render` parses its own output via xml.etree.  On
failure it prints the line range around the parse error.
"""

import math


PRINT_SCALE = 0.1

# Outer margin around the whole drawing, in printed millimetres.  The
# final SVG's width and height include 2 × MARGIN_MM — MARGIN_MM of
# empty border on each edge — and no drawn element is placed closer
# than MARGIN_MM to any edge.
MARGIN_MM = 30.0

# Extra safety pad around each label, as a fraction of the label's
# font size, applied only to the whole-image bbox.  The label.hw and
# label.hh values are CHAR_ASPECT-based estimates used by the layout;
# the actual rendered text of a wide display face can extend a few
# percent wider, and a taller cap height can push the ascender past
# hh.  This pad makes the bbox cover that, so the outer margin below
# stays truly empty.
LABEL_BBOX_PAD_FRAC = 0.15

# Printed widths, in mm.
_W_PLAN      = 1.2
_W_COL       = 1.1
_W_STEP      = 0.9
_W_DIM_SMALL = 1.0
_W_DIM_MAIN  = 0.6
_W_TEXT      = 0.6

COLOR_INK   = "#000000"
COLOR_RED   = "#d91a1a"
FONT_FAMILY = "sans-serif"

# Dot radius, as a fraction of arrow_size.
_DOT_RADIUS_FACTOR = 0.35


def _esc(s):
    return (str(s).replace("&",  "&amp;")
                   .replace("<",  "&lt;")
                   .replace(">",  "&gt;")
                   .replace('"', "&quot;"))


def _bbox_of(geom, layout):
    """Compute the drawing's bounding box in source units.

    Covers every element the renderer emits — plan faces, step edges
    (solid and dashed), column outlines, all four dimension-line
    arrays and the tick array, every label, and every leader segment
    including its target anchor — so the outer margin applied by
    render() ends up as a uniform empty border around the whole
    drawing.

    Labels are padded by LABEL_BBOX_PAD_FRAC × font size on every
    side, because label.hw / label.hh are CHAR_ASPECT-based estimates
    and a wide display face can render wider than that."""
    xs, ys = [], []

    def add_pt(p):
        xs.append(p[0]); ys.append(p[1])

    def add_segs(segs):
        for a, b in segs:
            add_pt(a); add_pt(b)

    # Plan outlines (walls, including their hole rings).
    for face in geom["planFaces"]:
        for p in face["outer"]:
            add_pt(p)
        for h in face.get("holes", []):
            for p in h:
                add_pt(p)

    # Steps, columns, dimension lines, ticks.
    add_segs(layout.get("step_lines_solid",  []))
    add_segs(layout.get("step_lines_dashed", []))
    add_segs(layout.get("col_outline_lines", []))
    add_segs(layout.get("dim_lines_main",    []))
    add_segs(layout.get("dim_lines_small",   []))
    add_segs(layout.get("col_dim_lines",     []))
    add_segs(layout.get("dim_ticks",         []))

    # Labels and notes: collision box, padded to cover font wobble.
    for lb in layout["labels"] + layout["notes_labels"]:
        pad = lb.size * LABEL_BBOX_PAD_FRAC
        xs.append(lb.pos[0] - lb.hw - pad); xs.append(lb.pos[0] + lb.hw + pad)
        ys.append(lb.pos[1] - lb.hh - pad); ys.append(lb.pos[1] + lb.hh + pad)

    # Leaders, including their target anchors.  The tail sits on the
    # label box edge (covered above) and the tip is the anchor; the
    # arrowhead wings are between them, so the two endpoints bound the
    # whole segment.
    for anchor, seg in (layout.get("leaders") or {}).values():
        if anchor is not None:
            add_pt(anchor)
        if seg is not None:
            add_pt(seg[0]); add_pt(seg[1])

    if not xs:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)


def _self_check(svg):
    import xml.etree.ElementTree as ET
    try:
        ET.fromstring(svg)
        return True
    except ET.ParseError as e:
        print(f"  WARNING: floor_plan_draw produced malformed XML: {e}")
        try:
            line_no, col_no = e.position
        except Exception:
            return False
        lines = svg.split("\n")
        lo = max(0, line_no - 3)
        hi = min(len(lines), line_no + 3)
        print(f"  ---- around line {line_no}, column {col_no} ----")
        for i in range(lo, hi):
            marker = ">>>" if i + 1 == line_no else "   "
            snippet = lines[i]
            if len(snippet) > 160:
                snippet = snippet[:157] + "..."
            print(f"  {marker} {i + 1:4d} | {snippet}")
        print(f"  ------------------------------------------------")
        return False


def _arrowhead_segments(tip, tail, size):
    """Two chevron segments forming an arrowhead at `tip`, pointing
    back along (tip − tail)."""
    wx, wy = tip
    sx, sy = tail
    dx = wx - sx
    dy = wy - sy
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return []
    ux, uy = dx / L, dy / L
    px, py = -uy, ux
    bx = wx - ux * size
    by = wy - uy * size
    w1 = (bx + px * size * 0.5, by + py * size * 0.5)
    w2 = (bx - px * size * 0.5, by - py * size * 0.5)
    return [(w1, (wx, wy)), (w2, (wx, wy))]


def render(geom, layout):
    minx, miny, maxx, maxy = _bbox_of(geom, layout)

    W = (maxx - minx) * PRINT_SCALE + 2 * MARGIN_MM
    H = (maxy - miny) * PRINT_SCALE + 2 * MARGIN_MM

    def to_screen(x, y):
        return ((x - minx) * PRINT_SCALE + MARGIN_MM,
                (maxy - y) * PRINT_SCALE + MARGIN_MM)

    def line(p1, p2, stroke_mm):
        x1, y1 = to_screen(p1[0], p1[1])
        x2, y2 = to_screen(p2[0], p2[1])
        return (f'<line x1="{x1:.3f}" y1="{y1:.3f}" '
                f'x2="{x2:.3f}" y2="{y2:.3f}" '
                f'stroke-width="{stroke_mm:.3f}"/>')

    def circle(center_src, radius_src, fill):
        cx, cy = to_screen(center_src[0], center_src[1])
        r = radius_src * PRINT_SCALE
        return (f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{r:.3f}" '
                f'fill="{fill}"/>')

    def poly_path(poly):
        if not poly:
            return ""
        parts = []
        x, y = to_screen(poly[0][0], poly[0][1])
        parts.append(f"M {x:.3f} {y:.3f}")
        for p in poly[1:]:
            x, y = to_screen(p[0], p[1])
            parts.append(f"L {x:.3f} {y:.3f}")
        parts.append("Z")
        return " ".join(parts)

    def face_path(face):
        d = poly_path(face["outer"])
        for h in face.get("holes", []):
            if h:
                d += " " + poly_path(h)
        return d

    def text(p_src, s, size_src):
        x, y = to_screen(p_src[0], p_src[1])
        size_mm = size_src * PRINT_SCALE
        return (f'<text x="{x:.3f}" y="{y:.3f}" '
                f'font-family="{FONT_FAMILY}" '
                f'font-size="{size_mm:.2f}" '
                f'text-anchor="middle" '
                f'dy=".35em">'
                f'{_esc(s)}</text>')

    labels       = layout["labels"]
    notes_labels = layout["notes_labels"]
    leaders      = layout.get("leaders", {})
    arrow_size   = layout["arrow_size"]

    p = []
    p.append('<?xml version="1.0" encoding="UTF-8"?>')
    p.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
        f'width="{W:.2f}mm" height="{H:.2f}mm" '
        f'viewBox="0 0 {W:.2f} {H:.2f}">')

    # plan faces — outline only
    p.append(f'<g fill="none" stroke="{COLOR_INK}" '
             f'stroke-width="{_W_PLAN:.3f}">')
    for face in geom["planFaces"]:
        d = face_path(face)
        if d:
            p.append(f'<path d="{d}"/>')
    p.append('</g>')

    # step outlines — SOLID edges first (risers against walls).
    p.append(f'<g fill="none" stroke="{COLOR_INK}" '
             f'stroke-width="{_W_STEP:.3f}">')
    for (a, b) in layout["step_lines_solid"]:
        p.append(line(a, b, _W_STEP))
    p.append('</g>')

    # step outlines — DASHED edges (free-standing risers).
    p.append(f'<g fill="none" stroke="{COLOR_INK}" '
             f'stroke-width="{_W_STEP:.3f}" '
             f'stroke-dasharray="6 4">')
    for (a, b) in layout["step_lines_dashed"]:
        p.append(line(a, b, _W_STEP))
    p.append('</g>')

    # column outlines
    p.append(f'<g fill="none" stroke="{COLOR_INK}" '
             f'stroke-width="{_W_COL:.3f}">')
    for (a, b) in layout["col_outline_lines"]:
        p.append(line(a, b, _W_COL))
    p.append('</g>')

    # main dimension lines
    p.append(f'<g fill="none" stroke="{COLOR_RED}" '
             f'stroke-width="{_W_DIM_MAIN:.3f}">')
    for (a, b) in layout["dim_lines_main"]:
        p.append(line(a, b, _W_DIM_MAIN))
    p.append('</g>')

    # small-room dimension lines
    p.append(f'<g fill="none" stroke="{COLOR_RED}" '
             f'stroke-width="{_W_DIM_SMALL:.3f}">')
    for (a, b) in layout["dim_lines_small"]:
        p.append(line(a, b, _W_DIM_SMALL))
    p.append('</g>')

    # column dimension lines
    p.append(f'<g fill="none" stroke="{COLOR_RED}" '
             f'stroke-width="{_W_COL:.3f}">')
    for (a, b) in layout["col_dim_lines"]:
        p.append(line(a, b, _W_COL))
    p.append('</g>')

    # dimension-line ticks
    p.append(f'<g fill="none" stroke="{COLOR_RED}" '
             f'stroke-width="{_W_DIM_MAIN:.3f}">')
    for (a, b) in layout["dim_ticks"]:
        p.append(line(a, b, _W_DIM_MAIN))
    p.append('</g>')

    # leaders — solid, continuous, no dashes
    p.append(f'<g stroke="{COLOR_INK}" stroke-width="{_W_TEXT:.3f}" '
             f'stroke-linecap="round" fill="none">')
    for i, lb in enumerate(labels):
        leader = leaders.get(i)
        if leader is None:
            continue
        _anchor, seg = leader
        if seg is None:
            continue
        tail, tip = seg[0], seg[1]
        p.append(line(tail, tip, _W_TEXT))
        if lb.tip == "arrow":
            for (a, b) in _arrowhead_segments(tip, tail, arrow_size):
                p.append(line(a, b, _W_TEXT))
    p.append('</g>')

    # dots sit on top of the leaders, one per polygon-target label
    p.append(f'<g stroke="none">')
    for i, lb in enumerate(labels):
        if lb.tip != "dot":
            continue
        leader = leaders.get(i)
        if leader is None:
            continue
        anchor_pt, seg = leader
        if seg is None:
            continue
        p.append(circle(anchor_pt,
                        arrow_size * _DOT_RADIUS_FACTOR,
                        COLOR_INK))
    p.append('</g>')

    # text, on top of everything
    p.append(f'<g fill="{COLOR_INK}" stroke="none">')
    for lb in labels + notes_labels:
        p.append(text(lb.pos, lb.text, lb.size))
    p.append('</g>')

    p.append('</svg>')

    svg = "\n".join(p) + "\n"
    _self_check(svg)
    return svg
