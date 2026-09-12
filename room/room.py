"""
room.py — parametric model of an irregular room + 2D floor plan

The libfontconfig bundled in some pip-installed build123d wheels is older
than the system one and prints warnings while scanning modern
/etc/fonts/conf.d/* files.  We mute OS-level stderr (fd 2) around the
operations that trigger fontconfig — the imports and every Text() call —
so the console stays clean.  Python tracebacks still work, because fd 2 is
restored before any exception unwinds past the context manager.

Install:  pip install build123d cairosvg
Run:      python room.py
Outputs:  room.step, room.stl, floor_plan.svg, floor_plan.png,
          room_walls.json   (sidecar for downstream tools, schema v3)
"""

import json
import os
import re
from collections import defaultdict
from contextlib import contextmanager
from math import hypot, atan2, degrees


@contextmanager
def _quiet():
    """Redirect OS-level stderr (fd 2) to /dev/null for the duration of the block."""
    saved = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(devnull)
        os.close(saved)


with _quiet():
    from build123d import *


# ============================================================================
# ROOM DESCRIPTION LANGUAGE — declarative MEASUREMENTS
# ============================================================================
#
# Every geometric value in the MEASUREMENTS block is expressed as one of:
#
#   • a raw number in "units" — UNIT_MM converts to mm (10.0 = cm)
#   • a named constant from CONSTANTS (e.g. "STEP_DEPTH")
#   • an anchor to a feature (e.g. "W4.inner.y", "C1.E.x")
#   • an expression combining them (e.g. "W4.inner.y + 99")
#
# Bare offsets in expressions are in units (99 = 99 units = 990 mm).
# Anchors resolve to absolute mm values.
# ============================================================================


DESCRIBE_ONLY = False

UNIT_MM = 10.0

CONSTANTS = {
    "STEP_DEPTH":   17.0,
    "STEP_HEIGHT":  28.0,
    "W7_EXTENSION": 13.5,
    "WALL_HEIGHT":  238.0,
    "WALL_THICK":   15.0,
    "FLOOR_THICK":  20.0,
    "PLAN_CUT_Z":   120.0,
}

GROUND_PERIMETER = [
    ("W1", 306.0, None),
    ("W7", "STEP_DEPTH", None),
    ("W8", 279.0, "L"),
    (None, "STEP_DEPTH", "L"),
    ("W3", 419.0, None),
    ("W4", 166.0, "L"),
    ("W5", 111.0, "L"),
    ("W6", 113.0, "R"),
]

WALL_DIMS_SPEC = {
    "W1": 306.0,
    "W7": {"value": "STEP_DEPTH + W7_EXTENSION",
           "extend_to": "W13.inner.y"},
    "W8": 279.0,
    "W3": 419.0,
    "W4": 166.0,
    "W5": 111.0,
    "W6": 113.0,
}

SMALL_ROOMS = [
    {
        "tag": "SR",
        "start": "D2.west",
        "heading": "N",
        "walk": [
            ("W10", 85.5, None),
            ("W11", 86.0, "R"),
            ("W12", 72.0, "R"),
            ("W13", 16.0, "R"),
            (None,  13.5, "L"),
            (None,  70.0, "R"),
        ],
        "dims": ["85.5", "86", "72", "16", None, None],
    },
]

COLUMNS = [
    {
        "tag":  "C1",
        "note": "NW corner of extension, brackets W9/W3",
        "box": {
            "west":  "W3.inner.x",
            "east":  "W3.inner.x + 14",
            "south": "W8.inner.y - 27",
            "north": "W8.inner.y",
        },
    },
    {
        "tag":  "C2",
        "note": "on W3, 99 units north of W4, 42.5 units long",
        "box": {
            "west":  "W3.inner.x",
            "east":  "W3.inner.x + 14",
            "south": "W4.inner.y + 99",
            "north": "W4.inner.y + 141.5",
        },
    },
    {
        "tag":  "C3",
        "note": "SW corner of SR, on W8/W10 junction, sits on the step",
        "box": {
            "west":  "W10.inner.x",
            "east":  "W10.inner.x + 3",
            "south": "W8.inner.y",
            "north": "W8.inner.y + 9",
        },
    },
]

STEPS = [
    {
        "tag": "EXT",
        "height": "STEP_HEIGHT",
        "bounds": {
            "south": "W3.start.y",
            "east":  "W7.inner.x",
            "north": "W8.inner.y",
            "west":  "C1.E.x",
        },
    },
    {
        "tag": "SR_STRIP",
        "height": "STEP_HEIGHT",
        "bounds": {
            "south": "W8.inner.y",
            "east":  "W7.inner.x",
            "north": "W13.inner.y",
            "west":  "W10.inner.x",
        },
    },
]

OPENINGS = [
    {"tag": "D1", "wall_tag": "W6", "offset": 15.0,
     "width": "to_end", "height": 210.0},
    {"tag": "D2", "wall_tag": "W8", "offset": 0.0,
     "width": 70.0, "height": 210.0, "sill": "STEP_HEIGHT"},
]

# ---------------------------------------------------------------------------
# Presentation tunables (not part of the room description)
# ---------------------------------------------------------------------------

FONT_PATH         = "/usr/share/fonts/noto/NotoSans-Regular.ttf"
TAG_PREFIX        = "W"
LABEL_SIZE        = 16.0
SR_LABEL_SIZE     = 9.0
STEP_LABEL_SIZE   = 9.0
TAG_INSET         = 60.0
SR_TAG_INSET      = 20.0
DIM_GAP           = 30.0
DIM_SIZE          = 14.0
COLUMN_LABEL_SIZE = 9.0
COL_DIM_GAP       = 12.0
DOOR_LABEL_INSET  = 50.0
SR_DIM_GAP        = 8.0

ARROW_SIZE        = 8.0
CHAR_ASPECT       = 0.55
LABEL_HEIGHT_FACT = 0.75

LAYOUT_ITERATIONS = 300
LAYOUT_SPRING     = 0.05
LAYOUT_REPULSE    = 0.55
LAYOUT_WALL_PUSH  = 0.35
LAYOUT_MARGIN     = 3.0

HOST_PARALLEL_TOL   = 0.02
HOST_COLLINEAR_TOL  = 20.0
HOST_OVERLAP_MIN    = 0.05

COLUMN_CLIP_EPS     = 1.0

NOTES_LABEL_SIZE    = 10.0
NOTES_LINE_SPACING  = 1.5
NOTES_GAP_FACTOR    = 5.0
NOTES_LINE_MARGIN   = 20.0
NOTES_MAX_CELLS     = 700

WALL_COLOR = (0, 0, 0)
TEXT_COLOR = (0, 0, 0)
LINE_COLOR = (217, 26, 26)

PNG_SCALE = 0.5
PNG_BG    = "white"

TAG_OVERRIDES = {
    "W4": (-240.0 * UNIT_MM, -50.0 * UNIT_MM),
    "W5": (-160.0 * UNIT_MM, -80.0 * UNIT_MM),
    "W6": (-90.0 * UNIT_MM, 80.0 * UNIT_MM),
    "D1": (-20.0 * UNIT_MM, 40.0 * UNIT_MM),
}


# ============================================================================
# RESOLVER — anchors, walks, boxes, bounds
# ============================================================================

class RoomContext:
    def __init__(self, mm):
        self.mm = mm
        self.registry = {}

    def register(self, name, value):
        self.registry[name] = value

    def _resolve_atom(self, atom):
        atom = atom.strip()
        if atom in self.registry:
            v = self.registry[atom]
            if isinstance(v, (int, float)):
                return float(v)
            raise TypeError(f"anchor {atom!r} is not a scalar")
        try:
            return float(atom) * self.mm
        except ValueError:
            raise KeyError(f"unknown anchor {atom!r}")

    def resolve(self, expr):
        if isinstance(expr, (int, float)):
            return float(expr) * self.mm
        s = str(expr).strip()
        tokens = re.split(r'\s*([+-])\s*', s)
        value = self._resolve_atom(tokens[0])
        i = 1
        while i < len(tokens):
            op = tokens[i]
            operand = self._resolve_atom(tokens[i + 1])
            value = value + operand if op == '+' else value - operand
            i += 2
        return value

    def resolve_point(self, expr):
        if isinstance(expr, (tuple, list)) and len(expr) == 2:
            x = self.resolve(expr[0]) if isinstance(expr[0], str) \
                else float(expr[0]) * self.mm
            y = self.resolve(expr[1]) if isinstance(expr[1], str) \
                else float(expr[1]) * self.mm
            return (x, y)
        v = self.registry.get(expr)
        if isinstance(v, tuple) and len(v) == 2 \
                and isinstance(v[0], (int, float)):
            return v
        raise KeyError(f"cannot resolve point anchor {expr!r}")


_DIRS = [(0, 1), (-1, 0), (0, -1), (1, 0)]
_HEADINGS = {"N": 0, "W": 1, "S": 2, "E": 3}


def walk_polygon(ctx, start, heading, entries, snap_tol_mm=30.0):
    heading_idx = _HEADINGS[heading]
    x, y = start
    pts = [(x, y)]
    for entry in entries:
        _tag, length, turn = entry
        if turn == "L":
            heading_idx = (heading_idx + 1) % 4
        elif turn == "R":
            heading_idx = (heading_idx - 1) % 4
        dx, dy = _DIRS[heading_idx]
        len_mm = ctx.resolve(length)
        x += dx * len_mm
        y += dy * len_mm
        pts.append((x, y))
    if len(pts) > 2:
        gap = hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
        if gap <= snap_tol_mm:
            pts.pop()
    return pts


def register_wall(ctx, name, p1, p2):
    ctx.register(f"{name}.start", p1)
    ctx.register(f"{name}.end", p2)
    ctx.register(f"{name}.inner", (p1, p2))
    ctx.register(f"{name}.start.x", p1[0])
    ctx.register(f"{name}.start.y", p1[1])
    ctx.register(f"{name}.end.x", p2[0])
    ctx.register(f"{name}.end.y", p2[1])
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    ctx.register(f"{name}.length", hypot(dx, dy))
    if abs(dx) < 1.0:
        ctx.register(f"{name}.inner.x", p1[0])
    if abs(dy) < 1.0:
        ctx.register(f"{name}.inner.y", p1[1])


def register_column(ctx, name, box):
    x0, y0, x1, y1 = box
    ctx.register(f"{name}.box", box)
    ctx.register(f"{name}.W", ((x0, y0), (x0, y1)))
    ctx.register(f"{name}.E", ((x1, y0), (x1, y1)))
    ctx.register(f"{name}.S", ((x0, y0), (x1, y0)))
    ctx.register(f"{name}.N", ((x0, y1), (x1, y1)))
    ctx.register(f"{name}.W.x", x0)
    ctx.register(f"{name}.E.x", x1)
    ctx.register(f"{name}.S.y", y0)
    ctx.register(f"{name}.N.y", y1)
    ctx.register(f"{name}.center", ((x0 + x1) / 2.0, (y0 + y1) / 2.0))


def register_opening(ctx, name, p1, p2):
    ctx.register(f"{name}.p1", p1)
    ctx.register(f"{name}.p2", p2)
    ctx.register(f"{name}.center",
                 ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0))
    if abs(p2[1] - p1[1]) < 1.0:
        if p1[0] < p2[0]:
            ctx.register(f"{name}.west", p1)
            ctx.register(f"{name}.east", p2)
        else:
            ctx.register(f"{name}.west", p2)
            ctx.register(f"{name}.east", p1)
    elif abs(p2[0] - p1[0]) < 1.0:
        if p1[1] < p2[1]:
            ctx.register(f"{name}.south", p1)
            ctx.register(f"{name}.north", p2)
        else:
            ctx.register(f"{name}.south", p2)
            ctx.register(f"{name}.north", p1)


def resolve_measurements():
    ctx = RoomContext(UNIT_MM)
    for name, value in CONSTANTS.items():
        ctx.register(name, value * UNIT_MM)

    inner_pts = walk_polygon(ctx, (0.0, 0.0), "N", GROUND_PERIMETER)
    main_tags = [e[0] for e in GROUND_PERIMETER]
    for i, tag in enumerate(main_tags):
        p1 = inner_pts[i]
        p2 = inner_pts[(i + 1) % len(inner_pts)]
        name = tag if tag else f"wall[{i + 1}]"
        register_wall(ctx, name, p1, p2)

    for op in OPENINGS:
        idx = main_tags.index(op["wall_tag"])
        wall_p1 = inner_pts[idx]
        wall_p2 = inner_pts[(idx + 1) % len(inner_pts)]
        L = hypot(wall_p2[0] - wall_p1[0], wall_p2[1] - wall_p1[1])
        ux = (wall_p2[0] - wall_p1[0]) / L
        uy = (wall_p2[1] - wall_p1[1]) / L
        off_mm = ctx.resolve(op["offset"])
        w_mm = (L - off_mm) if op["width"] == "to_end" \
            else ctx.resolve(op["width"])
        p1 = (wall_p1[0] + ux * off_mm, wall_p1[1] + uy * off_mm)
        p2 = (wall_p1[0] + ux * (off_mm + w_mm),
              wall_p1[1] + uy * (off_mm + w_mm))
        op["_wall_p1"] = wall_p1
        op["_wall_p2"] = wall_p2
        op["_p1"] = p1
        op["_p2"] = p2
        op["_off_mm"] = off_mm
        op["_w_mm"] = w_mm
        op["_h_mm"] = ctx.resolve(op["height"])
        op["_sill_mm"] = ctx.resolve(op.get("sill", 0.0))
        register_opening(ctx, op["tag"], p1, p2)

    for sr in SMALL_ROOMS:
        start = ctx.resolve_point(sr["start"])
        sr["_pts"] = walk_polygon(ctx, start, sr["heading"], sr["walk"])
        sr["_wall_tags"] = [e[0] for e in sr["walk"]]
        for i, entry in enumerate(sr["walk"]):
            tag = entry[0]
            if not tag:
                continue
            p1 = sr["_pts"][i]
            p2 = sr["_pts"][(i + 1) % len(sr["_pts"])]
            register_wall(ctx, tag, p1, p2)

    for col in COLUMNS:
        x0 = ctx.resolve(col["box"]["west"])
        x1 = ctx.resolve(col["box"]["east"])
        y0 = ctx.resolve(col["box"]["south"])
        y1 = ctx.resolve(col["box"]["north"])
        col["_box"] = (x0, y0, x1, y1)
        register_column(ctx, col["tag"], col["_box"])

    for step in STEPS:
        y_s = ctx.resolve(step["bounds"]["south"])
        y_n = ctx.resolve(step["bounds"]["north"])
        x_e = ctx.resolve(step["bounds"]["east"])
        x_w = ctx.resolve(step["bounds"]["west"])
        step["_outline"] = [(x_w, y_s), (x_e, y_s), (x_e, y_n), (x_w, y_n)]
        step["_height"] = ctx.resolve(step["height"]) / UNIT_MM

    wall_dims_resolved = []
    for tag in main_tags:
        if tag is None or tag not in WALL_DIMS_SPEC:
            wall_dims_resolved.append(None)
            continue
        entry = WALL_DIMS_SPEC[tag]
        if isinstance(entry, dict):
            val = ctx.resolve(entry["value"]) / UNIT_MM
            ext = ctx.resolve(entry["extend_to"]) if "extend_to" in entry \
                else None
            wall_dims_resolved.append((val, ext))
        else:
            wall_dims_resolved.append((ctx.resolve(entry) / UNIT_MM, None))

    return ctx, inner_pts, main_tags, wall_dims_resolved


CTX, INNER_PTS, MAIN_TAGS, WALL_DIMS_RESOLVED = resolve_measurements()

MM = UNIT_MM
WALL_HEIGHT = CONSTANTS["WALL_HEIGHT"]
WALL_THICK  = CONSTANTS["WALL_THICK"]
FLOOR_THICK = CONSTANTS["FLOOR_THICK"]
PLAN_CUT_Z  = CONSTANTS["PLAN_CUT_Z"]


# ============================================================================
# DUMP WALL DEFINITIONS FOR DOWNSTREAM TOOLS (e.g. cable_playground.py)
# ============================================================================
#
# Schema v3 — a 2D segment can carry SEVERAL vertical surfaces.
#
# Why: a wall above a step, a wall above a door, a wall between two windows
# all sit on the same 2D footprint.  A scalar "height_mm" cannot describe
# them all.  Each surface therefore carries a z-band instead:
#
#     z_range_mm = [z_lo, z_hi]
#
# Downstream tools ask "what surface is at (x, y, z)?" and get exactly one
# answer — or none, if (x, y, z) is inside solid material.
#
# The rooms's perimeter, in the order the surfaces are emitted, is traced
# CCW when viewed from above; the left normal (-dy, dx)/L of each segment
# points into the room.
#
# Layout:
#   * surfaces  — flat list of every visible surface, one entry per
#                 (2D footprint × z-band) pair:
#                     {tag, p1, p2, z_range_mm, kind, parent}
#   * openings  — every door/window: {tag, wall_tag, p1, p2, sill_mm, top_mm}
#   * columns   — plan-position boxes: {tag, note, box}
#
# Polarity contract (added when the unfolded view learned to respect the
# eagle-view alignment): each emitted (p1, p2) is oriented so that, when
# the unfolded wall strip is laid out with u increasing left→right, the
# segment's visual direction matches the plan:
#
#     • E–W walls run west → east   (east at the higher u / right side)
#     • N–S walls run south → north (north at the higher u / right side)
#
# This is enforced by _normalize_polarity() right before each surfaces.append.
# ============================================================================

def _dump_wall_json(path="room_walls.json"):

    # ---------- inline helpers ------------------------------------------
    def _pip(px, py, poly):
        inside = False
        j = len(poly) - 1
        for i in range(len(poly)):
            xi, yi = poly[i]
            xj, yj = poly[j]
            if ((yi > py) != (yj > py)) and \
               (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
                inside = not inside
            j = i
        return inside

    def _orient(p1, p2, tol=0.5):
        x1, y1 = p1; x2, y2 = p2
        if abs(y2 - y1) < tol:
            return ("H", y1, min(x1, x2), max(x1, x2))
        if abs(x2 - x1) < tol:
            return ("V", x1, min(y1, y2), max(y1, y2))
        return None

    def _overlaps(s1, s2, tol=0.5):
        a = _orient(*s1, tol=tol)
        b = _orient(*s2, tol=tol)
        if a is None or b is None:
            return False
        if a[0] != b[0] or abs(a[1] - b[1]) > tol:
            return False
        return a[2] < b[3] - tol and b[2] < a[3] - tol

    def _ensure_ccw(poly):
        n = len(poly)
        a2 = sum(poly[i][0] * poly[(i + 1) % n][1]
                 - poly[(i + 1) % n][0] * poly[i][1]
                 for i in range(n))
        return list(poly) if a2 >= 0 else list(reversed(poly))

    def _normalize_polarity(p1, p2):
        """Orient a wall/step segment so its direction in the unfolded
        strip matches its visual alignment in the eagle view:

            • E–W walls run west → east   (east at u1, i.e. on the right)
            • N–S walls run south → north (north at u1, i.e. on the right)

        The schema is unchanged — only the *order* of p1/p2 is affected,
        so consumers that treat segments as undirected (key-normalised
        dicts, adjacency tests) are unaffected, while the unfolding logic
        in cable_playground.py picks up the intended alignment for free.
        Diagonal segments (rare, none in practice) fall back to E–W.
        """
        dx = abs(p2[0] - p1[0])
        dy = abs(p2[1] - p1[1])
        if dx >= dy:
            if p1[0] > p2[0]:
                return p2, p1
        else:
            if p1[1] > p2[1]:
                return p2, p1
        return p1, p2

    def _split_by_covers(seg, covers, tol=0.5):
        """Return sub-segments of `seg` that are NOT covered by any of the
        axis-aligned segments in `covers`."""
        info = _orient(*seg, tol=tol)
        if info is None:
            return [seg]
        skind, scoord, s0, s1 = info

        intervals = []
        for cover in covers:
            cinfo = _orient(*cover, tol=tol)
            if cinfo is None:
                continue
            ckind, ccoord, c0, c1 = cinfo
            if ckind != skind or abs(ccoord - scoord) > tol:
                continue
            lo = max(s0, c0); hi = min(s1, c1)
            if hi > lo + tol:
                intervals.append((lo, hi))

        if not intervals:
            return [seg]

        intervals.sort()
        merged = [list(intervals[0])]
        for lo, hi in intervals[1:]:
            if lo <= merged[-1][1] + tol:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])

        pieces = []
        cur = s0
        for lo, hi in merged:
            if lo > cur + tol:
                pieces.append((cur, lo))
            cur = max(cur, hi)
        if cur < s1 - tol:
            pieces.append((cur, s1))

        p1, p2 = seg
        forward = (p2[0] >= p1[0]) if skind == "H" else (p2[1] >= p1[1])
        out = []
        for lo, hi in pieces:
            if skind == "H":
                out.append(((lo, scoord), (hi, scoord)) if forward
                           else ((hi, scoord), (lo, scoord)))
            else:
                out.append(((scoord, lo), (scoord, hi)) if forward
                           else ((scoord, hi), (scoord, lo)))
        return out

    WALL_H_MM = CONSTANTS["WALL_HEIGHT"] * UNIT_MM

    # ---------- main perimeter walls (CCW: room on left) ----------------
    n = len(INNER_PTS)
    main_poly = _ensure_ccw(INNER_PTS)
    rev = (main_poly[0] != INNER_PTS[0] or main_poly[1] != INNER_PTS[1])

    if not rev:
        main_tags_ccw = [
            (MAIN_TAGS[i] if i < len(MAIN_TAGS) and MAIN_TAGS[i]
                          else f"wall[{i+1}]") for i in range(n)
        ]
    else:
        main_tags_ccw = [
            (MAIN_TAGS[n - 1 - j] if (n - 1 - j) < len(MAIN_TAGS)
                                   and MAIN_TAGS[n - 1 - j]
                                 else f"wall[{n - j}]")
            for j in range(n)
        ]

    main_edges = [(main_tags_ccw[i], main_poly[i], main_poly[(i + 1) % n])
                  for i in range(n)]
    main_segs_all = [(p1, p2) for (_t, p1, p2) in main_edges]

    # ---------- small rooms (CCW; drop edges that overlap main) --------
    sr_polys_ccw = []
    for sr in SMALL_ROOMS:
        sr_polys_ccw.append((sr, _ensure_ccw(sr["_pts"])))

    sr_edges = []
    for sr, _poly in sr_polys_ccw:
        orig = sr["_pts"]
        n_orig = len(orig)
        a2_orig = sum(
            orig[i][0] * orig[(i + 1) % n_orig][1]
            - orig[(i + 1) % n_orig][0] * orig[i][1]
            for i in range(n_orig))
        reverse = a2_orig < 0
        tags_orig = sr["_wall_tags"]
        for i in range(n_orig):
            tag = tags_orig[i] if i < len(tags_orig) else None
            p1 = orig[i]; p2 = orig[(i + 1) % n_orig]
            if reverse:
                p1, p2 = p2, p1
            if any(_overlaps((p1, p2), ms) for ms in main_segs_all):
                continue
            # Untagged walk entries are still real walls.  SR's walk uses
            # None for unnamed boundary segments (e.g. the 13.5-unit stretch
            # between W7's extension and W13 that shares its footprint with
            # the top of SR_STRIP).  Synthesize a tag so it enters
            # all_wall_edges; the step-riser pass will then match against it
            # and suppress the phantom "step" surface at the same location.
            if not tag:
                tag = f"{sr['tag']}.edge[{i + 1}]"
            sr_edges.append((tag, p1, p2))

    # ---------- column faces facing the room ---------------------------
    PROBE = 5.0
    col_edges = []
    for col in COLUMNS:
        x0, y0, x1, y1 = col["_box"]
        candidates = [
            ("W", (x0, y0), (x0, y1), (-1.0,  0.0)),
            ("N", (x0, y1), (x1, y1), ( 0.0,  1.0)),
            ("E", (x1, y1), (x1, y0), ( 1.0,  0.0)),
            ("S", (x1, y0), (x0, y0), ( 0.0, -1.0)),
        ]
        for name, p1, p2, (nx, ny) in candidates:
            mx = (p1[0] + p2[0]) * 0.5 + nx * PROBE
            my = (p1[1] + p2[1]) * 0.5 + ny * PROBE
            faces_room = _pip(mx, my, main_poly)
            if not faces_room:
                for _sr, poly in sr_polys_ccw:
                    if _pip(mx, my, poly):
                        faces_room = True
                        break
            if not faces_room:
                continue
            if any(_overlaps((p1, p2), ms) for ms in main_segs_all):
                continue
            col_edges.append((f"{col['tag']}.{name}", p1, p2, col["tag"]))

    # ---------- master list of wall-like edges (all CCW) ---------------
    all_wall_edges = []
    for (tag, p1, p2) in main_edges:
        all_wall_edges.append((tag, p1, p2, "wall", None))
    for (tag, p1, p2) in sr_edges:
        all_wall_edges.append((tag, p1, p2, "wall", None))
    for (tag, p1, p2, parent) in col_edges:
        all_wall_edges.append((tag, p1, p2, "column", parent))

    # ---------- step boundary edges (from merged step regions) ---------
    regions = None
    for s in STEPS:
        face = make_face(Polyline(*s["_outline"], close=True))
        regions = face if regions is None else regions + face

    raw_step_edges = []   # (step_tag, step_h_mm, p1, p2) — CCW
    if regions is not None:
        for face in regions.faces():
            outer = face.outer_wire()
            outer_pts = _ensure_ccw([(v.X, v.Y) for v in outer.vertices()])

            edges = []
            for e in outer.edges():
                a = e.position_at(0); b = e.position_at(1)
                edges.append(((a.X, a.Y), (b.X, b.Y)))
            try:
                for iw in face.inner_wires():
                    for e in iw.edges():
                        a = e.position_at(0); b = e.position_at(1)
                        edges.append(((a.X, a.Y), (b.X, b.Y)))
            except Exception:
                pass

            for (p1, p2) in edges:
                dx = p2[0] - p1[0]; dy = p2[1] - p1[1]
                L = hypot(dx, dy)
                if L < 1e-6:
                    continue
                mx = (p1[0] + p2[0]) * 0.5
                my = (p1[1] + p2[1]) * 0.5
                nx = -dy / L; ny = dx / L
                if _pip(mx + nx * 5.0, my + ny * 5.0, outer_pts):
                    p1, p2 = p2, p1
                owner = None
                for step in STEPS:
                    ol = step["_outline"]
                    for k in range(len(ol)):
                        a = ol[k]; b = ol[(k + 1) % len(ol)]
                        d2x = b[0] - a[0]; d2y = b[1] - a[1]
                        L2 = d2x * d2x + d2y * d2y
                        if L2 < 1e-9:
                            continue
                        t = max(0.0, min(1.0,
                            ((mx - a[0]) * d2x + (my - a[1]) * d2y) / L2))
                        cx = a[0] + t * d2x; cy = a[1] + t * d2y
                        if hypot(mx - cx, my - cy) < 0.75:
                            owner = step
                            break
                    if owner:
                        break
                if owner is None:
                    owner = STEPS[0]
                raw_step_edges.append(
                    (owner["tag"], owner["_height"] * UNIT_MM, p1, p2))

    # ---------- wall ↔ step overlays ------------------------------------
    # For each wall edge, find every step edge that is collinear with it AND
    # whose step interior lies on the wall's room side.  Returns, per wall
    # edge index, a list of (t_lo, t_hi, step_h) in wall-local coordinates.
    def _step_on_wall(wp1, wp2, sp1, sp2, step):
        wdx = wp2[0] - wp1[0]; wdy = wp2[1] - wp1[1]
        wL = hypot(wdx, wdy)
        if wL < 1e-6:
            return None
        wux, wuy = wdx / wL, wdy / wL
        wnx, wny = -wuy, wux
        sdx = sp2[0] - sp1[0]; sdy = sp2[1] - sp1[1]
        sL = hypot(sdx, sdy)
        if sL < 1e-6:
            return None
        sux, suy = sdx / sL, sdy / sL
        if abs(sux * wuy - suy * wux) > 0.02:
            return None
        perp = (sp1[0] - wp1[0]) * wnx + (sp1[1] - wp1[1]) * wny
        if abs(perp) > 0.5:
            return None
        t1 = (sp1[0] - wp1[0]) * wux + (sp1[1] - wp1[1]) * wuy
        t2 = (sp2[0] - wp1[0]) * wux + (sp2[1] - wp1[1]) * wuy
        tlo = max(0.0, min(t1, t2))
        thi = min(wL, max(t1, t2))
        if thi <= tlo + 0.5:
            return None
        tmid = (tlo + thi) * 0.5
        px = wp1[0] + wux * tmid + wnx * 1.0
        py = wp1[1] + wuy * tmid + wny * 1.0
        if not _pip(px, py, step["_outline"]):
            return None
        return (tlo, thi)

    wall_step_feats = defaultdict(list)
    for idx, (_tag, wp1, wp2, _k, _p) in enumerate(all_wall_edges):
        for step in STEPS:
            sh = step["_height"] * UNIT_MM
            ol = step["_outline"]
            for k in range(len(ol)):
                sp1 = ol[k]; sp2 = ol[(k + 1) % len(ol)]
                r = _step_on_wall(wp1, wp2, sp1, sp2, step)
                if r is not None:
                    wall_step_feats[idx].append((r[0], r[1], float(sh)))

    # ---------- wall ↔ opening overlays ---------------------------------
    wall_opening_feats = defaultdict(list)
    for op in OPENINGS:
        wall_tag = op["wall_tag"]
        wall_idx = None
        for idx, (wtag, _p1, _p2, _k, _p) in enumerate(all_wall_edges):
            if wtag == wall_tag:
                wall_idx = idx
                break
        if wall_idx is None:
            continue
        wp1 = all_wall_edges[wall_idx][1]
        wp2 = all_wall_edges[wall_idx][2]
        wdx = wp2[0] - wp1[0]; wdy = wp2[1] - wp1[1]
        wL = hypot(wdx, wdy)
        if wL < 1e-6:
            continue
        wux, wuy = wdx / wL, wdy / wL
        t1 = (op["_p1"][0] - wp1[0]) * wux + (op["_p1"][1] - wp1[1]) * wuy
        t2 = (op["_p2"][0] - wp1[0]) * wux + (op["_p2"][1] - wp1[1]) * wuy
        tlo = max(0.0, min(t1, t2))
        thi = min(wL, max(t1, t2))
        if thi <= tlo + 0.5:
            continue
        sill = float(op.get("_sill_mm", 0.0))
        top = float(sill + op.get("_h_mm", 0.0))
        wall_opening_feats[wall_idx].append((tlo, thi, sill, top))

    # ---------- z-profile along a wall ----------------------------------
    def _wall_z_pieces(wp1, wp2, step_feats, opening_feats):
        wdx = wp2[0] - wp1[0]; wdy = wp2[1] - wp1[1]
        wL = hypot(wdx, wdy)
        if wL < 1e-6:
            return []
        ev = {0.0, wL}
        for (a, b, _h) in step_feats:
            ev.add(max(0.0, min(wL, a)))
            ev.add(max(0.0, min(wL, b)))
        for (a, b, _s, _t) in opening_feats:
            ev.add(max(0.0, min(wL, a)))
            ev.add(max(0.0, min(wL, b)))
        ev = sorted(ev)
        pieces = []
        for i in range(len(ev) - 1):
            a, b = ev[i], ev[i + 1]
            if b - a < 0.5:
                continue
            tmid = (a + b) * 0.5
            zr = [(0.0, WALL_H_MM)]
            for (s0, s1, sh) in step_feats:
                if s0 <= tmid <= s1:
                    zr = [(max(lo, sh), hi)
                          for (lo, hi) in zr if hi > sh + 0.5]
            for (o0, o1, sill, top) in opening_feats:
                if o0 <= tmid <= o1:
                    new = []
                    for (lo, hi) in zr:
                        if hi <= sill + 0.5 or lo >= top - 0.5:
                            new.append((lo, hi))
                        else:
                            if lo < sill - 0.5:
                                new.append((lo, sill))
                            if hi > top + 0.5:
                                new.append((top, hi))
                    zr = new
            if zr:
                pieces.append((a, b, zr))
        return pieces

    # ---------- emit wall/column surfaces -------------------------------
    surfaces = []
    for idx, (wtag, wp1, wp2, kind, parent) in enumerate(all_wall_edges):
        wdx = wp2[0] - wp1[0]; wdy = wp2[1] - wp1[1]
        wL = hypot(wdx, wdy)
        if wL < 1e-6:
            continue
        wux, wuy = wdx / wL, wdy / wL
        pieces = _wall_z_pieces(
            wp1, wp2,
            wall_step_feats.get(idx, []),
            wall_opening_feats.get(idx, []))
        for (a, b, zr) in pieces:
            p1 = (wp1[0] + wux * a, wp1[1] + wuy * a)
            p2 = (wp1[0] + wux * b, wp1[1] + wuy * b)
            p1, p2 = _normalize_polarity(p1, p2)
            for (lo, hi) in zr:
                if hi - lo < 0.5:
                    continue
                surfaces.append({
                    "tag":        wtag,
                    "p1":         [float(p1[0]), float(p1[1])],
                    "p2":         [float(p2[0]), float(p2[1])],
                    "z_range_mm": [float(lo), float(hi)],
                    "kind":       kind,
                    "parent":     parent,
                })

    # ---------- emit free-standing step risers --------------------------
    wall_segs = [(p1, p2) for (_t, p1, p2, _k, _p) in all_wall_edges]
    for (stag, sh, sp1, sp2) in raw_step_edges:
        for (fp1, fp2) in _split_by_covers((sp1, sp2), wall_segs):
            if hypot(fp2[0] - fp1[0], fp2[1] - fp1[1]) < 1.0:
                continue
            fp1, fp2 = _normalize_polarity(fp1, fp2)
            surfaces.append({
                "tag":        f"{stag}.step",
                "p1":         [float(fp1[0]), float(fp1[1])],
                "p2":         [float(fp2[0]), float(fp2[1])],
                "z_range_mm": [0.0, float(sh)],
                "kind":       "step",
                "parent":     stag,
            })

    # ---------- openings + columns metadata -----------------------------
    openings_data = [{
        "tag":      op["tag"],
        "wall_tag": op["wall_tag"],
        "p1":       [float(op["_p1"][0]), float(op["_p1"][1])],
        "p2":       [float(op["_p2"][0]), float(op["_p2"][1])],
        "sill_mm":  float(op.get("_sill_mm", 0.0)),
        "top_mm":   float(op.get("_sill_mm", 0.0) + op.get("_h_mm", 0.0)),
    } for op in OPENINGS]

    columns_data = [{
        "tag":  col["tag"],
        "note": col.get("note", ""),
        "box":  [float(col["_box"][0]), float(col["_box"][1]),
                 float(col["_box"][2]), float(col["_box"][3])],
    } for col in COLUMNS]

    data = {
        "version":           3,
        "unit_mm":           UNIT_MM,
        "wall_height_mm":    WALL_H_MM,
        "wall_thickness_mm": CONSTANTS["WALL_THICK"] * UNIT_MM,
        "plan_cut_z_mm":     CONSTANTS["PLAN_CUT_Z"] * UNIT_MM,
        "surfaces":          surfaces,
        "openings":          openings_data,
        "columns":           columns_data,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    n_w  = sum(1 for s in surfaces if s["kind"] == "wall")
    n_c  = sum(1 for s in surfaces if s["kind"] == "column")
    n_st = sum(1 for s in surfaces if s["kind"] == "step")
    print(f"Wrote {path}  (v3: {len(surfaces)} surface(s) — "
          f"{n_w} wall, {n_c} column, {n_st} step; "
          f"{len(openings_data)} opening(s), "
          f"{len(columns_data)} column(s))")


_dump_wall_json()


# ============================================================================
# HELPERS
# ============================================================================

ALIGN_MIN = (Align.MIN, Align.MIN, Align.MIN)


def point_in_polygon(pt, poly):
    x, y = pt
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def to_ccw(poly):
    n = len(poly)
    area = sum(poly[i][0] * poly[(i + 1) % n][1]
               - poly[(i + 1) % n][0] * poly[i][1]
               for i in range(n))
    return list(reversed(poly)) if area < 0 else list(poly)


def build_wall_ring(inner_pts_mm, wall_t_mm, height_mm, skip_edge=None):
    pts = to_ccw(inner_pts_mm)
    n = len(pts)
    edges = []
    for i in range(n):
        p1 = pts[i]
        p2 = pts[(i + 1) % n]
        if skip_edge is not None and skip_edge(p1, p2):
            edges.append(None)
        else:
            edges.append((p1, p2))

    solids = []
    for i in range(n):
        edge = edges[i]
        if edge is None:
            continue
        p1, p2 = edge
        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        L = hypot(dx, dy)
        if L < 1e-9:
            continue
        ux, uy = dx / L, dy / L

        prev = edges[(i - 1) % n]
        start_ext = 0.0
        if prev is not None:
            pp1, pp2 = prev
            pdx = pp2[0] - pp1[0]
            pdy = pp2[1] - pp1[1]
            if pdx * dy - pdy * dx > 0:
                start_ext = wall_t_mm

        nxt = edges[(i + 1) % n]
        end_ext = 0.0
        if nxt is not None:
            np1, np2 = nxt
            ndx = np2[0] - np1[0]
            ndy = np2[1] - np1[1]
            if dx * ndy - dy * ndx > 0:
                end_ext = wall_t_mm

        ext_p1 = (p1[0] - ux * start_ext, p1[1] - uy * start_ext)
        ext_p2 = (p2[0] + ux * end_ext, p2[1] + uy * end_ext)
        L_ext = L + start_ext + end_ext

        nx, ny = uy, -ux
        mx = (ext_p1[0] + ext_p2[0]) / 2 + nx * wall_t_mm / 2
        my = (ext_p1[1] + ext_p2[1]) / 2 + ny * wall_t_mm / 2

        angle = degrees(atan2(dy, dx))
        box = Box(L_ext, wall_t_mm, height_mm)
        box = box.rotate(Axis.Z, angle)
        solids.append(Pos(mx, my, height_mm / 2) * box)

    if not solids:
        return None
    result = solids[0]
    for s in solids[1:]:
        result = result + s
    return result


def inward_normal(p1, p2, poly, probe):
    x1, y1 = p1
    x2, y2 = p2
    dx, dy = x2 - x1, y2 - y1
    L = hypot(dx, dy)
    nx, ny = dy / L, -dx / L
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    for sign in (1, -1):
        if point_in_polygon((mx + sign * nx * probe, my + sign * ny * probe), poly):
            return sign * nx, sign * ny
    return nx, ny


def label(text, x, y, size, rotation=0.0):
    with _quiet():
        t = Text(text, font_size=size, font_path=FONT_PATH,
                 align=(Align.CENTER, Align.CENTER))
    if rotation:
        t = t.rotate(Axis.Z, rotation)
    return Pos(x, y, 0) * t


class Label:
    def __init__(self, text, preferred, target, size):
        self.text = text
        self.preferred = (float(preferred[0]), float(preferred[1]))
        self.target = target
        self.size = size
        self.hw = CHAR_ASPECT * size * len(text) / 2.0
        self.hh = LABEL_HEIGHT_FACT * size
        self.pos = [self.preferred[0], self.preferred[1]]


def dim_label(text, target, size, direction, arrow_size):
    hw = CHAR_ASPECT * size * len(text) / 2.0
    hh = LABEL_HEIGHT_FACT * size
    dx, dy = direction
    box_extent = abs(dx) * hw + abs(dy) * hh
    offset = box_extent + arrow_size
    preferred = (target[0] + dx * offset, target[1] + dy * offset)
    return Label(text, preferred, target, size)


def _seg_point_dist(p1, p2, q):
    px, py = q
    x1, y1 = p1
    x2, y2 = p2
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    if L2 < 1e-9:
        return hypot(px - x1, py - y1), (x1, y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    cx, cy = x1 + t * dx, y1 + t * dy
    return hypot(px - cx, py - cy), (cx, cy)


def relax_labels(labels, obstacles, iterations=LAYOUT_ITERATIONS,
                 spring=LAYOUT_SPRING, repulse=LAYOUT_REPULSE,
                 wall_push=LAYOUT_WALL_PUSH, margin=LAYOUT_MARGIN):
    n = len(labels)
    for _ in range(iterations):
        fx = [0.0] * n
        fy = [0.0] * n
        for i, l in enumerate(labels):
            fx[i] += (l.preferred[0] - l.pos[0]) * spring
            fy[i] += (l.preferred[1] - l.pos[1]) * spring
        for i in range(n):
            a = labels[i]
            for j in range(i + 1, n):
                b = labels[j]
                dx = b.pos[0] - a.pos[0]
                dy = b.pos[1] - a.pos[1]
                ox = (a.hw + b.hw + margin) - abs(dx)
                oy = (a.hh + b.hh + margin) - abs(dy)
                if ox > 0 and oy > 0:
                    if ox < oy:
                        s = 1.0 if dx >= 0 else -1.0
                        push = ox * repulse
                        fx[i] -= s * push
                        fx[j] += s * push
                    else:
                        s = 1.0 if dy >= 0 else -1.0
                        push = oy * repulse
                        fy[i] -= s * push
                        fy[j] += s * push
        for i, l in enumerate(labels):
            r = max(l.hw, l.hh)
            limit = r + margin
            for (p1, p2) in obstacles:
                d, closest = _seg_point_dist(p1, p2, l.pos)
                if 1e-6 < d < limit:
                    ux = (l.pos[0] - closest[0]) / d
                    uy = (l.pos[1] - closest[1]) / d
                    push = (limit - d) * wall_push
                    fx[i] += ux * push
                    fy[i] += uy * push
        for i, l in enumerate(labels):
            l.pos[0] += fx[i]
            l.pos[1] += fy[i]


def leader_from_box(center, hw, hh, target, arrow_size, pad=2.0):
    tx, ty = center
    wx, wy = target
    dx, dy = wx - tx, wy - ty
    L = hypot(dx, dy)
    if L < 1e-6:
        return []
    ux, uy = dx / L, dy / L
    t_x = hw / abs(ux) if abs(ux) > 1e-9 else float("inf")
    t_y = hh / abs(uy) if abs(uy) > 1e-9 else float("inf")
    t = min(t_x, t_y) + pad
    if t >= L:
        return []
    sx = tx + ux * t
    sy = ty + uy * t
    px, py = -uy, ux
    bx = wx - ux * arrow_size
    by = wy - uy * arrow_size
    w1 = (bx + px * arrow_size * 0.5, by + py * arrow_size * 0.5)
    w2 = (bx - px * arrow_size * 0.5, by - py * arrow_size * 0.5)
    return [Line((sx, sy), (wx, wy)), Line(w1, (wx, wy)), Line(w2, (wx, wy))]


def dimension(p1, p2, poly, gap):
    (x1, y1), (x2, y2) = p1, p2
    ix, iy = inward_normal(p1, p2, poly, gap)
    nx, ny = -ix, -iy
    ex1, ey1 = x1 + nx * gap, y1 + ny * gap
    ex2, ey2 = x2 + nx * gap, y2 + ny * gap
    segs = [((x1, y1), (ex1, ey1)),
            ((x2, y2), (ex2, ey2)),
            ((ex1, ey1), (ex2, ey2))]
    lines = [Line(a, b) for a, b in segs]
    mid = ((ex1 + ex2) / 2, (ey1 + ey2) / 2)
    return lines, segs, mid


def simple_dimension(p1, p2, offset_dir, gap):
    (x1, y1), (x2, y2) = p1, p2
    nx, ny = offset_dir
    ex1, ey1 = x1 + nx * gap, y1 + ny * gap
    ex2, ey2 = x2 + nx * gap, y2 + ny * gap
    segs = [((x1, y1), (ex1, ey1)),
            ((x2, y2), (ex2, ey2)),
            ((ex1, ey1), (ex2, ey2))]
    lines = [Line(a, b) for a, b in segs]
    dim_mid = ((ex1 + ex2) / 2, (ey1 + ey2) / 2)
    return lines, segs, dim_mid


def make_wall_opening(p1, p2, poly, offset_along, width, z0, height,
                      wall_t, pad=30.0):
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    L = hypot(dx, dy)
    ux, uy = dx / L, dy / L
    ix, iy = inward_normal(p1, p2, poly, wall_t)
    ox, oy = -ix, -iy
    cx = p1[0] + ux * (offset_along + width / 2) + ox * (wall_t / 2)
    cy = p1[1] + uy * (offset_along + width / 2) + oy * (wall_t / 2)
    cz = z0 + height / 2
    angle = degrees(atan2(dy, dx))
    box = Box(width, wall_t + 2 * pad, height)
    box = box.rotate(Axis.Z, angle)
    return Pos(cx, cy, cz) * box


# ============================================================================
# NOTES BLOCK
# ============================================================================

def build_notes_lines():
    pairs = [
        ("Room height",   f"{CONSTANTS['WALL_HEIGHT']:g}"),
        ("Step rise",     f"{max(s['_height'] for s in STEPS):g}"
                          if STEPS else "—"),
        ("Wall thickness", f"{CONSTANTS['WALL_THICK']:g}"),
        ("Floor slab",    f"{CONSTANTS['FLOOR_THICK']:g}"),
    ]
    key_w = max(len(k) for k, _ in pairs)
    body = [f"{k.ljust(key_w)}  {v}" for k, v in pairs]
    title = "GENERAL NOTES"
    rule = "=" * len(title)
    width = max([len(title)] + [len(b) for b in body])
    title = title.ljust(width)
    rule = rule.ljust(width)
    body = [b.ljust(width) for b in body]
    return [title, rule] + body


def place_notes_block(lines, font_size, obstacles, labels):
    if not lines:
        return [], None
    line_h = font_size * NOTES_LINE_SPACING
    block_w = CHAR_ASPECT * font_size * max(len(l) for l in lines)
    block_h = line_h * len(lines)
    margin = font_size * NOTES_GAP_FACTOR

    xs, ys = [], []
    for (p1, p2) in obstacles:
        xs.append(p1[0]); xs.append(p2[0])
        ys.append(p1[1]); ys.append(p2[1])
    for lb in labels:
        xs.append(lb.pos[0] - lb.hw); xs.append(lb.pos[0] + lb.hw)
        ys.append(lb.pos[1] - lb.hh); ys.append(lb.pos[1] + lb.hh)
    if not xs:
        return [], None
    bx0, by0, bx1, by1 = min(xs), min(ys), max(xs), max(ys)

    gx0 = bx0 - 2 * (block_w + margin)
    gy0 = by0 - 2 * (block_h + margin)
    gx1 = bx1 + 2 * (block_w + margin)
    gy1 = by1 + 2 * (block_h + margin)

    min_cell = max(2.0, min(block_w, block_h) / 20.0)
    max_dim = max(gx1 - gx0, gy1 - gy0, 1.0)
    cell = max(min_cell, max_dim / NOTES_MAX_CELLS)

    nx = int((gx1 - gx0) / cell) + 1
    ny = int((gy1 - gy0) / cell) + 1
    grid = bytearray(nx * ny)

    def mark(x0, y0, x1, y1):
        ix0 = max(0, int((x0 - gx0) / cell))
        iy0 = max(0, int((y0 - gy0) / cell))
        ix1 = min(nx - 1, int((x1 - gx0) / cell))
        iy1 = min(ny - 1, int((y1 - gy0) / cell))
        for iy in range(iy0, iy1 + 1):
            base = iy * nx
            for ix in range(ix0, ix1 + 1):
                grid[base + ix] = 1

    r = NOTES_LINE_MARGIN
    for (p1, p2) in obstacles:
        mark(min(p1[0], p2[0]) - r, min(p1[1], p2[1]) - r,
             max(p1[0], p2[0]) + r, max(p1[1], p2[1]) + r)
    for lb in labels:
        mark(lb.pos[0] - lb.hw, lb.pos[1] - lb.hh,
             lb.pos[0] + lb.hw, lb.pos[1] + lb.hh)

    stride = nx + 1
    integral = [0] * (stride * (ny + 1))
    for iy in range(ny):
        base = iy * nx
        ibase = (iy + 1) * stride
        pbase = iy * stride
        s = 0
        for ix in range(nx):
            if grid[base + ix]:
                s += 1
            integral[ibase + ix + 1] = integral[pbase + ix + 1] + s

    def rect_sum(ix0, iy0, ix1, iy1):
        return (integral[iy1 * stride + ix1]
                - integral[iy0 * stride + ix1]
                - integral[iy1 * stride + ix0]
                + integral[iy0 * stride + ix0])

    bw_cells = int(block_w / cell) + 1
    bh_cells = int(block_h / cell) + 1
    bcx = (bx0 + bx1) / 2.0
    bcy = (by0 + by1) / 2.0
    bhw = max(1.0, (bx1 - bx0) / 2.0)
    bhh = max(1.0, (by1 - by0) / 2.0)

    best_inside = None
    best_outside = None
    for iy in range(0, ny - bh_cells + 1):
        for ix in range(0, nx - bw_cells + 1):
            if rect_sum(ix, iy, ix + bw_cells, iy + bh_cells) > 0:
                continue
            cx = gx0 + (ix + bw_cells / 2.0) * cell
            cy = gy0 + (iy + bh_cells / 2.0) * cell
            x0 = cx - block_w / 2.0; x1 = cx + block_w / 2.0
            y0 = cy - block_h / 2.0; y1 = cy + block_h / 2.0
            dx_out = max(0.0, bx0 - x0) + max(0.0, x1 - bx1)
            dy_out = max(0.0, by0 - y0) + max(0.0, y1 - by1)
            expansion = dx_out + dy_out
            nx_off = (cx - bcx) / bhw
            ny_off = (cy - bcy) / bhh
            corner = (nx_off * nx_off + ny_off * ny_off) ** 0.5
            if expansion == 0:
                if best_inside is None or corner > best_inside[0]:
                    best_inside = (corner, cx, cy)
            else:
                key = (expansion, corner)
                if best_outside is None or key < (best_outside[0], best_outside[1]):
                    best_outside = (expansion, corner, cx, cy)

    if best_inside is not None:
        _, cx, cy = best_inside
        vert = "top" if cy > bcy else "bottom"
        horiz = "right" if cx > bcx else "left"
        name = f"inside {vert}-{horiz}"
    elif best_outside is not None:
        _, _, cx, cy = best_outside
        vert = "top" if cy > bcy else "bottom"
        horiz = "right" if cx > bcx else "left"
        name = f"outside {vert}-{horiz}"
    else:
        cx = bx1 + margin + block_w / 2.0
        cy = by0 - margin - block_h / 2.0
        name = "fallback (no free pocket)"

    labels_out = []
    top_y = cy + block_h / 2.0
    for i, line in enumerate(lines):
        ly = top_y - line_h * (i + 0.5)
        lw = CHAR_ASPECT * font_size * len(line)
        lx = (cx - block_w / 2.0) + lw / 2.0
        labels_out.append(Label(line, (lx, ly), None, font_size))
    return labels_out, name


# ============================================================================
# FEATURES / HOST-FINDING
# ============================================================================

def collect_features():
    feats = []
    n = len(INNER_PTS)
    for i in range(n):
        p1 = INNER_PTS[i]
        p2 = INNER_PTS[(i + 1) % n]
        tag = MAIN_TAGS[i] if i < len(MAIN_TAGS) else None
        name = tag if tag else f"wall[{i + 1}]"
        feats.append((name, p1, p2))
    for c in COLUMNS:
        x0, y0, x1, y1 = c["_box"]
        feats.append((f"{c['tag']}.W", (x0, y0), (x0, y1)))
        feats.append((f"{c['tag']}.E", (x1, y0), (x1, y1)))
        feats.append((f"{c['tag']}.S", (x0, y0), (x1, y0)))
        feats.append((f"{c['tag']}.N", (x0, y1), (x1, y1)))
    for sr in SMALL_ROOMS:
        poly = sr["_pts"]
        tags = sr["_wall_tags"]
        for i in range(len(poly)):
            p1 = poly[i]
            p2 = poly[(i + 1) % len(poly)]
            tag = tags[i] if i < len(tags) else None
            name = tag if tag else f"{sr['tag']}_edge[{i + 1}]"
            feats.append((name, p1, p2))
    return feats


def find_host(edge, features,
              parallel_tol=HOST_PARALLEL_TOL,
              collinear_tol=HOST_COLLINEAR_TOL,
              overlap_min=HOST_OVERLAP_MIN):
    ex1, ey1 = edge[0]
    ex2, ey2 = edge[1]
    edx, edy = ex2 - ex1, ey2 - ey1
    elen = hypot(edx, edy)
    if elen < 1e-6:
        return None
    ux, uy = edx / elen, edy / elen
    best_name = None
    best_score = None
    for name, fp1, fp2 in features:
        fx1, fy1 = fp1
        fx2, fy2 = fp2
        fdx, fdy = fx2 - fx1, fy2 - fy1
        flen = hypot(fdx, fdy)
        if flen < 1e-6:
            continue
        fux, fuy = fdx / flen, fdy / flen
        if abs(ux * fuy - uy * fux) > parallel_tol:
            continue
        dist = abs((ex1 - fx1) * fuy - (ey1 - fy1) * fux)
        if dist > collinear_tol:
            continue
        t1 = ((ex1 - fx1) * fux + (ey1 - fy1) * fuy) / flen
        t2 = ((ex2 - fx1) * fux + (ey2 - fy1) * fuy) / flen
        tmin, tmax = min(t1, t2), max(t1, t2)
        if tmax < -overlap_min or tmin > 1.0 + overlap_min:
            continue
        overlap = min(tmax, 1.0) - max(tmin, 0.0)
        if overlap < overlap_min:
            continue
        score = (-overlap, -flen, dist)
        if best_score is None or score < best_score:
            best_score = score
            best_name = name
    return best_name


def clip_seg_against_boxes(seg, boxes):
    segs = [seg]
    for (bx0, by0, bx1, by1) in boxes:
        new_segs = []
        for s in segs:
            x1, y1 = s[0]
            x2, y2 = s[1]
            if abs(y2 - y1) < 1e-6:
                y = y1
                if by0 <= y <= by1:
                    xmin, xmax = min(x1, x2), max(x1, x2)
                    if xmax < bx0 or xmin > bx1:
                        new_segs.append(s)
                    else:
                        if xmin < bx0:
                            new_segs.append(((xmin, y), (bx0, y)))
                        if xmax > bx1:
                            new_segs.append(((bx1, y), (xmax, y)))
                else:
                    new_segs.append(s)
            elif abs(x2 - x1) < 1e-6:
                x = x1
                if bx0 <= x <= bx1:
                    ymin, ymax = min(y1, y2), max(y1, y2)
                    if ymax < by0 or ymin > by1:
                        new_segs.append(s)
                    else:
                        if ymin < by0:
                            new_segs.append(((x, ymin), (x, by0)))
                        if ymax > by1:
                            new_segs.append(((x, by1), (x, ymax)))
                else:
                    new_segs.append(s)
            else:
                new_segs.append(s)
        segs = new_segs
    return segs


def merged_step_regions(steps):
    if not steps:
        return None
    result = None
    for s in steps:
        face = make_face(Polyline(*s["_outline"], close=True))
        result = face if result is None else result + face
    return result


def sketch_boundary_segments(sketch):
    segs = []
    for e in sketch.edges():
        a = e.position_at(0)
        b = e.position_at(1)
        segs.append(((a.X, a.Y), (b.X, b.Y)))
    return segs


# ============================================================================
# DESCRIPTION
# ============================================================================

def describe_room():
    W = 68
    eq = "=" * W
    dash = "-" * W
    print(eq)
    print("ROOM DESCRIPTION — lego-style, AI-parseable")
    print(eq)
    print("A room is a set of composable pieces.  To recreate this")
    print("room, populate the MEASUREMENTS block of room.py.")
    print()
    print(dash); print("GLOBAL"); print(dash)
    print(f"  unit             : {UNIT_MM} mm per unit")
    for k, v in CONSTANTS.items():
        print(f"  {k:<17}: {v:g} units")
    print()
    print(dash); print("PIECE 1 — main perimeter"); print(dash)
    n = len(INNER_PTS)
    for i in range(n):
        p1 = INNER_PTS[i]
        p2 = INNER_PTS[(i + 1) % n]
        dx = p2[0] - p1[0]; dy = p2[1] - p1[1]
        L = hypot(dx, dy) / MM
        tag = MAIN_TAGS[i] if i < len(MAIN_TAGS) else None
        tag_s = tag if tag else "--"
        print(f"  {i+1}   {tag_s:<4}  L={L:>7.1f}")
    print()
    print(dash); print("TOTALS"); print(dash)
    main_tagged = [t for t in MAIN_TAGS if t]
    print(f"  walls (main)  : {len(main_tagged)}  ({', '.join(main_tagged)})")
    print()


if DESCRIBE_ONLY:
    describe_room()
    raise SystemExit(0)


# ============================================================================
# BUILD 3D
# ============================================================================

WALL_H_MM  = WALL_HEIGHT * MM
WALL_T_MM  = WALL_THICK  * MM
FLOOR_T_MM = FLOOR_THICK * MM
CUT_Z_MM   = PLAN_CUT_Z  * MM

inner_face = make_face(Polyline(*INNER_PTS, close=True))
outer_face = offset(inner_face, WALL_T_MM, kind=Kind.INTERSECTION)

walls = extrude(outer_face, WALL_H_MM) - extrude(inner_face, WALL_H_MM)
floor = Pos(0, 0, -FLOOR_T_MM) * extrude(outer_face, FLOOR_T_MM)

platform = None
for s in STEPS:
    face = make_face(Polyline(*s["_outline"], close=True))
    piece = extrude(face, s["_height"] * MM)
    platform = piece if platform is None else platform + piece

room = walls + floor
if platform is not None:
    room = room + platform
for c in COLUMNS:
    x0, y0, x1, y1 = c["_box"]
    room = room + Pos(x0, y0, 0) * Box(x1 - x0, y1 - y0, WALL_H_MM,
                                       align=ALIGN_MIN)

main_wall_features = [
    (MAIN_TAGS[i] if MAIN_TAGS[i] else f"wall[{i + 1}]",
     INNER_PTS[i], INNER_PTS[(i + 1) % len(INNER_PTS)])
    for i in range(len(INNER_PTS))
]
for sr in SMALL_ROOMS:
    sr_ring = build_wall_ring(
        sr["_pts"], WALL_T_MM, WALL_H_MM,
        skip_edge=lambda p1, p2: find_host(
            (p1, p2), main_wall_features) is not None,
    )
    if sr_ring is not None:
        room = room + sr_ring

for d in OPENINGS:
    room -= make_wall_opening(d["_wall_p1"], d["_wall_p2"], INNER_PTS,
                              d["_off_mm"], d["_w_mm"],
                              d["_sill_mm"], d["_h_mm"], WALL_T_MM)

# ============================================================================
# EXPORT 3D
# ============================================================================

export_step(room, "room.step")
export_stl(room, "room.stl")

# ============================================================================
# 2D FLOOR PLAN
# ============================================================================

plan = section(room, Plane.XY.offset(CUT_Z_MM))
plan = plan.moved(Location((0, 0, -CUT_Z_MM)))

n = len(INNER_PTS)
features = collect_features()
column_boxes = [c["_box"] for c in COLUMNS]

dim_lines, dim_midpoints = [], {}
sr_dim_lines, sr_dim_midpoints = [], {}
col_dim_lines = []
obstacles = []

for i in range(n):
    entry = WALL_DIMS_RESOLVED[i]
    if entry is None:
        continue
    value, extend_to = entry
    p1 = INNER_PTS[i]
    p2 = INNER_PTS[(i + 1) % n]
    if extend_to is not None:
        dx = p2[0] - p1[0]; dy = p2[1] - p1[1]
        if abs(dy) > abs(dx):
            p2 = (p2[0], extend_to)
        else:
            p2 = (extend_to, p2[1])
    lines, segs, mid = dimension(p1, p2, INNER_PTS, DIM_GAP * MM)
    dim_lines += lines
    obstacles += segs
    dim_midpoints[MAIN_TAGS[i]] = mid

for i in range(n):
    obstacles.append((INNER_PTS[i], INNER_PTS[(i + 1) % n]))

for sr in SMALL_ROOMS:
    pts = sr["_pts"]
    tags = sr["_wall_tags"]
    dims = sr["dims"]
    for i in range(len(pts)):
        dim_value = dims[i] if i < len(dims) else None
        if dim_value is None:
            continue
        p1 = pts[i]
        p2 = pts[(i + 1) % len(pts)]
        lines, segs, mid = dimension(p1, p2, pts, SR_DIM_GAP * MM)
        sr_dim_lines += lines
        obstacles += segs
        tag = tags[i]
        if tag:
            sr_dim_midpoints[tag] = mid
    for i in range(len(pts)):
        obstacles.append((pts[i], pts[(i + 1) % len(pts)]))

step_regions = merged_step_regions(STEPS)

column_boxes_clip = [
    (x0 + COLUMN_CLIP_EPS, y0 + COLUMN_CLIP_EPS,
     x1 - COLUMN_CLIP_EPS, y1 - COLUMN_CLIP_EPS)
    for (x0, y0, x1, y1) in column_boxes
]

step_lines = []
step_region_centroids = []
if step_regions is not None:
    for seg in sketch_boundary_segments(step_regions):
        for clipped in clip_seg_against_boxes(seg, column_boxes_clip):
            step_lines.append(Line(*clipped))
        obstacles.append(seg)
    for f in step_regions.faces():
        c = f.center()
        step_region_centroids.append((c.X, c.Y))

col_outline_lines = []
for c in COLUMNS:
    x0, y0, x1, y1 = c["_box"]
    for edge in [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                 ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]:
        col_outline_lines.append(Line(*edge))
        obstacles.append(edge)

all_labels = []
for i in range(n):
    wall_tag = MAIN_TAGS[i]
    if wall_tag is None or WALL_DIMS_RESOLVED[i] is None:
        continue
    p1 = INNER_PTS[i]
    p2 = INNER_PTS[(i + 1) % n]
    mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
    tag_name = f"{TAG_PREFIX}{wall_tag[1:]}"
    if tag_name in TAG_OVERRIDES:
        preferred = TAG_OVERRIDES[tag_name]
    else:
        ix, iy = inward_normal(p1, p2, INNER_PTS, TAG_INSET * MM)
        preferred = (mx + ix * TAG_INSET * MM,
                     my + iy * TAG_INSET * MM)
    text = f"{tag_name} = {WALL_DIMS_RESOLVED[i][0]:g}"
    target = dim_midpoints.get(wall_tag, (mx, my))
    all_labels.append(Label(text, preferred, target, LABEL_SIZE * MM))

for sr in SMALL_ROOMS:
    pts = sr["_pts"]
    tags = sr["_wall_tags"]
    dims = sr["dims"]
    for i, tag in enumerate(tags):
        if not tag:
            continue
        p1 = pts[i]; p2 = pts[(i + 1) % len(pts)]
        ix, iy = inward_normal(p1, p2, pts, SR_TAG_INSET * MM)
        mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
        preferred = (mx + ix * SR_TAG_INSET * MM,
                     my + iy * SR_TAG_INSET * MM)
        text = f"{tag} = {dims[i]}"
        target = sr_dim_midpoints.get(tag, (mx, my))
        all_labels.append(Label(text, preferred, target, SR_LABEL_SIZE * MM))

for d in OPENINGS:
    p1, p2 = d["_p1"], d["_p2"]
    L = hypot(p2[0] - p1[0], p2[1] - p1[1])
    ux, uy = (p2[0] - p1[0]) / L, (p2[1] - p1[1]) / L
    t_mid = d["_off_mm"] + d["_w_mm"] / 2
    wall_p1 = d["_wall_p1"]
    wx = wall_p1[0] + (p2[0] - wall_p1[0]) * t_mid / L
    wy = wall_p1[1] + (p2[1] - wall_p1[1]) * t_mid / L
    ix, iy = inward_normal(d["_wall_p1"], d["_wall_p2"], INNER_PTS,
                           DOOR_LABEL_INSET * MM)
    if d["tag"] in TAG_OVERRIDES:
        preferred = TAG_OVERRIDES[d["tag"]]
    else:
        preferred = (wx + ix * DOOR_LABEL_INSET * MM,
                     wy + iy * DOOR_LABEL_INSET * MM)
    all_labels.append(Label(d["tag"], preferred, (wx, wy), LABEL_SIZE * MM))

for c in COLUMNS:
    x0, y0, x1, y1 = c["_box"]
    all_labels.append(Label(c["tag"], ((x0 + x1) / 2, (y0 + y1) / 2), None,
                            COLUMN_LABEL_SIZE * MM))

for c in COLUMNS:
    x0, y0, x1, y1 = c["_box"]
    lines, segs, dim_mid = simple_dimension(
        (x0, y0), (x1, y0), (0, -1), COL_DIM_GAP * MM)
    col_dim_lines += lines
    obstacles += segs
    all_labels.append(dim_label(f"{(x1 - x0) / MM:g}", dim_mid,
                                DIM_SIZE * MM, (0, -1), ARROW_SIZE * MM))
    lines, segs, dim_mid = simple_dimension(
        (x1, y0), (x1, y1), (1, 0), COL_DIM_GAP * MM)
    col_dim_lines += lines
    obstacles += segs
    all_labels.append(dim_label(f"{(y1 - y0) / MM:g}", dim_mid,
                                DIM_SIZE * MM, (1, 0), ARROW_SIZE * MM))

for (cx, cy) in step_region_centroids:
    all_labels.append(Label("STEP", (cx, cy), None, STEP_LABEL_SIZE * MM))

relax_labels(all_labels, obstacles)

notes_lines = build_notes_lines()
notes_labels, notes_placement = place_notes_block(
    notes_lines, NOTES_LABEL_SIZE * MM, obstacles, all_labels)

merged_text = []
merged_arrows = []
for lb in all_labels:
    merged_text.append(label(lb.text, lb.pos[0], lb.pos[1], lb.size))
    if lb.target is not None:
        merged_arrows += leader_from_box(lb.pos, lb.hw, lb.hh, lb.target,
                                         ARROW_SIZE * MM)
for lb in notes_labels:
    merged_text.append(label(lb.text, lb.pos[0], lb.pos[1], lb.size))

svg = ExportSVG(scale=0.1, margin=20, line_weight=0.5)
svg.add_layer("plan",  line_color=WALL_COLOR, line_weight=0.7)
svg.add_layer("dim",   line_color=LINE_COLOR, line_weight=0.3)
svg.add_layer("col",   line_color=LINE_COLOR, line_weight=0.6)
svg.add_layer("small", line_color=LINE_COLOR, line_weight=0.5)
svg.add_layer("step",  line_color=WALL_COLOR, line_weight=0.5)
svg.add_layer("text",  line_color=TEXT_COLOR, fill_color=TEXT_COLOR,
              line_weight=0.3)

svg.add_shape(plan, layer="plan")
for s in col_outline_lines: svg.add_shape(s, layer="plan")
for s in dim_lines:         svg.add_shape(s, layer="dim")
for s in col_dim_lines:     svg.add_shape(s, layer="col")
for s in sr_dim_lines:      svg.add_shape(s, layer="small")
for s in step_lines:        svg.add_shape(s, layer="step")
for s in merged_text:       svg.add_shape(s, layer="text")
for s in merged_arrows:     svg.add_shape(s, layer="text")
svg.write("floor_plan.svg")

png_ok = True
try:
    with _quiet():
        import cairosvg
        cairosvg.svg2png(url="floor_plan.svg",
                         write_to="floor_plan.png",
                         background_color=PNG_BG,
                         scale=PNG_SCALE)
except ImportError:
    png_ok = False

# ============================================================================
# REPORT
# ============================================================================

bb = room.bounding_box()
area_mm2 = abs(sum(
    INNER_PTS[i][0] * INNER_PTS[(i + 1) % n][1]
    - INNER_PTS[(i + 1) % n][0] * INNER_PTS[i][1]
    for i in range(n)
)) / 2

main_tagged = [t for t in MAIN_TAGS if t is not None]
sr_tagged = []
for sr in SMALL_ROOMS:
    sr_tagged += [t for t in sr["_wall_tags"] if t]
print(f"Walls (main)   : {len(main_tagged)}  ({', '.join(main_tagged)})")
print(f"Walls (SR)     : {', '.join(sr_tagged)}")
print(f"Steps          : {len(STEPS)} primitives  "
      f"→ {len(step_region_centroids)} merged region(s)")
print(f"Labels total   : {len(all_labels)} + {len(notes_labels)} notes lines")
print(f"Notes placement: {notes_placement}")
print(f"Wall height    : {WALL_HEIGHT:g} units ({WALL_H_MM:.0f} mm)")
print(f"Wall thickness : {WALL_THICK:.0f} units ({WALL_T_MM:.0f} mm)")
for c in COLUMNS:
    x0, y0, x1, y1 = c["_box"]
    print(f"Column {c['tag']:<3}     : {(x1-x0)/MM:g} × {(y1-y0)/MM:g} "
          f"units  ({c['note']})")
for d in OPENINGS:
    print(f"Open {d['tag']:<5}     : on {d['wall_tag']}, "
          f"starts {d['offset']:g} units from its start, "
          f"width {d['_w_mm']/MM:g} units, height {d['height']:g} units, "
          f"sill {d.get('sill', 0)}")
print(f"Interior area  : {area_mm2 / 1e6:.2f} m² (main room only)")
print(f"Overall bbox   : {bb.size.X:.0f} × {bb.size.Y:.0f} × {bb.size.Z:.0f} mm")
print("Wrote room.step, room.stl, room_walls.json, floor_plan.svg"
      + (", floor_plan.png" if png_ok else ""))
if not png_ok:
    print("  (skipped PNG — install cairosvg: pip install cairosvg)")
