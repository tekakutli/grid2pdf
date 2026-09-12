"""
cable_playground.py — plan cable / pipe routes in a room.

    1. run room.py                → produces room.step (+ room_walls.json)
    2. run cable_playground.py    → writes cable_playground.html and serves it.
    3. open the URL that this script prints.

Data model
----------
Anchor: a point in the room.  Two spaces:
    floor     : { x, y }  (+ optional gridId/gridAxis)
    wall-edge : { segIdx, t, v }

Cable: an ordered list of anchor ids.  Sharing an anchor between cables is
what "connecting" means; drag a vertex, every cable referencing that anchor
re-renders from the same source.

Junction graph (derived, computed once at load)
-----------------------------------------------
Nodes  — physical room corners.
Terminals — (segIdx, side) pairs, side ∈ {0, 1}.
Two terminals are co-located when their plan XY is within 3 mm.

Every "does X share a physical point with Y" question routes through this
graph: corner-drag height sync, true-cable grouping pass 2, focus-mode
neighbours, route planner chain resolution.

Segment adjacency: segIdx → [neighbourSegIdx, ...], derived from junctions.
Route planner: BFS over precomputed adjacency; entry/exit sides resolved
in O(1) via ADJ_DETAIL.

Wormhole route planner
----------------------
Every wall-view click is planned against the previous anchor.  When the
click lands on a different segment, the planner:
    1. BFS the segment-adjacency graph.
    2. Build a virtual 1-D frame (segments laid end to end).
    3. Compute the straight line, split at each segment boundary.
    4. Emit a wall-edge anchor per boundary on each side at the
       interpolated height.
The resulting cable's consecutive anchors at a boundary are physically
coincident — the renderer draws a green hop arc wherever the strip can't
lay them adjacent.

Focus mode
----------
While drawing on the unfolded view, the strip narrows to the focused wall
and its physical neighbours.  Focused wall keeps its u; neighbours re-lay
immediately left / right; everything else hides.  Visual aid only — the
route planner works whether or not focus is engaged.

Alt+click adopt / merge
-----------------------
A plain click near an existing endpoint just shares the anchor and starts
a fresh cable — the two are still one physical cable through true-cable
grouping.  Alt+click adopts (continue) that cable or merges the current
drawing into it.  This makes merge an explicit gesture.

Save format v3
--------------
wall-edge anchors persist as { t, v, segRef: {a,b}, world: [x,y] }, so they
re-bind to whichever new segment occupies the same footprint at load,
independently of how room_walls.json re-orders segments on the next build.
Legacy 'wall' anchors from v1/v2/v3 saves are upgraded to wall-edge on load.
"""

import json
import os
import threading
import webbrowser
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from math import hypot


@contextmanager
def _quiet():
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
    from build123d import import_step, Plane, section, offset, Kind


STEP_FILE   = "room.step"
WALLS_FILE  = "room_walls.json"
HTML_FILE   = "cable_playground.html"
STATE_FILE  = "room_layout.json"
WALL_CUT_Z  = 1200.0
STEP_LOW_Z  = 100.0
WALL_HEIGHT = 3000.0

UNFOLD_CLOSE_R = 600.0

SERVE        = True
PORT         = 8765
OPEN_BROWSER = True


# ---------------------------------------------------------------------------
# 1. Geometry extraction
# ---------------------------------------------------------------------------

def wire_to_polygon(wire, tol=0.01):
    edges = list(wire.edges())
    if not edges:
        return []
    e0 = edges.pop(0)
    pts = [e0.position_at(0), e0.position_at(1)]
    end = pts[-1]
    while edges:
        for i, e in enumerate(edges):
            s = e.position_at(0)
            t = e.position_at(1)
            if (s - end).length < tol:
                pts.append(t); end = t; edges.pop(i); break
            if (t - end).length < tol:
                pts.append(s); end = s; edges.pop(i); break
        else:
            break
    if len(pts) > 1 and (pts[0] - pts[-1]).length < tol:
        pts.pop()
    return [[float(p.X), float(p.Y)] for p in pts]


def faces_to_polys(faces):
    out = []
    for f in faces:
        outer = wire_to_polygon(f.outer_wire())
        if not outer:
            continue
        holes = []
        for w in f.inner_wires():
            h = wire_to_polygon(w)
            if h:
                holes.append(h)
        out.append({"outer": outer, "holes": holes})
    return out


def signed_area(ring):
    n = len(ring)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s * 0.5


def polygon_area(ring):
    return abs(signed_area(ring))


def extract_geometry():
    if not os.path.exists(STEP_FILE):
        raise SystemExit(f"{STEP_FILE} not found — run room.py first.")

    with _quiet():
        room = import_step(STEP_FILE)
        wall_sketch = section(room, Plane.XY.offset(WALL_CUT_Z))
        low_sketch  = section(room, Plane.XY.offset(STEP_LOW_Z))

    wall_polys = faces_to_polys(wall_sketch.faces())

    step_polys = []
    try:
        with _quiet():
            step_sketch = low_sketch - wall_sketch
        step_polys = faces_to_polys(step_sketch.faces())
    except Exception as e:
        print(f"  (could not compute step areas: {e})")
        step_polys = []

    if not wall_polys:
        raise SystemExit("Section returned no faces — is room.step valid?")

    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    for face in wall_polys + step_polys:
        for ring in [face["outer"], *face["holes"]]:
            for x, y in ring:
                min_x = min(min_x, x); min_y = min(min_y, y)
                max_x = max(max_x, x); max_y = max(max_y, y)

    geom = {
        "faces":     wall_polys,
        "stepFaces": step_polys,
        "bounds":    {"minX": min_x, "minY": min_y,
                      "maxX": max_x, "maxY": max_y},
        "cutZ":      WALL_CUT_Z,
        "columns":   [],
        "openings":  [],
    }

    if os.path.exists(WALLS_FILE):
        try:
            with open(WALLS_FILE, "r", encoding="utf-8") as f:
                wdata = json.load(f)
            uw = _unfold_from_walls_json(wdata)
            geom["unfoldedWall"] = uw
            geom["columns"]      = wdata.get("columns", [])
            geom["openings"]     = wdata.get("openings", [])

            n_step = sum(1 for s in uw["segments"]
                         if s.get("kind") == "step")
            n_col  = sum(1 for s in uw["segments"]
                         if s.get("kind") == "column")
            print(f"  unfolded-wall source  : {WALLS_FILE} v"
                  f"{wdata.get('version', 1)} "
                  f"({len(uw['segments'])} segment(s) — "
                  f"{n_col} column, {n_step} step; "
                  f"perimeter {uw['totalU']:.0f} mm)")
            return geom
        except Exception as e:
            print(f"  (could not read {WALLS_FILE}: {e})")
            print(f"  falling back to geometric extraction from {STEP_FILE}")
    else:
        print(f"  note: {WALLS_FILE} not found — falling back to "
              f"geometric extraction from {STEP_FILE}")

    uw, method = compute_unfold(wall_sketch, geom)
    geom["unfoldedWall"] = uw
    print(f"  unfolded-wall method  : {method}")
    return geom


# ---------------------------------------------------------------------------
# Spatial ordering helpers for the unfolded wall strip
# ---------------------------------------------------------------------------

def _pt_seg_dist(px, py, x1, y1, x2, y2):
    dx = x2 - x1; dy = y2 - y1
    l2 = dx * dx + dy * dy
    if l2 < 1e-9:
        return hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / l2
    t = max(0.0, min(1.0, t))
    return hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _footprints_adjacent(g1, g2, tol=20.0):
    a1, b1 = g1["a"], g1["b"]
    a2, b2 = g2["a"], g2["b"]
    for p in (a1, b1):
        for q in (a2, b2):
            if hypot(p[0] - q[0], p[1] - q[1]) < tol:
                return True
    if _pt_seg_dist(a1[0], a1[1], a2[0], a2[1], b2[0], b2[1]) < tol: return True
    if _pt_seg_dist(b1[0], b1[1], a2[0], a2[1], b2[0], b2[1]) < tol: return True
    if _pt_seg_dist(a2[0], a2[1], a1[0], a1[1], b1[0], b1[1]) < tol: return True
    if _pt_seg_dist(b2[0], b2[1], a1[0], a1[1], b1[0], b1[1]) < tol: return True
    return False


def _order_footprints_spatially(footprints_geom, original_order):
    keys = list(original_order)
    n = len(keys)
    if n <= 1:
        return keys

    pts = {k: (footprints_geom[k]["a"], footprints_geom[k]["b"]) for k in keys}

    def endpoint_dist(k, pt):
        a, b = pts[k]
        return min(hypot(a[0] - pt[0], a[1] - pt[1]),
                   hypot(b[0] - pt[0], b[1] - pt[1]))

    def on_seg_dist(k, pt):
        a, b = pts[k]
        return _pt_seg_dist(pt[0], pt[1], a[0], a[1], b[0], b[1])

    def proximity(k, pt):
        return min(endpoint_dist(k, pt), on_seg_dist(k, pt))

    def far_end(k, pt):
        a, b = pts[k]
        da = hypot(a[0] - pt[0], a[1] - pt[1])
        db = hypot(b[0] - pt[0], b[1] - pt[1])
        return b if da < db else a

    adj = {k: set() for k in keys}
    for i in range(n):
        ki = keys[i]
        for j in range(i + 1, n):
            kj = keys[j]
            if _footprints_adjacent(footprints_geom[ki], footprints_geom[kj]):
                adj[ki].add(kj)
                adj[kj].add(ki)

    start = keys[0]
    for k in keys:
        if len(adj[k]) == 1:
            start = k
            break

    visited = {start}
    order = [start]
    cur = start
    cur_end = pts[cur][1]

    while len(order) < n:
        best = None
        best_d = float("inf")
        for nb in adj[cur]:
            if nb in visited:
                continue
            d = proximity(nb, cur_end)
            if d < 20.0 and d < best_d:
                best = nb; best_d = d
        if best is None:
            for nb in adj[cur]:
                if nb in visited:
                    continue
                d = endpoint_dist(nb, cur_end)
                if d < best_d:
                    best = nb; best_d = d
        if best is None:
            for k in keys:
                if k in visited:
                    continue
                d = endpoint_dist(k, cur_end)
                if d < best_d:
                    best = k; best_d = d
        if best is None:
            break
        cur_end = far_end(best, cur_end)
        visited.add(best)
        order.append(best)
        cur = best

    return order


def _unfold_from_walls_json(wdata):
    version = int(wdata.get("version", 1))
    wh_default = float(wdata.get("wall_height_mm", WALL_HEIGHT))

    raw = []
    if version >= 3:
        raw = list(wdata.get("surfaces", []))
    elif version == 2:
        for s in wdata.get("surfaces", []):
            raw.append({
                "tag":    s.get("tag"),
                "p1":     s["p1"],
                "p2":     s["p2"],
                "z_range_mm": [0.0, float(s.get("height_mm", wh_default))],
                "kind":   s.get("kind", "wall"),
                "parent": s.get("parent"),
            })
    else:
        for w in wdata.get("walls", []):
            raw.append({
                "tag":    w.get("tag"),
                "p1":     w["p1"],
                "p2":     w["p2"],
                "z_range_mm": [0.0, wh_default],
                "kind":   "wall",
                "parent": None,
            })
        for sf in wdata.get("stepFaces", []):
            raw.append({
                "tag":    sf.get("tag"),
                "p1":     sf["p1"],
                "p2":     sf["p2"],
                "z_range_mm": [0.0, float(sf.get("top_mm", wh_default))],
                "kind":   "step",
                "parent": None,
            })

    def _key(p1, p2):
        a = (round(p1[0], 3), round(p1[1], 3))
        b = (round(p2[0], 3), round(p2[1], 3))
        if a > b:
            a, b = b, a
        return (a, b)

    footprints_geom = {}
    original_order = []
    for s in raw:
        p1 = s["p1"]; p2 = s["p2"]
        k = _key(p1, p2)
        if k in footprints_geom:
            continue
        dx = p2[0] - p1[0]; dy = p2[1] - p1[1]
        L = (dx * dx + dy * dy) ** 0.5
        if L < 1e-6:
            continue
        footprints_geom[k] = {
            "a":   [float(p1[0]), float(p1[1])],
            "b":   [float(p2[0]), float(p2[1])],
            "len": float(L),
        }
        original_order.append(k)

    ordered_keys = _order_footprints_spatially(footprints_geom, original_order)

    footprints = {}
    u = 0.0
    for k in ordered_keys:
        g = footprints_geom[k]
        footprints[k] = {
            "u0":  u,
            "u1":  u + g["len"],
            "a":   g["a"],
            "b":   g["b"],
            "len": g["len"],
        }
        u += g["len"]

    segments = []
    for s in raw:
        p1 = s["p1"]; p2 = s["p2"]
        k = _key(p1, p2)
        if k not in footprints:
            continue
        fp = footprints[k]
        zr = s.get("z_range_mm") or [0.0, float(s.get("height_mm", wh_default))]
        segments.append({
            "u0":     fp["u0"],
            "u1":     fp["u1"],
            "a":      fp["a"],
            "b":      fp["b"],
            "len":    fp["len"],
            "tag":    s.get("tag"),
            "z_lo":   float(zr[0]),
            "z_hi":   float(zr[1]),
            "kind":   s.get("kind", "wall"),
            "parent": s.get("parent"),
        })

    footprints_list = []
    for k in ordered_keys:
        fp = footprints[k]
        footprints_list.append({
            "u0":  fp["u0"], "u1":  fp["u1"],
            "a":   fp["a"],  "b":   fp["b"],
            "len": fp["len"],
        })

    return {
        "segments":   segments,
        "footprints": footprints_list,
        "totalU":     u,
        "height":     wh_default,
        "ccw":        True,
    }


# ---------------------------------------------------------------------------
# Fallback geometric extraction (used only when room_walls.json is missing).
# ---------------------------------------------------------------------------

def _ring_via_closing(wall_sketch):
    try:
        dilated = offset(wall_sketch, UNFOLD_CLOSE_R, kind=Kind.INTERSECTION)
        closed  = offset(dilated, -UNFOLD_CLOSE_R, kind=Kind.INTERSECTION)
    except Exception as e:
        print(f"  (morphological close failed: {e})")
        return None

    rings = []
    try:
        for f in closed.faces():
            try:
                for w in f.inner_wires():
                    r = wire_to_polygon(w)
                    if r and len(r) >= 3:
                        rings.append(r)
            except Exception:
                continue
    except Exception:
        try:
            for w in closed.inner_wires():
                r = wire_to_polygon(w)
                if r and len(r) >= 3:
                    rings.append(r)
        except Exception:
            pass

    if not rings:
        return None
    return max(rings, key=polygon_area)


def compute_unfold(wall_sketch, geom):
    ring = None
    method = "none"

    best_area = -1.0
    for face in geom["faces"]:
        for hole in face["holes"]:
            A = polygon_area(hole)
            if A > best_area:
                best_area = A
                ring = hole
                method = "largest hole"

    if ring is None:
        ring = _ring_via_closing(wall_sketch)
        if ring is not None:
            method = f"morphological close (r={UNFOLD_CLOSE_R:.0f} mm)"

    if ring is None:
        best_farea = -1.0
        best_face = None
        for face in geom["faces"]:
            A = polygon_area(face["outer"])
            if A > best_farea:
                best_farea = A
                best_face = face
        if best_face is not None:
            ring = best_face["outer"]
            method = "fallback (largest face outer ring)"

    if ring is None:
        return ({"segments": [], "footprints": [], "totalU": 0.0,
                 "height": WALL_HEIGHT, "ccw": True}, method)

    ccw = signed_area(ring) > 0

    segments = []
    footprints = []
    u = 0.0
    n = len(ring)
    for i in range(n):
        a = ring[i]
        b = ring[(i + 1) % n]
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        L = (dx * dx + dy * dy) ** 0.5
        if L < 1e-6:
            continue
        segments.append({
            "u0": u, "u1": u + L,
            "a": [float(a[0]), float(a[1])],
            "b": [float(b[0]), float(b[1])],
            "len": float(L),
            "tag": None,
            "z_lo": 0.0, "z_hi": WALL_HEIGHT,
            "kind": "wall", "parent": None,
        })
        footprints.append({
            "u0": u, "u1": u + L,
            "a": [float(a[0]), float(a[1])],
            "b": [float(b[0]), float(b[1])],
            "len": float(L),
        })
        u += L

    return ({"segments": segments, "footprints": footprints,
             "totalU": u, "height": WALL_HEIGHT, "ccw": ccw}, method)


# ---------------------------------------------------------------------------
# 2. HTML / JS playground
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Cable / pipe layout planner</title>
<style>
  html, body { margin:0; padding:0; height:100%; overflow:hidden;
               font-family: -apple-system, system-ui, Segoe UI, sans-serif; }
  #ui {
    position:absolute; top:12px; left:12px; z-index:10;
    background:rgba(255,255,255,0.96); padding:12px 14px;
    border-radius:8px; box-shadow:0 2px 10px rgba(0,0,0,0.18);
    font-size:13px; user-select:none; width:460px;
  }
  #ui.collapsed { display: none; }
  #ui h3 { margin:0 0 8px; font-size:14px; font-weight:600;
           display:flex; align-items:center; gap:6px; }
  #collapseBtn {
    margin-left:auto;
    padding:1px 8px; font-size:13px; font-weight:700; line-height:1.4;
    cursor:pointer; border:1px solid #bbb; background:#f7f7f7;
    border-radius:4px; color:#374151;
  }
  #collapseBtn:hover { background:#e5e7eb; }
  #restoreBtn {
    position:absolute; top:12px; left:12px; z-index:10;
    display:none;
    padding:6px 12px; cursor:pointer;
    border:1px solid #bbb; background:rgba(255,255,255,0.96);
    border-radius:8px; box-shadow:0 2px 10px rgba(0,0,0,0.18);
    font-size:12px; font-weight:600; color:#374151;
  }
  #restoreBtn:hover { background:#f3f4f6; }
  #restoreBtn.visible { display:block; }
  #ui .row { margin:6px 0; display:flex; gap:6px; flex-wrap:wrap; align-items:center; }
  #ui button { padding:5px 9px; cursor:pointer; border:1px solid #bbb;
               background:#f7f7f7; border-radius:4px; font-size:12px; }
  #ui button:hover { background:#eee; }
  #ui button.primary { background:#1d4ed8; color:#fff; border-color:#1d4ed8;
                       font-weight:600; }
  #ui button.danger { background:#fee2e2; border-color:#fca5a5; color:#991b1b; }
  #ui button.active { background:#0ea5e9; border-color:#0284c7; color:#fff;
                      font-weight:600; }
  #ui hr { border:0; border-top:1px solid #eee; margin:9px 0; }
  #status { margin-top:8px; font-size:12px; min-height:16px; font-weight:600;
            font-family: ui-monospace, Menlo, Consolas, monospace; }
  #status.ok  { color:#15803d; }
  #status.bad { color:#dc2626; }
  #status.warn{ color:#ea580c; }
  #hint { margin-top:8px; color:#555; font-size:11px; line-height:1.55;
          max-width:460px; }
  #snapPill {
    display:inline-block; padding:2px 8px; border-radius:10px;
    font-size:11px; font-weight:700; margin-left:6px;
    background:#e5e7eb; color:#6b7280; vertical-align:middle;
  }
  #snapPill.on { background:#bbf7d0; color:#166534; }
  canvas { display:block; touch-action:none; }
</style>
</head>
<body>
<div id="ui">
  <h3>Cable / pipe layout planner <span id="snapPill">snap: off</span>
      <button id="collapseBtn" title="Collapse panel">−</button></h3>
  <div class="row">
    <button id="addNsGrid">+ N–S grid</button>
    <button id="addEwGrid">+ E–W grid</button>
    <button id="addWallV">+ Wall |</button>
    <button id="addWallH">+ Wall ─</button>
  </div>
  <div class="row">
    <button id="drawBtn">✏️ Draw cable</button>
  </div>
  <div class="row">
    <button id="saveBtn" class="primary">💾 Save</button>
    <button id="delVertexBtn" class="danger">✂️ Delete vertex</button>
    <button id="deleteBtn" class="danger">🗑️ Delete cable</button>
    <button id="clearBtn">Clear all</button>
  </div>
  <hr>
  <div id="status"></div>
  <div id="hint">
    <b>Floor plan</b> (top) — <b>Unfolded wall</b> (bottom).
    Hold <b>Shift</b> while clicking to enable snapping.<br>
    <b>Draw cable</b>: click once, click again. If the two clicks are on the
    same wall, a straight segment is drawn. If they are on two different
    walls, the route planner builds a virtual straight line through every
    physically-connected wall between them, splits it at each corner, and
    files the pieces as one cable. Right-click undoes the last leg.
    Enter / Esc finishes.<br>
    <b>Alt+click</b> on an existing endpoint to adopt (continue) that cable
    or merge the current drawing into it.  A plain click near an endpoint
    just shares the anchor and starts a fresh cable — the two are still one
    physical cable through true-cable grouping.<br>
    <b>Focus mode</b>: the strip narrows to the wall you clicked and its
    physical neighbours. Visual aid — the route planner works regardless.<br>
    <b>Hops</b> (green arcs): a cable that turns a corner between two walls
    far apart in the strip draws a green hop arc instead of a diagonal.<br>
    <b>Drag</b> a corner anchor: only its height moves; every partner at
    that physical corner tracks it.<br>
    Select a vertex, then <b>Delete</b>; with no vertex selected, <b>Delete</b>
    removes the whole cable.
  </div>
</div>
<button id="restoreBtn" title="Show panel">☰ Show panel</button>
<canvas id="c"></canvas>
<script>
"use strict";
const GEOMETRY   = __GEOMETRY_JSON__;
const STATE_FILE = "room_layout.json";
const WALL       = GEOMETRY.unfoldedWall;
const WALL_HEIGHT = WALL.height;

/* ==========================================================================
   BASIC GEOMETRY
   ========================================================================== */

function pointInPolygon(p, poly) {
  const x = p[0], y = p[1];
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i][0], yi = poly[i][1];
    const xj = poly[j][0], yj = poly[j][1];
    if (((yi > y) !== (yj > y)) &&
        (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) inside = !inside;
  }
  return inside;
}

function pointToSegmentDist(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const len2 = dx*dx + dy*dy;
  if (len2 < 1e-9) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax)*dx + (py - ay)*dy) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t*dx), py - (ay + t*dy));
}

function polylineHit(wx, wy, pts, tol) {
  for (let i = 0; i < pts.length - 1; i++) {
    if (pointToSegmentDist(wx, wy,
        pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1]) < tol) return true;
  }
  return false;
}

function fmtCm(v_mm) {
  const cm = v_mm / 10;
  const r = Math.round(cm * 10) / 10;
  return Number.isInteger(r) ? String(r) : r.toFixed(1);
}

function segTop(s) { return (s && typeof s.z_hi === "number") ? s.z_hi : WALL_HEIGHT; }
function segBottom(s) { return (s && typeof s.z_lo === "number") ? s.z_lo : 0; }
function isStepFace(s) { return !!(s && s.kind === "step"); }

function pointOnStep(x, y) {
  const p = [x, y];
  for (const face of GEOMETRY.stepFaces) {
    if (!pointInPolygon(p, face.outer)) continue;
    let inHole = false;
    for (const h of face.holes) {
      if (pointInPolygon(p, h)) { inHole = true; break; }
    }
    if (!inHole) return true;
  }
  return false;
}

/* ==========================================================================
   JUNCTION GRAPH (computed once at load)
   ==========================================================================
   Nodes are physical room corners.  Terminals are (segIdx, side) pairs,
   side ∈ {0, 1}.  Two terminals are co-located when their plan XY is within
   TOL mm — meaning the two walls physically meet at that corner in the room.
   Every "do these two anchors share a physical point" question routes
   through this graph. */

let JUNCTIONS = [];             // [{ id, x, y, terminals: [{segIdx, side}] }]
let JUNCTION_OF_TERMINAL = null; // Map<"segIdx:side", junctionId>
let SEGMENT_ADJ = [];           // Array<Array<segIdx>>
let ADJ_DETAIL = null;          // Map<"a:b", {junctionId, side, other}>

function wallEdgeKey(segIdx, side) { return segIdx + ":" + side; }

function buildJunctionGraph() {
  const TOL = 3.0;
  const N = WALL.segments.length;

  const parent = new Map();
  const ensure = (k) => { if (!parent.has(k)) parent.set(k, k); };
  const find = (k) => {
    while (parent.get(k) !== k) {
      parent.set(k, parent.get(parent.get(k)));
      k = parent.get(k);
    }
    return k;
  };
  const union = (a, b) => {
    const ra = find(a), rb = find(b);
    if (ra !== rb) parent.set(ra, rb);
  };

  for (let i = 0; i < N; i++) {
    ensure(wallEdgeKey(i, 0));
    ensure(wallEdgeKey(i, 1));
  }

  const endpointOf = (segIdx, side) => {
    const s = WALL.segments[segIdx];
    return side === 0 ? s.a : s.b;
  };

  for (let i = 0; i < N; i++) {
    for (let j = i + 1; j < N; j++) {
      for (const si of [0, 1]) {
        const pi = endpointOf(i, si);
        for (const sj of [0, 1]) {
          const pj = endpointOf(j, sj);
          if (Math.hypot(pi[0]-pj[0], pi[1]-pj[1]) < TOL) {
            union(wallEdgeKey(i, si), wallEdgeKey(j, sj));
          }
        }
      }
    }
  }

  const groups = new Map();
  for (const k of parent.keys()) {
    const r = find(k);
    if (!groups.has(r)) groups.set(r, []);
    groups.get(r).push(k);
  }

  JUNCTIONS = [];
  JUNCTION_OF_TERMINAL = new Map();
  for (const [, terminals] of groups) {
    const parts0 = terminals[0].split(":");
    const p = endpointOf(Number(parts0[0]), Number(parts0[1]));
    const id = JUNCTIONS.length;
    JUNCTIONS.push({
      id,
      x: p[0], y: p[1],
      terminals: terminals.map(k => {
        const [si, ss] = k.split(":").map(Number);
        return { segIdx: si, side: ss };
      }),
    });
    for (const k of terminals) JUNCTION_OF_TERMINAL.set(k, id);
  }

  /* Segment adjacency. */
  SEGMENT_ADJ = Array.from({ length: N }, () => new Set());
  for (const j of JUNCTIONS) {
    for (const t1 of j.terminals) {
      for (const t2 of j.terminals) {
        if (t1.segIdx === t2.segIdx) continue;
        SEGMENT_ADJ[t1.segIdx].add(t2.segIdx);
      }
    }
  }
  SEGMENT_ADJ = SEGMENT_ADJ.map(s => Array.from(s));

  /* Adjacency detail: "a:b" → { junctionId, side, other } where `side` is
     a's side at the shared junction and `other` is b's side. */
  ADJ_DETAIL = new Map();
  for (const j of JUNCTIONS) {
    for (const t1 of j.terminals) {
      for (const t2 of j.terminals) {
        if (t1.segIdx === t2.segIdx) continue;
        const key = t1.segIdx + ":" + t2.segIdx;
        if (!ADJ_DETAIL.has(key)) {
          ADJ_DETAIL.set(key, {
            junctionId: j.id,
            side: t1.side,
            other: t2.side,
          });
        }
      }
    }
  }
}

function junctionOfTerminal(segIdx, side) {
  return JUNCTION_OF_TERMINAL.get(wallEdgeKey(segIdx, side));
}

/* Which junction is anchor `a` at?  -1 if it isn't at an endpoint. */
function junctionOfAnchor(a) {
  if (!a || a.space !== "wall-edge") return -1;
  const T_END = 0.01;
  let side = -1;
  if (Math.abs(a.t) < T_END) side = 0;
  else if (Math.abs(a.t - 1) < T_END) side = 1;
  else return -1;
  const jid = junctionOfTerminal(a.segIdx, side);
  return (typeof jid === "number") ? jid : -1;
}

/* ==========================================================================
   ANCHOR REGISTRY
   ========================================================================== */

let idCounter = 1;
const anchors = new Map();

function makeAnchor(space, fields) {
  const a = Object.assign({ id: idCounter++, space }, fields);
  anchors.set(a.id, a);
  return a;
}

function anchorPlan(a) {
  if (!a) return null;
  if (a.space === "floor")     return [a.x, a.y];
  if (a.space === "wall-edge") return wallAttachToPlan(a.segIdx, a.t);
  return null;
}

function anchorWall(a) {
  if (!a) return null;
  if (a.space === "wall-edge") return [wallAttachToU(a.segIdx, a.t), a.v];
  return null;
}

function anchorIsStepFace(a) {
  if (!a || a.space !== "wall-edge") return false;
  const s = WALL.segments[a.segIdx];
  return !!(s && isStepFace(s));
}

function anchorSide(a) {
  if (!anchorIsStepFace(a)) return null;
  const s = WALL.segments[a.segIdx];
  const top = segTop(s);
  if (Math.abs(a.v - top) < 0.5) return "top";
  if (Math.abs(a.v)      < 0.5) return "bottom";
  return null;
}

function clampAnchorToSegment(a) {
  if (a.space !== "wall-edge") return;
  const s = WALL.segments[a.segIdx];
  if (!s) return;
  if (isStepFace(s)) {
    const top = segTop(s);
    a.v = (a.v > top * 0.5) ? top : 0;
  } else {
    a.v = Math.max(segBottom(s), Math.min(segTop(s), a.v));
  }
}

/* Resolve a spec to an anchor id, preferring an existing match.  This is the
   single funnel through which every anchor is created or reused. */
function resolveSpecToAnchorId(spec, tolMm) {
  if (!spec) return null;

  if (spec.space === "floor") {
    for (const a of anchors.values()) {
      if (a.space !== "floor") continue;
      if (Math.hypot(a.x - spec.x, a.y - spec.y) < tolMm) return a.id;
    }
    return makeAnchor("floor", {
      x: spec.x, y: spec.y,
      gridId: spec.gridId, gridAxis: spec.gridAxis,
    }).id;
  }

  if (spec.space === "wall-edge") {
    const s = WALL.segments[spec.segIdx];
    if (!s) return null;
    let best = null, bestD = Infinity;
    for (const a of anchors.values()) {
      if (a.space !== "wall-edge") continue;
      if (a.segIdx !== spec.segIdx) continue;
      if (Math.abs(a.v - spec.v) > tolMm) continue;
      const dt = Math.abs(a.t - spec.t) * s.len;
      if (dt < tolMm && dt < bestD) { bestD = dt; best = a; }
    }
    if (best) return best.id;
    const a = makeAnchor("wall-edge", {
      segIdx: spec.segIdx, t: spec.t, v: spec.v,
      gridId: spec.gridId, gridAxis: spec.gridAxis,
    });
    clampAnchorToSegment(a);
    return a.id;
  }

  return null;
}

/* ==========================================================================
   WALL-EDGE GEOMETRY
   ========================================================================== */

function wallAttachToPlan(segIdx, t) {
  const s = WALL.segments[segIdx];
  if (!s) return null;
  return [s.a[0] + t * (s.b[0] - s.a[0]),
          s.a[1] + t * (s.b[1] - s.a[1])];
}
function wallAttachToU(segIdx, t) {
  const s = WALL.segments[segIdx];
  return s.u0 + t * s.len;
}
function uToWallAttach(u, v) {
  const cands = [];
  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    if (u >= s.u0 - 0.01 && u <= s.u1 + 0.01) {
      const t = (u - s.u0) / s.len;
      cands.push({ segIdx: i, t: Math.max(0, Math.min(1, t)) });
    }
  }
  if (cands.length === 0) return null;
  if (cands.length === 1 || typeof v !== "number") return cands[0];
  let best = cands[0], bestD = Infinity;
  for (const c of cands) {
    const s = WALL.segments[c.segIdx];
    if (v >= s.z_lo - 0.5 && v <= s.z_hi + 0.5) return c;
    const d = Math.min(Math.abs(v - s.z_lo), Math.abs(v - s.z_hi));
    if (d < bestD) { bestD = d; best = c; }
  }
  return best;
}
function nearestSegmentToUV(u, v) {
  let best = -1, bestD = Infinity;
  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    const du = Math.max(s.u0 - u, u - s.u1, 0);
    const dv = Math.max(segBottom(s) - v, v - segTop(s), 0);
    const d = Math.hypot(du, dv);
    if (d < bestD) { bestD = d; best = i; }
  }
  return best;
}
function findSegmentAtU(u) {
  let wallHit = null;
  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    if (u >= s.u0 - 0.01 && u <= s.u1 + 0.01) {
      if (isStepFace(s)) return { segIdx: i, seg: s };
      if (!wallHit) wallHit = { segIdx: i, seg: s };
    }
  }
  return wallHit;
}

/* ==========================================================================
   SEGMENT-REFERENCE HELPERS (save format v3)
   ========================================================================== */

function segRefOf(seg) {
  return { a: [seg.a[0], seg.a[1]], b: [seg.b[0], seg.b[1]] };
}

function findSegmentByRef(ref) {
  if (!ref || !ref.a || !ref.b) return -1;
  const TOL = 2.0;
  const same = (p, q) => Math.hypot(p[0]-q[0], p[1]-q[1]) < TOL;
  for (let i = 0; i < WALL.segments.length; i++) {
    const s = WALL.segments[i];
    if (same(s.a, ref.a) && same(s.b, ref.b)) return i;
    if (same(s.a, ref.b) && same(s.b, ref.a)) return i;
  }
  return -1;
}

function findSegmentByWorld(world) {
  if (!world) return -1;
  let best = -1, bestD = 50;
  for (let i = 0; i < WALL.segments.length; i++) {
    const s = WALL.segments[i];
    const dx = s.b[0] - s.a[0], dy = s.b[1] - s.a[1];
    const len2 = dx * dx + dy * dy;
    if (len2 < 1e-9) continue;
    let t = ((world[0] - s.a[0]) * dx + (world[1] - s.a[1]) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    const px = s.a[0] + t * dx, py = s.a[1] + t * dy;
    const d = Math.hypot(world[0] - px, world[1] - py);
    if (d < bestD) { bestD = d; best = i; }
  }
  return best;
}

/* Two consecutive wall-edge anchors on different segments that are far
   apart in u.  Purely a strip-level statement — whether they are
   physically coincident is irrelevant to the rendering decision. */
function isHopTransition(a, b) {
  if (!a || !b) return false;
  if (a.space !== "wall-edge" || b.space !== "wall-edge") return false;
  if (a.segIdx === b.segIdx) return false;
  const ua = anchorWall(a), ub = anchorWall(b);
  if (!ua || !ub) return false;
  return Math.abs(ua[0] - ub[0]) > 60.0;
}

function drawHopArc(fromX, fromY, toX, toY, emphasized) {
  ctx.save();
  ctx.setLineDash([5, 3]);
  ctx.strokeStyle = emphasized ? "rgba(16, 185, 129, 1)"
                               : "rgba(16, 185, 129, 0.85)";
  ctx.lineWidth  = emphasized ? 2.4 : 1.8;
  ctx.lineJoin = "round"; ctx.lineCap = "round";
  const midX = (fromX + toX) / 2;
  const dipY = Math.min(fromY, toY) - 24;
  ctx.beginPath();
  ctx.moveTo(fromX, fromY);
  ctx.quadraticCurveTo(midX, dipY, toX, toY);
  ctx.stroke();
  ctx.setLineDash([]);
  const chev = (x, y, dir) => {
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x + 5 * dir, y - 4);
    ctx.moveTo(x, y);
    ctx.lineTo(x + 5 * dir, y + 4);
    ctx.stroke();
  };
  chev(fromX, fromY, 1);
  chev(toX, toY, -1);
  ctx.restore();
}

/* ==========================================================================
   WORMHOLE ROUTE PLANNER
   ========================================================================== */

function findSegmentChain(startSeg, endSeg) {
  if (startSeg === endSeg) return null;
  const N = WALL.segments.length;
  const prev = new Array(N).fill(-1);
  const visited = new Set([startSeg]);
  const queue = [startSeg];
  while (queue.length) {
    const cur = queue.shift();
    if (cur === endSeg) break;
    for (const j of SEGMENT_ADJ[cur]) {
      if (visited.has(j)) continue;
      visited.add(j);
      prev[j] = cur;
      queue.push(j);
    }
  }
  if (!visited.has(endSeg)) return null;

  const path = [];
  let node = endSeg;
  while (node !== startSeg) {
    path.unshift(node);
    node = prev[node];
  }
  path.unshift(startSeg);

  const chain = [];
  for (let k = 0; k < path.length; k++) {
    let entrySide = -1, exitSide = -1;
    if (k > 0) {
      const d = ADJ_DETAIL.get(path[k] + ":" + path[k-1]);
      entrySide = d ? d.side : -1;
    }
    if (k < path.length - 1) {
      const d = ADJ_DETAIL.get(path[k] + ":" + path[k+1]);
      exitSide = d ? d.side : -1;
    }
    chain.push({ segIdx: path[k], entrySide, exitSide });
  }
  return chain;
}

function planWormholeRoute(click1, click2) {
  if (click1.segIdx === click2.segIdx) return null;
  const chain = findSegmentChain(click1.segIdx, click2.segIdx);
  if (!chain || chain.length < 2) return null;

  const n = chain.length;
  const L = chain.map(e => WALL.segments[e.segIdx].len);

  const exit0 = chain[0].exitSide;
  const d0 = (exit0 === 1) ? (1 - click1.t) * L[0]
                           : click1.t * L[0];
  const entryN = chain[n - 1].entrySide;
  const dN = (entryN === 1) ? (1 - click2.t) * L[n - 1]
                            : click2.t * L[n - 1];

  let D = d0 + dN;
  for (let i = 1; i < n - 1; i++) D += L[i];
  if (D < 1e-6) return null;

  const v0 = click1.v, vN = click2.v;
  const heightAt = (u) => v0 + (vN - v0) * (u / D);

  const out = [];
  out.push({ space: "wall-edge", segIdx: click1.segIdx,
             t: click1.t, v: click1.v });

  let u = d0;
  for (let i = 0; i < n - 1; i++) {
    const h = heightAt(u);
    out.push({ space: "wall-edge", segIdx: chain[i].segIdx,
               t: chain[i].exitSide, v: h });
    out.push({ space: "wall-edge", segIdx: chain[i + 1].segIdx,
               t: chain[i + 1].entrySide, v: h });
    u += L[i + 1];
  }

  out.push({ space: "wall-edge", segIdx: click2.segIdx,
             t: click2.t, v: click2.v });
  return out;
}

/* ==========================================================================
   SNAP SPEC PRODUCERS
   ========================================================================== */

function findWallAttachSpec(x, y) {
  const SNAP = 22 / viewFloor.scale;
  const onStep = pointOnStep(x, y);
  let best = null, bestDist = SNAP, bestPri = -1;
  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    const ax = s.a[0], ay = s.a[1], bx = s.b[0], by = s.b[1];
    const dx = bx - ax, dy = by - ay;
    const len2 = dx*dx + dy*dy;
    if (len2 < 1e-9) continue;
    let t = ((x - ax)*dx + (y - ay)*dy) / len2;
    t = Math.max(0, Math.min(1, t));
    const sx = ax + t*dx, sy = ay + t*dy;
    const d = Math.hypot(x - sx, y - sy);
    if (d > SNAP) continue;
    let pri, v;
    if (isStepFace(s)) {
      if (onStep) { pri = 1; v = segTop(s); }
      else        { pri = 2; v = 0; }
    } else if (s.kind === "wall" && segBottom(s) > 100 && onStep) {
      pri = 2; v = segBottom(s);
    } else {
      pri = 0; v = segBottom(s);
    }
    const better = (d < bestDist - 1.0) ||
                   (Math.abs(d - bestDist) <= 1.0 && pri > bestPri);
    if (better) {
      bestDist = d; bestPri = pri;
      best = { segIdx: i, t, v };
    }
  }
  if (!best) return null;
  return { space: "wall-edge", segIdx: best.segIdx, t: best.t, v: best.v };
}

function findWallEdgeSnapSpec(u, v) {
  const SNAP = 22 / Math.min(viewWall.scaleX, viewWall.scaleY);
  const sAt = findSegmentAtU(u);
  if (sAt && isStepFace(sAt.seg)) {
    const top = segTop(sAt.seg);
    const t = (u - sAt.seg.u0) / sAt.seg.len;
    if (Math.abs(v - top) < SNAP)
      return { space: "wall-edge", segIdx: sAt.segIdx, t, v: top };
    if (Math.abs(v)      < SNAP)
      return { space: "wall-edge", segIdx: sAt.segIdx, t, v: 0 };
  }
  if (Math.abs(v) < SNAP) {
    const attach = uToWallAttach(u, v);
    if (attach)
      return { space: "wall-edge", segIdx: attach.segIdx, t: attach.t, v: 0 };
  }
  return null;
}

function snapFloorSpec(wx, wy, snap) {
  if (snap) {
    const SNAP = 14 / viewFloor.scale;
    for (const g of state.floorGrids) {
      if (g.type === "ns" && Math.abs(wx - g.pos) < SNAP)
        return { space: "floor", x: g.pos, y: wy, gridId: g.id, gridAxis: "ns" };
      if (g.type === "ew" && Math.abs(wy - g.pos) < SNAP)
        return { space: "floor", x: wx, y: g.pos, gridId: g.id, gridAxis: "ew" };
    }
    const wall = findWallAttachSpec(wx, wy);
    if (wall) return wall;
  }
  return { space: "floor", x: wx, y: wy };
}

function snapWallSpec(u, v, snap) {
  if (snap && !focusSavedU) {
    const SNAP = 14 / Math.min(viewWall.scaleX, viewWall.scaleY);
    for (const g of state.wallGrids) {
      if (g.type === "v" && Math.abs(u - g.pos) < SNAP) u = g.pos;
      if (g.type === "h" && Math.abs(v - g.pos) < SNAP) v = g.pos;
    }
  }
  if (snap) {
    const edge = findWallEdgeSnapSpec(u, v);
    if (edge) return edge;
  }
  const attach = uToWallAttach(u, v);
  if (attach) {
    const s = WALL.segments[attach.segIdx];
    const vv = Math.max(segBottom(s), Math.min(segTop(s), v));
    return { space: "wall-edge", segIdx: attach.segIdx, t: attach.t, v: vv };
  }
  const i = nearestSegmentToUV(u, v);
  if (i < 0) return null;
  const s = WALL.segments[i];
  const t = Math.max(0, Math.min(1, (u - s.u0) / s.len));
  const vv = Math.max(segBottom(s), Math.min(segTop(s), v));
  return { space: "wall-edge", segIdx: i, t, v: vv };
}

/* ==========================================================================
   VIEW STATE / TRANSFORMS
   ========================================================================== */

const canvas = document.getElementById("c");
const ctx = canvas.getContext("2d");
let dpr = window.devicePixelRatio || 1;

const layout = { floorH: 0, dividerY: 0, wallY: 0, wallH: 0 };
const viewFloor = { scale: 1, tx: 0, ty: 0 };
const viewWall  = { scaleX: 1, scaleY: 1, tx: 0, ty: 0, stripTopY: 0 };
const router = { ARROW_GAP: 14, ESCAPE_DROP: 30, leftBaseX: 0, rightBaseX: 0 };
const ANCHOR_NEARBY_PX = 14;

function w2sFloor(x, y) { return [x*viewFloor.scale + viewFloor.tx, -y*viewFloor.scale + viewFloor.ty]; }
function s2wFloor(sx, sy) { return [(sx - viewFloor.tx)/viewFloor.scale, -(sy - viewFloor.ty)/viewFloor.scale]; }
function w2sWall(u, v) { return [u*viewWall.scaleX + viewWall.tx, -v*viewWall.scaleY + viewWall.ty]; }
function s2wWall(sx, sy) { return [(sx - viewWall.tx)/viewWall.scaleX, -(sy - viewWall.ty)/viewWall.scaleY]; }
function whichView(sy) {
  if (sy < layout.dividerY) return "floor";
  if (sy >= layout.wallY)   return "wall";
  return null;
}

/* ==========================================================================
   STATE
   ========================================================================== */

let snapEnabled = false;

const state = {
  floorGrids: [], wallGrids: [],
  floorCables: [], wallCables: [],
  trueCables: [],
  selectedCable: null, selectedVertex: null,
  dragGrid: null, dragVertex: null, hoveredRoute: null,
};

let drawing = null;
let drawingPreview = null;
let routeCache = [];
const mouse = { sx: 0, sy: 0, view: null, inside: false };

function updateSnapPill() {
  const el = document.getElementById("snapPill");
  if (!el) return;
  if (snapEnabled) { el.textContent = "snap: ON (Shift)"; el.classList.add("on"); }
  else             { el.textContent = "snap: off";        el.classList.remove("on"); }
}

/* ==========================================================================
   FOCUS MODE
   ========================================================================== */

let focusSeg    = -1;
let focusSavedU = null;

function isSegHidden(i) {
  return WALL.segments[i] && WALL.segments[i]._hidden === true;
}

function enterFocus(segIdx) {
  exitFocus();
  if (segIdx < 0 || segIdx >= WALL.segments.length) return;

  const S = WALL.segments[segIdx];
  const leftNbrs = [], rightNbrs = [];

  /* For every junction of this segment, its other terminals' segments
     become neighbours.  Their side (left / right of the focused wall) is
     determined by which endpoint of the focused segment the junction is. */
  for (const j of JUNCTIONS) {
    const t_this = j.terminals.find(t => t.segIdx === segIdx);
    if (!t_this) continue;
    for (const o of j.terminals) {
      if (o.segIdx === segIdx) continue;
      if (t_this.side === 0) leftNbrs.push(o.segIdx);
      else                   rightNbrs.push(o.segIdx);
    }
  }

  focusSavedU = new Map();
  const save = (i) => focusSavedU.set(i, { u0: WALL.segments[i].u0,
                                            u1: WALL.segments[i].u1 });
  save(segIdx); leftNbrs.forEach(save); rightNbrs.forEach(save);

  let cursor = S.u0;
  for (const i of leftNbrs) {
    const T = WALL.segments[i];
    const L = T.len;
    T.u0 = cursor - L;
    T.u1 = cursor;
    cursor -= L;
  }
  cursor = S.u1;
  for (const i of rightNbrs) {
    const T = WALL.segments[i];
    const L = T.len;
    T.u0 = cursor;
    T.u1 = cursor + L;
    cursor += L;
  }

  const vis = new Set([segIdx, ...leftNbrs, ...rightNbrs]);
  for (let i = 0; i < WALL.segments.length; i++) {
    if (vis.has(i)) delete WALL.segments[i]._hidden;
    else            WALL.segments[i]._hidden = true;
  }

  focusSeg = segIdx;
  refitFocusView();
}

function exitFocus() {
  if (focusSavedU) {
    for (const [i, u] of focusSavedU) {
      WALL.segments[i].u0 = u.u0;
      WALL.segments[i].u1 = u.u1;
    }
  }
  for (const s of WALL.segments) delete s._hidden;
  focusSavedU = null;
  focusSeg = -1;
  fitViews();
}

function refitFocusView() {
  if (!focusSavedU) return;
  let uMin = Infinity, uMax = -Infinity;
  for (const [i] of focusSavedU) {
    const s = WALL.segments[i];
    uMin = Math.min(uMin, s.u0);
    uMax = Math.max(uMax, s.u1);
  }
  const W = Math.max(uMax - uMin, 1);
  const cw = window.innerWidth;
  const sideChan = 40, topPad = 20;
  const bottomPad = router.ESCAPE_DROP + 30;
  const gutterTop = 140;
  const availW = cw - 2 * topPad - 2 * sideChan;
  const availH = layout.wallH - topPad - bottomPad - gutterTop;
  const H = WALL_HEIGHT || 1;
  viewWall.scaleX = availW / W;
  viewWall.scaleY = availH / H;
  viewWall.tx     = topPad + sideChan - uMin * viewWall.scaleX;
  viewWall.ty     = layout.wallY + layout.wallH - bottomPad;
  viewWall.stripTopY = viewWall.ty - H * viewWall.scaleY;
  router.leftBaseX  = viewWall.tx - 12;
  router.rightBaseX = viewWall.tx + W * viewWall.scaleX + 12;
}

/* ==========================================================================
   TRUE-CABLE GROUPING
   ========================================================================== */

function allCables() {
  return state.floorCables.concat(state.wallCables);
}

function trueCableIdOf(cable) {
  return (cable && cable.trueCableId != null) ? cable.trueCableId : cable.id;
}

function trueCableSiblings(cable) {
  const tid = trueCableIdOf(cable);
  return allCables().filter(c => trueCableIdOf(c) === tid);
}

function recomputeTrueCables() {
  const all = allCables();
  if (all.length === 0) { state.trueCables = []; return; }

  const parent = new Map();
  for (const c of all) parent.set(c.id, c.id);
  const find = (x) => {
    while (parent.get(x) !== x) {
      parent.set(x, parent.get(parent.get(x)));
      x = parent.get(x);
    }
    return x;
  };
  const union = (a, b) => {
    const ra = find(a), rb = find(b);
    if (ra !== rb) parent.set(Math.max(ra, rb), Math.min(ra, rb));
  };

  /* Pass 1: shared anchor id. */
  const anchorOwner = new Map();
  for (const c of all) {
    for (const aid of c.anchorIds) {
      if (anchorOwner.has(aid)) union(c.id, anchorOwner.get(aid));
      else                      anchorOwner.set(aid, c.id);
    }
  }

  /* Pass 2: junction-based coincidence.  Every wall-edge anchor sitting at
     a given junction terminal is physically coincident (same XY) with every
     other anchor at a terminal of the same junction, provided their v
     values agree.  Union their owning cables. */
  for (const j of JUNCTIONS) {
    const here = [];
    for (const t of j.terminals) {
      for (const a of anchors.values()) {
        if (a.space !== "wall-edge") continue;
        if (a.segIdx !== t.segIdx) continue;
        const side = Math.abs(a.t) < 0.01 ? 0
                   : Math.abs(a.t - 1) < 0.01 ? 1 : -1;
        if (side !== t.side) continue;
        here.push(a);
      }
    }
    for (let i = 0; i < here.length; i++) {
      for (let k = i + 1; k < here.length; k++) {
        if (Math.abs((here[i].v || 0) - (here[k].v || 0)) > 5.0) continue;
        const ci = anchorOwner.get(here[i].id);
        const ck = anchorOwner.get(here[k].id);
        if (ci != null && ck != null) union(ci, ck);
      }
    }
  }

  for (const c of all) c.trueCableId = find(c.id);

  const byRoot = new Map();
  for (const c of all) {
    const r = c.trueCableId;
    if (!byRoot.has(r)) byRoot.set(r, []);
    byRoot.get(r).push(c.id);
  }
  state.trueCables = Array.from(byRoot, ([id, parts]) => ({ id, parts }));
}

/* ==========================================================================
   RESIZE / FIT
   ========================================================================== */

function resize() {
  dpr = window.devicePixelRatio || 1;
  const cw = window.innerWidth, ch = window.innerHeight;
  canvas.width  = Math.round(cw * dpr);
  canvas.height = Math.round(ch * dpr);
  canvas.style.width  = cw + "px";
  canvas.style.height = ch + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  layout.floorH   = Math.round(ch * 0.54);
  layout.dividerY = layout.floorH;
  layout.wallY    = layout.floorH + 4;
  layout.wallH    = ch - layout.wallY;
  fitViews();
  draw();
}
window.addEventListener("resize", resize);

function fitViews() {
  const cw = window.innerWidth;
  const b = GEOMETRY.bounds;
  const pad = 50;
  const bw = (b.maxX - b.minX) || 1;
  const bh = (b.maxY - b.minY) || 1;
  const sf = Math.min((cw - 2*pad) / bw, (layout.floorH - 2*pad) / bh);
  viewFloor.scale = sf;
  const mx = (b.minX + b.maxX) / 2, my = (b.minY + b.maxY) / 2;
  viewFloor.tx = cw / 2 - mx * sf;
  viewFloor.ty = layout.floorH / 2 + my * sf;

  const sideChan = 40, topPad = 20;
  const bottomPad = router.ESCAPE_DROP + 30;
  const gutterTop = 140;
  const W = WALL.totalU || 1, H = WALL_HEIGHT || 1;
  const availW = cw - 2 * topPad - 2 * sideChan;
  const availH = layout.wallH - topPad - bottomPad - gutterTop;
  viewWall.scaleX    = availW / W;
  viewWall.scaleY    = availH / H;
  viewWall.tx        = topPad + sideChan;
  viewWall.ty        = layout.wallY + layout.wallH - bottomPad;
  viewWall.stripTopY = viewWall.ty - H * viewWall.scaleY;
  router.leftBaseX  = viewWall.tx - 12;
  router.rightBaseX = viewWall.tx + WALL.totalU * viewWall.scaleX + 12;
}

/* ==========================================================================
   SHARED RENDER HELPERS
   ========================================================================== */

function pathFaceFloor(face) {
  ctx.beginPath();
  const o = face.outer;
  if (!o.length) return;
  let [x0, y0] = w2sFloor(o[0][0], o[0][1]);
  ctx.moveTo(x0, y0);
  for (let i = 1; i < o.length; i++) {
    const [x, y] = w2sFloor(o[i][0], o[i][1]);
    ctx.lineTo(x, y);
  }
  ctx.closePath();
  for (const h of face.holes) {
    if (!h.length) continue;
    [x0, y0] = w2sFloor(h[0][0], h[0][1]);
    ctx.moveTo(x0, y0);
    for (let i = 1; i < h.length; i++) {
      const [x, y] = w2sFloor(h[i][0], h[i][1]);
      ctx.lineTo(x, y);
    }
    ctx.closePath();
  }
}

function drawBoundaryMarker(sx, sy, selected, onTop) {
  ctx.beginPath();
  ctx.arc(sx, sy, selected ? 7 : 6, 0, Math.PI * 2);
  ctx.fillStyle = onTop ? "rgba(220, 38, 38, 0.95)" : "rgba(6, 182, 212, 0.95)";
  ctx.fill();
  ctx.strokeStyle = onTop ? "#991b1b" : "#0e7490";
  ctx.lineWidth = 1.6; ctx.stroke();
  ctx.beginPath();
  ctx.arc(sx, sy, 2.4, 0, Math.PI * 2);
  ctx.fillStyle = "#ffffff"; ctx.fill();
}

function drawColumnMarkers() {
  const cols = GEOMETRY.columns || [];
  if (!cols.length) return;
  for (const col of cols) {
    const [x0, y0, x1, y1] = col.box;
    const [sxa, sya] = w2sFloor(x0, y0);
    const [sxb, syb] = w2sFloor(x1, y1);
    const bx = Math.min(sxa, sxb), by = Math.min(sya, syb);
    const bw = Math.abs(sxb - sxa), bh = Math.abs(syb - sya);
    ctx.save();
    ctx.strokeStyle = "rgba(245, 158, 11, 0.9)";
    ctx.lineWidth = 1.6; ctx.setLineDash([3, 3]);
    ctx.strokeRect(bx, by, bw, bh);
    ctx.setLineDash([]);
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    const [sx, sy] = w2sFloor(cx, cy);
    ctx.beginPath();
    ctx.arc(sx, sy, 5, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(245, 158, 11, 0.95)"; ctx.fill();
    ctx.strokeStyle = "#b45309"; ctx.lineWidth = 1.6; ctx.stroke();
    ctx.font = "700 10px ui-monospace, monospace";
    ctx.fillStyle = "#7c2d12"; ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(col.tag, sx + 8, sy);
    ctx.restore();
  }
}

/* ==========================================================================
   CABLE RENDERING
   ========================================================================== */

function drawCable(c, view) {
  if (!c.anchorIds || c.anchorIds.length === 0) return;
  if (drawing && drawing.baseCableId === c.id) return;
  const project  = (view === "floor") ? anchorPlan  : anchorWall;
  const toScreen = (view === "floor") ? w2sFloor    : w2sWall;

  const selSelf  = (c === state.selectedCable);
  const selGroup = state.selectedCable &&
                   trueCableIdOf(c) === trueCableIdOf(state.selectedCable);
  const sel = selGroup;
  const strokeColor = selSelf ? "#0ea5e9"
                    : selGroup ? "rgba(14, 165, 233, 0.65)"
                    : "#3b82f6";

  const visible = (a) => {
    if (view !== "wall") return true;
    if (!a) return false;
    if (a.space !== "wall-edge") return true;
    return !isSegHidden(a.segIdx);
  };

  ctx.beginPath();
  let started = false;
  let lastAnchor = null;
  const hops = [];
  for (const id of c.anchorIds) {
    const a = anchors.get(id);
    if (!visible(a)) { started = false; lastAnchor = null; continue; }
    const p = a ? project(a) : null;
    if (!p) { started = false; lastAnchor = null; continue; }
    const [sx, sy] = toScreen(p[0], p[1]);
    if (started && lastAnchor && isHopTransition(lastAnchor, a)) {
      const lp = project(lastAnchor);
      const [lx, ly] = toScreen(lp[0], lp[1]);
      hops.push({ fromX: lx, fromY: ly, toX: sx, toY: sy });
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(sx, sy);
    } else if (!started) {
      ctx.moveTo(sx, sy);
    } else {
      ctx.lineTo(sx, sy);
    }
    started = true;
    lastAnchor = a;
  }
  ctx.strokeStyle = strokeColor;
  ctx.lineWidth = sel ? 3.2 : 2.2;
  ctx.lineJoin = "round"; ctx.lineCap = "round";
  ctx.stroke();

  for (const j of hops) {
    drawHopArc(j.fromX, j.fromY, j.toX, j.toY, sel);
  }

  for (let i = 0; i < c.anchorIds.length; i++) {
    const a = anchors.get(c.anchorIds[i]);
    if (!visible(a)) continue;
    const p = a ? project(a) : null;
    if (!p) continue;
    const [vx, vy] = toScreen(p[0], p[1]);
    const isStep = anchorIsStepFace(a);
    const side   = anchorSide(a);
    ctx.beginPath();
    ctx.arc(vx, vy, isStep ? 6.5 : 5, 0, Math.PI * 2);
    ctx.fillStyle = isStep ? "#f97316" : "#ffffff";
    ctx.fill();
    ctx.strokeStyle = isStep ? "#c2410c" : strokeColor;
    ctx.lineWidth = 2; ctx.stroke();
    if (isStep) {
      ctx.beginPath();
      ctx.arc(vx, vy, 8.5, 0, Math.PI * 2);
      ctx.strokeStyle = (side === "top") ? "rgba(220, 38, 38, 0.85)"
                                         : "rgba(6, 182, 212, 0.85)";
      ctx.lineWidth = 1.4; ctx.stroke();
    }
    if (state.selectedVertex && state.selectedVertex.cable === c &&
        state.selectedVertex.index === i) {
      ctx.beginPath();
      ctx.arc(vx, vy, 10, 0, Math.PI * 2);
      ctx.strokeStyle = "#f59e0b"; ctx.lineWidth = 2; ctx.stroke();
    }
  }
}

/* Cross-view markers: only wall-edge anchors at floor level (v = 0) or at
   the top of a step face project to the eagle view.  Any other height is
   wall-only and must not appear as a floor-plan dot. */
function drawCrossMarkersFromWallCables() {
  for (const c of state.wallCables) {
    if (drawing && drawing.baseCableId === c.id) continue;
    const sel = (c === state.selectedCable);
    for (const id of c.anchorIds) {
      const a = anchors.get(id);
      if (!a || a.space !== "wall-edge") continue;
      if (isSegHidden(a.segIdx)) continue;
      const s = WALL.segments[a.segIdx];
      if (!s) continue;
      const atFloorLevel = Math.abs(a.v) < 0.5 ||
                           (isStepFace(s) && Math.abs(a.v - segTop(s)) < 0.5);
      if (!atFloorLevel) continue;
      const p = anchorPlan(a);
      if (!p) continue;
      const [mx, my] = w2sFloor(p[0], p[1]);
      const side = anchorSide(a);
      drawBoundaryMarker(mx, my, sel, side === "top");
    }
  }
}
function drawCrossMarkersFromFloorCables() {
  for (const c of state.floorCables) {
    if (drawing && drawing.baseCableId === c.id) continue;
    const sel = (c === state.selectedCable);
    for (const id of c.anchorIds) {
      const a = anchors.get(id);
      if (!a || a.space !== "wall-edge") continue;
      if (isSegHidden(a.segIdx)) continue;
      const p = anchorWall(a);
      if (!p) continue;
      const [mx, my] = w2sWall(p[0], p[1]);
      const side = anchorSide(a);
      drawBoundaryMarker(mx, my, sel, side === "top");
    }
  }
}

function drawStepBridgeBars() {
  const floorUsed = new Set();
  const wallUsed  = new Set();
  const collect = (list, view, set) => {
    for (const c of list) {
      if (drawing && drawing.baseCableId === c.id) continue;
      for (const id of c.anchorIds) set.add(id);
    }
    if (drawing && drawing.view === view) {
      for (const id of drawing.anchorIds) set.add(id);
    }
  };
  collect(state.floorCables, "floor", floorUsed);
  collect(state.wallCables,  "wall",  wallUsed);

  for (const a of anchors.values()) {
    if (!anchorIsStepFace(a))  continue;
    if (isSegHidden(a.segIdx)) continue;
    if (!floorUsed.has(a.id))  continue;
    if ( wallUsed.has(a.id))   continue;

    const s   = WALL.segments[a.segIdx];
    const top = segTop(s);
    const u   = wallAttachToU(a.segIdx, a.t);
    const [x0, y0] = w2sWall(u, 0);
    const [x1, y1] = w2sWall(u, top);
    ctx.save();
    ctx.setLineDash([4, 3]);
    ctx.strokeStyle = "rgba(220, 38, 38, 0.55)";
    ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
    ctx.restore();
  }
}

/* ==========================================================================
   FLOOR VIEW
   ========================================================================== */

function drawFloorView() {
  const cw = window.innerWidth;
  for (const g of state.floorGrids) {
    let x0, y0, x1, y1;
    if (g.type === "ns") {
      [x0, y0] = w2sFloor(g.pos, 0); y0 = 0; y1 = layout.floorH; x1 = x0;
    } else {
      [x0, y0] = w2sFloor(0, g.pos); x0 = 0; x1 = cw; y1 = y0;
    }
    const active = (state.dragGrid && state.dragGrid.grid === g);
    ctx.strokeStyle = active ? "#dc2626" : "rgba(139, 92, 246, 0.85)";
    ctx.lineWidth = active ? 2.5 : 1.5;
    ctx.setLineDash([10, 6]);
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
    ctx.stroke(); ctx.setLineDash([]);
    const [tx, ty] = (g.type === "ns")
      ? w2sFloor(g.pos, GEOMETRY.bounds.maxY)
      : w2sFloor(GEOMETRY.bounds.minX, g.pos);
    const txt = (g.type === "ns" ? "x=" : "y=") + g.pos.toFixed(0);
    ctx.font = "600 10px ui-monospace, monospace";
    ctx.fillStyle = "rgba(109, 40, 217, 0.9)";
    ctx.textBaseline = "bottom";
    if (g.type === "ns") ctx.fillText(txt, tx + 4, Math.min(ty + 12, layout.floorH - 4));
    else                 ctx.fillText(txt, tx + 4, ty - 3);
  }

  for (const face of GEOMETRY.stepFaces) {
    pathFaceFloor(face);
    ctx.fillStyle = "rgba(186, 214, 244, 0.55)";
    ctx.fill("evenodd");
    ctx.strokeStyle = "rgba(80, 130, 200, 0.75)";
    ctx.lineWidth = 1; ctx.setLineDash([7, 5]);
    ctx.stroke(); ctx.setLineDash([]);
  }
  for (const face of GEOMETRY.faces) {
    pathFaceFloor(face);
    ctx.fillStyle = "#2b2b2b";
    ctx.fill("evenodd");
    ctx.strokeStyle = "#000"; ctx.lineWidth = 1; ctx.stroke();
  }
  drawColumnMarkers();

  for (const c of state.floorCables) drawCable(c, "floor");
  drawDrawingPreview("floor");
  drawCrossMarkersFromWallCables();

  if (mouse.inside && mouse.view === "floor") {
    const [wx, wy] = s2wFloor(mouse.sx, mouse.sy);
    const [sx, sy] = w2sFloor(wx, wy);
    ctx.beginPath();
    ctx.arc(sx, sy, 4, 0, Math.PI * 2);
    ctx.fillStyle = "#f59e0b"; ctx.fill();
    ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.4; ctx.stroke();
  }
}

/* ==========================================================================
   WALL VIEW
   ========================================================================== */

function drawWallView() {
  const cw = window.innerWidth;
  ctx.fillStyle = "#eceff3";
  ctx.fillRect(0, layout.wallY, cw, layout.wallH);

  const [x0, y0] = w2sWall(0, 0);
  const [x1, y1] = w2sWall(WALL.totalU, 0);
  const [x2, y2] = w2sWall(WALL.totalU, WALL_HEIGHT);
  const [x3, y3] = w2sWall(0, WALL_HEIGHT);
  ctx.beginPath();
  ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
  ctx.lineTo(x2, y2); ctx.lineTo(x3, y3); ctx.closePath();
  ctx.fillStyle = "#e2e8f0";
  ctx.fill();
  ctx.strokeStyle = "#334155"; ctx.lineWidth = 2; ctx.stroke();

  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    const [sx0, syHi] = w2sWall(s.u0, s.z_hi);
    const [sx1, syLo] = w2sWall(s.u1, s.z_lo);
    ctx.fillStyle = isStepFace(s) ? "rgba(100, 116, 139, 0.22)" : "#fbfbfd";
    ctx.fillRect(sx0, syHi, sx1 - sx0, syLo - syHi);
  }

  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    const [sx0, syTop] = w2sWall(s.u0, 0);
    const [sx1, syBot] = w2sWall(s.u0, 0);
    ctx.strokeStyle = "rgba(15, 23, 42, 0.35)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(sx0, syTop); ctx.lineTo(sx1, syBot);
    ctx.moveTo(sx1, syTop); ctx.lineTo(sx1, syBot);
    ctx.stroke();
  }
  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    const [sx0, sy0] = w2sWall(s.u1, 0);
    const [sx1, sy1] = w2sWall(s.u1, WALL_HEIGHT);
    ctx.beginPath(); ctx.moveTo(sx0, sy0); ctx.lineTo(sx1, sy1);
    ctx.strokeStyle = "rgba(15, 23, 42, 0.35)";
    ctx.lineWidth = 1; ctx.stroke();
  }

  ctx.save();
  ctx.setLineDash([5, 3]);
  ctx.strokeStyle = "rgba(220, 38, 38, 0.8)";
  ctx.lineWidth = 1.6;
  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    if (!isStepFace(s)) continue;
    const t = segTop(s);
    const [sx0, sy0] = w2sWall(s.u0, t);
    const [sx1, sy1] = w2sWall(s.u1, t);
    ctx.beginPath(); ctx.moveTo(sx0, sy0); ctx.lineTo(sx1, sy1);
    ctx.stroke();
  }
  ctx.setLineDash([]); ctx.restore();

  ctx.font = "700 10px ui-monospace, monospace";
  ctx.fillStyle = "rgba(185, 28, 28, 0.9)";
  ctx.textAlign = "center"; ctx.textBaseline = "bottom";
  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    if (!isStepFace(s)) continue;
    const t = segTop(s);
    const [sxMid, syMid] = w2sWall((s.u0 + s.u1) / 2, t);
    ctx.fillText("STEP " + fmtCm(t) + "cm", sxMid, syMid - 3);
  }

  if (!focusSavedU) drawWallRulers();

  for (const g of state.wallGrids) {
    if (focusSavedU) continue;
    let gx0, gy0, gx1, gy1;
    if (g.type === "v") {
      [gx0, gy0] = w2sWall(g.pos, 0);
      [gx1, gy1] = w2sWall(g.pos, WALL_HEIGHT);
    } else {
      [gx0, gy0] = w2sWall(0, g.pos);
      [gx1, gy1] = w2sWall(WALL.totalU, g.pos);
    }
    const active = (state.dragGrid && state.dragGrid.grid === g);
    ctx.strokeStyle = active ? "#dc2626" : "rgba(139, 92, 246, 0.85)";
    ctx.lineWidth = active ? 2.5 : 1.5;
    ctx.setLineDash([10, 6]);
    ctx.beginPath(); ctx.moveTo(gx0, gy0); ctx.lineTo(gx1, gy1);
    ctx.stroke(); ctx.setLineDash([]);
    const label = (g.type === "v" ? "u=" : "v=") + g.pos.toFixed(0);
    ctx.font = "600 10px ui-monospace, monospace";
    ctx.fillStyle = "rgba(109, 40, 217, 0.9)";
    if (g.type === "v") {
      ctx.textAlign = "left"; ctx.textBaseline = "top";
      ctx.fillText(label, gx0 + 4, gy0 - 12);
    } else {
      ctx.textAlign = "right"; ctx.textBaseline = "bottom";
      ctx.fillText(label, gx0 - 4, gy0 - 2);
    }
  }

  drawStepBridgeBars();

  for (const c of state.wallCables) drawCable(c, "wall");
  drawDrawingPreview("wall");
  drawCrossMarkersFromFloorCables();

  if (focusSavedU && focusSeg >= 0) {
    const seg = WALL.segments[focusSeg];
    const tag = (seg && seg.tag) ? seg.tag : ("segment " + (focusSeg + 1));
    ctx.font = "700 11px ui-monospace, monospace";
    ctx.fillStyle = "rgba(16, 185, 129, 0.95)";
    ctx.textAlign = "left"; ctx.textBaseline = "bottom";
    ctx.fillText("FOCUS  ·  " + tag + "  ·  neighbours shown adjacent",
                 12, layout.wallY + 30);
  }

  if (mouse.inside && mouse.view === "floor") {
    const [wx, wy] = s2wFloor(mouse.sx, mouse.sy);
    const proj = projectOntoWalls(wx, wy);
    for (const p of proj) {
      const [sx, sy] = w2sWall(p.u, 0);
      ctx.beginPath();
      ctx.arc(sx, sy, p.closest ? 6 : 3, 0, Math.PI * 2);
      ctx.fillStyle = p.closest ? "#f59e0b" : "rgba(245,158,11,0.55)";
      ctx.fill();
      ctx.strokeStyle = p.closest ? "#b45309" : "rgba(180,83,9,0.4)";
      ctx.lineWidth = p.closest ? 2 : 1; ctx.stroke();
    }
  }
  ctx.font = "600 11px -apple-system, system-ui, sans-serif";
  ctx.fillStyle = "rgba(15, 23, 42, 0.75)";
  ctx.textAlign = "left"; ctx.textBaseline = "bottom";
  ctx.fillText(
    `Unfolded wall — u: perimeter distance, v: height (ceiling ${WALL_HEIGHT.toFixed(0)} mm)`,
    12, layout.wallY + 14);
}

function drawWallRulers() {
  if (!WALL.footprints.length) return;
  const FS = 12, ROW_H = FS * 1.7, ROWS = 6, HPAD = 12;
  const stripTopY = viewWall.stripTopY;
  ctx.font = `600 ${FS}px ui-monospace, monospace`;
  const items = [];
  for (const fp of WALL.footprints) {
    const [ax] = w2sWall((fp.u0 + fp.u1) / 2, 0);
    const txt = fmtCm(fp.len);
    const w = ctx.measureText(txt).width;
    items.push({ anchorX: ax, txt, w, labelX: ax, labelY: 0 });
  }
  const sorted = [...items].sort((a, b) => a.anchorX - b.anchorX);
  const rowLastRight = new Array(ROWS).fill(-Infinity);
  for (const it of sorted) {
    let placed = false;
    for (let r = 0; r < ROWS; r++) {
      if (it.anchorX - it.w / 2 > rowLastRight[r] + HPAD) {
        it.labelX = it.anchorX;
        it.labelY = stripTopY - (r + 1) * ROW_H + ROW_H * 0.4;
        rowLastRight[r] = it.anchorX + it.w / 2; placed = true; break;
      }
    }
    if (!placed) {
      let minR = 0;
      for (let r = 1; r < ROWS; r++)
        if (rowLastRight[r] < rowLastRight[minR]) minR = r;
      const lx = Math.max(rowLastRight[minR] + HPAD + it.w / 2, it.anchorX);
      it.labelX = lx;
      it.labelY = stripTopY - (minR + 1) * ROW_H + ROW_H * 0.4;
      rowLastRight[minR] = lx + it.w / 2;
    }
  }
  for (const it of items)
    drawArrow(it.labelX, it.labelY + FS * 0.9, it.anchorX, stripTopY);
  for (const it of items) {
    ctx.fillStyle = "rgba(255, 255, 255, 0.96)";
    ctx.fillRect(it.labelX - it.w / 2 - 4, it.labelY - FS * 0.75,
                 it.w + 8, FS * 1.5);
  }
  ctx.fillStyle = "rgba(15, 23, 42, 0.92)";
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.font = `600 ${FS}px ui-monospace, monospace`;
  for (const it of items) ctx.fillText(it.txt, it.labelX, it.labelY);
}

function drawArrow(x0, y0, x1, y1) {
  ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
  ctx.strokeStyle = "rgba(15, 23, 42, 0.35)";
  ctx.lineWidth = 1; ctx.stroke();
  const dx = x1 - x0, dy = y1 - y0;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len;
  const size = 7, halfW = 3.5;
  const px = -uy, py = ux;
  ctx.beginPath(); ctx.moveTo(x1, y1);
  ctx.lineTo(x1 - ux * size + px * halfW, y1 - uy * size + py * halfW);
  ctx.lineTo(x1 - ux * size - px * halfW, y1 - uy * size - py * halfW);
  ctx.closePath();
  ctx.fillStyle = "rgba(15, 23, 42, 0.7)"; ctx.fill();
  ctx.beginPath(); ctx.arc(x1, y1, 2.5, 0, Math.PI * 2);
  ctx.fillStyle = "#334155"; ctx.fill();
}

/* ==========================================================================
   WALL → PLAN ESCAPE ROUTES
   ========================================================================== */

function drawWallToPlanArrows() {
  routeCache = [];
  if (!WALL.segments.length) return;
  const stripBaseY  = viewWall.ty;
  const stripRouteY = stripBaseY + router.ESCAPE_DROP;
  const BASE_COLOR = "rgba(139, 92, 246, 0.6)";
  const STEP_COLOR = "rgba(220, 38, 38, 0.55)";
  const HIGHLIGHT  = "#f59e0b";
  const HL_FILL    = "rgba(245, 158, 11, 0.18)";
  const ARROW_GAP  = router.ARROW_GAP;
  const midX = (GEOMETRY.bounds.minX + GEOMETRY.bounds.maxX) / 2;
  const routes = [];
  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    const wxMid = (s.a[0] + s.b[0]) / 2;
    const wyMid = (s.a[1] + s.b[1]) / 2;
    const uMid  = (s.u0 + s.u1) / 2;
    const [stripX] = w2sWall(uMid, 0);
    const [planX, planY] = w2sFloor(wxMid, wyMid);
    const useLeft = (wxMid < midX);
    routes.push({ stripX, stripBaseY, stripRouteY, planX, planY,
                  useLeft, segIdx: i, isStep: isStepFace(s) });
  }
  for (const r of routes) {
    r.slotX = r.useLeft ? router.leftBaseX : router.rightBaseX;
    const sign = r.useLeft ? 1 : -1;
    r.approachX = r.planX - sign * ARROW_GAP;
    r.sign = sign;
    routeCache.push(r);
  }
  const hl = state.hoveredRoute;
  if (hl) {
    const seg = WALL.segments[hl.segIdx];
    if (seg && !isSegHidden(hl.segIdx)) {
      const [x0, yTop] = w2sWall(seg.u0, segTop(seg));
      const [x1, yBot] = w2sWall(seg.u1, segBottom(seg));
      ctx.fillStyle = HL_FILL;
      ctx.fillRect(x0, yTop, x1 - x0, yBot - yTop);
      const [ax, ay] = w2sFloor(seg.a[0], seg.a[1]);
      const [bx, by] = w2sFloor(seg.b[0], seg.b[1]);
      ctx.save();
      ctx.strokeStyle = HIGHLIGHT; ctx.lineWidth = 6; ctx.lineCap = "round";
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by);
      ctx.stroke(); ctx.restore();
    }
  }
  ctx.save();
  ctx.lineWidth = 1.4; ctx.lineJoin = "round"; ctx.lineCap = "round";
  for (const r of routeCache) {
    const col = r.isStep ? STEP_COLOR : BASE_COLOR;
    ctx.strokeStyle = col;
    drawRoutePath(r);
    drawArrowhead(r.planX, r.planY, r.sign, 0, col);
    ctx.beginPath();
    ctx.arc(r.stripX, r.stripBaseY, 3, 0, Math.PI * 2);
    ctx.fillStyle = col; ctx.fill();
  }
  ctx.restore();
  if (hl && !isSegHidden(hl.segIdx)) {
    ctx.save();
    ctx.lineWidth = 2.6; ctx.lineJoin = "round"; ctx.lineCap = "round";
    ctx.strokeStyle = HIGHLIGHT;
    ctx.shadowColor = "rgba(245, 158, 11, 0.55)";
    ctx.shadowBlur = 6;
    drawRoutePath(hl);
    drawArrowhead(hl.planX, hl.planY, hl.sign, 0, HIGHLIGHT);
    ctx.beginPath();
    ctx.arc(hl.stripX, hl.stripBaseY, 5, 0, Math.PI * 2);
    ctx.fillStyle = HIGHLIGHT; ctx.fill();
    ctx.beginPath();
    ctx.arc(hl.planX, hl.planY, 5, 0, Math.PI * 2);
    ctx.fillStyle = HIGHLIGHT; ctx.fill();
    ctx.restore();
  }
}

function drawRoutePath(r) {
  ctx.beginPath();
  ctx.moveTo(r.stripX,   r.stripBaseY);
  ctx.lineTo(r.stripX,   r.stripRouteY);
  ctx.lineTo(r.slotX,    r.stripRouteY);
  ctx.lineTo(r.slotX,    r.planY);
  ctx.lineTo(r.approachX, r.planY);
  ctx.stroke();
}

function hitTestRoute(sx, sy) {
  if (!routeCache.length) return null;
  const TOL = 6;
  let best = null, bestDist = TOL;
  for (const r of routeCache) {
    const segs = [
      [r.stripX, r.stripBaseY, r.stripX, r.stripRouteY],
      [r.stripX, r.stripRouteY, r.slotX, r.stripRouteY],
      [r.slotX, r.stripRouteY, r.slotX, r.planY],
      [r.slotX, r.planY, r.approachX, r.planY],
    ];
    for (const [ax, ay, bx, by] of segs) {
      const d = pointToSegmentDist(sx, sy, ax, ay, bx, by);
      if (d < bestDist) { bestDist = d; best = r; }
    }
  }
  return best;
}

function drawArrowhead(x, y, dx, dy, color) {
  const size = 8, halfW = 4;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len;
  const px = -uy, py = ux;
  ctx.beginPath(); ctx.moveTo(x, y);
  ctx.lineTo(x - ux * size + px * halfW, y - uy * size + py * halfW);
  ctx.lineTo(x - ux * size - px * halfW, y - uy * size - py * halfW);
  ctx.closePath();
  ctx.fillStyle = color; ctx.fill();
}

/* ==========================================================================
   DRAWING PREVIEW
   ========================================================================== */

function drawDrawingPreview(view) {
  if (!drawing || drawing.view !== view) return;
  const toScreen = (view === "floor") ? w2sFloor : w2sWall;
  const s2w      = (view === "floor") ? s2wFloor : s2wWall;
  const project  = (view === "floor") ? anchorPlan : anchorWall;

  const visAnchor = (a) => {
    if (!a) return false;
    if (view !== "wall") return true;
    if (a.space !== "wall-edge") return true;
    return !isSegHidden(a.segIdx);
  };

  if (drawing.anchorIds.length > 0) {
    ctx.strokeStyle = "#0ea5e9"; ctx.lineWidth = 2.4;
    ctx.lineJoin = "round"; ctx.lineCap = "round";
    ctx.beginPath();
    let started = false;
    let lastA = null;
    const jumps = [];
    for (const id of drawing.anchorIds) {
      const a = anchors.get(id);
      if (!visAnchor(a)) { started = false; lastA = null; continue; }
      const p = a ? project(a) : null;
      if (!p) { started = false; lastA = null; continue; }
      const [sx, sy] = toScreen(p[0], p[1]);
      if (started && lastA && isHopTransition(lastA, a)) {
        const lp = project(lastA);
        const [lx, ly] = toScreen(lp[0], lp[1]);
        jumps.push({ fromX: lx, fromY: ly, toX: sx, toY: sy });
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(sx, sy);
      } else if (!started) {
        ctx.moveTo(sx, sy);
      } else {
        ctx.lineTo(sx, sy);
      }
      started = true;
      lastA = a;
    }
    ctx.stroke();
    for (const j of jumps) {
      drawHopArc(j.fromX, j.fromY, j.toX, j.toY, false);
    }

    for (const id of drawing.anchorIds) {
      const a = anchors.get(id);
      if (!visAnchor(a)) continue;
      const p = a ? project(a) : null;
      if (!p) continue;
      const [vx, vy] = toScreen(p[0], p[1]);
      const isStep = anchorIsStepFace(a);
      const side   = anchorSide(a);
      ctx.beginPath();
      ctx.arc(vx, vy, isStep ? 6 : 5, 0, Math.PI * 2);
      ctx.fillStyle = isStep ? "#f97316" : "#ffffff";
      ctx.fill();
      ctx.strokeStyle = isStep ? "#c2410c" : "#0ea5e9";
      ctx.lineWidth = 2; ctx.stroke();
      if (isStep) {
        ctx.beginPath();
        ctx.arc(vx, vy, 8.5, 0, Math.PI * 2);
        ctx.strokeStyle = (side === "top") ? "rgba(220, 38, 38, 0.85)"
                                           : "rgba(6, 182, 212, 0.85)";
        ctx.lineWidth = 1.4; ctx.stroke();
      }
    }
  }

  if (drawingPreview) {
    const [pwx, pwy] = s2w(drawingPreview.sx, drawingPreview.sy);

    let sticky   = null;
    let snapSpec = null;

    if (snapEnabled) {
      sticky = findAnchorAtScreen(view, drawingPreview.sx, drawingPreview.sy);

      if (!sticky) {
        snapSpec = (view === "floor")
          ? snapFloorSpec(pwx, pwy, true)
          : snapWallSpec(pwx, pwy, true);

        if (snapSpec && snapSpec.space === "wall-edge") {
          const scale = (view === "floor")
            ? viewFloor.scale
            : Math.min(viewWall.scaleX, viewWall.scaleY);
          const tolMm = ANCHOR_NEARBY_PX / scale;
          /* Magnetic snap to an existing anchor at the same segment. */
          const s = WALL.segments[snapSpec.segIdx];
          if (s) {
            for (const a of anchors.values()) {
              if (a.space !== "wall-edge") continue;
              if (a.segIdx !== snapSpec.segIdx) continue;
              if (Math.abs(a.v - snapSpec.v) > tolMm) continue;
              if (Math.abs(a.t - snapSpec.t) * s.len < tolMm) {
                sticky = a; snapSpec = null; break;
              }
            }
          }
        }
      }
    }

    let showX = pwx, showY = pwy;
    let show = true;
    if (sticky) {
      const p = project(sticky);
      if (p) { showX = p[0]; showY = p[1]; }
    } else if (snapSpec) {
      if (snapSpec.space === "floor") {
        showX = snapSpec.x; showY = snapSpec.y;
      } else if (snapSpec.space === "wall-edge") {
        const [px, py] = wallAttachToPlan(snapSpec.segIdx, snapSpec.t);
        if (view === "floor") { showX = px; showY = py; }
        else {
          showX = wallAttachToU(snapSpec.segIdx, snapSpec.t);
          showY = snapSpec.v;
        }
      }
    } else if (!snapEnabled && view === "wall") {
      const attach = uToWallAttach(pwx, pwy);
      if (attach) {
        showX = wallAttachToU(attach.segIdx, attach.t);
        showY = pwy;
      } else {
        show = false;
      }
    }
    const [px, py] = show ? toScreen(showX, showY) : [0, 0];

    if (show) {
      if (drawing.anchorIds.length > 0) {
        const lastId = drawing.anchorIds[drawing.anchorIds.length - 1];
        const lastA  = anchors.get(lastId);
        if (visAnchor(lastA)) {
          const lastP  = lastA ? project(lastA) : null;
          if (lastP) {
            const [lx, ly] = toScreen(lastP[0], lastP[1]);
            ctx.beginPath();
            ctx.moveTo(lx, ly); ctx.lineTo(px, py);
            ctx.setLineDash([6, 4]);
            ctx.strokeStyle = sticky ? "#10b981"
                            : snapEnabled ? "#0ea5e9" : "#94a3b8";
            ctx.lineWidth = 1.8;
            ctx.stroke(); ctx.setLineDash([]);
          }
        }
      }

      ctx.beginPath();
      ctx.arc(px, py, sticky ? 7 : (snapSpec ? 6 : 4), 0, Math.PI * 2);
      ctx.fillStyle = sticky ? "#10b981"
                    : snapSpec ? "#f59e0b"
                    : (snapEnabled ? "#0ea5e9" : "#94a3b8");
      ctx.fill();
      ctx.strokeStyle = sticky ? "#047857"
                      : snapSpec ? "#b45309"
                      : (snapEnabled ? "#0ea5e9" : "#94a3b8");
      ctx.lineWidth = 1.5; ctx.stroke();

      if (sticky) {
        ctx.beginPath();
        ctx.arc(px, py, 12, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(16, 185, 129, 0.8)";
        ctx.lineWidth = 2;
        ctx.setLineDash([3, 3]);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }
  }

  const tgt = findMergeTarget(view, mouse.sx, mouse.sy);
  if (tgt) {
    const E = tgt.cable;
    const a = anchors.get(E.anchorIds[tgt.endpointIdx]);
    if (visAnchor(a)) {
      const p = a ? project(a) : null;
      if (p) {
        const [tx, ty] = toScreen(p[0], p[1]);
        ctx.beginPath();
        ctx.arc(tx, ty, 10, 0, Math.PI * 2);
        ctx.strokeStyle = "#10b981"; ctx.lineWidth = 2.6; ctx.stroke();
        ctx.beginPath();
        ctx.arc(tx, ty, 5, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(16, 185, 129, 0.55)"; ctx.fill();
      }
    }
  }
}

function drawCrossViewGhosts() {
  if (!drawing || !drawingPreview) return;
  if (drawing.view === null) return;
  if (!snapEnabled) return;
  const view = drawing.view;
  const s2w = (view === "floor") ? s2wFloor : s2wWall;
  const [pwx, pwy] = s2w(drawingPreview.sx, drawingPreview.sy);
  if (view === "floor") {
    const wall = findWallAttachSpec(pwx, pwy);
    if (!wall) return;
    const [gx, gy] = w2sWall(wallAttachToU(wall.segIdx, wall.t), wall.v);
    drawGhostMarker(gx, gy);
  } else {
    const edge = findWallEdgeSnapSpec(pwx, pwy);
    if (!edge) return;
    const [px, py] = wallAttachToPlan(edge.segIdx, edge.t);
    const [gx, gy] = w2sFloor(px, py);
    drawGhostMarker(gx, gy);
  }
}

function drawGhostMarker(sx, sy) {
  ctx.save();
  ctx.beginPath();
  ctx.arc(sx, sy, 8, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(6, 182, 212, 0.28)";
  ctx.fill();
  ctx.strokeStyle = "rgba(6, 182, 212, 0.85)";
  ctx.lineWidth = 1.6; ctx.setLineDash([3, 3]); ctx.stroke();
  ctx.setLineDash([]);
  ctx.beginPath();
  ctx.arc(sx, sy, 2.6, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(6, 182, 212, 0.9)"; ctx.fill();
  ctx.restore();
}

/* ==========================================================================
   PROJECTING FLOOR CURSOR TO WALL
   ========================================================================== */

function projectOntoWalls(wx, wy) {
  const result = [];
  let minDist = Infinity, closestIdx = -1;
  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    const ax = s.a[0], ay = s.a[1], bx = s.b[0], by = s.b[1];
    const dx = bx - ax, dy = by - ay;
    const len2 = dx*dx + dy*dy;
    if (len2 < 1e-9) continue;
    const t = ((wx - ax)*dx + (wy - ay)*dy) / len2;
    if (t < 0 || t > 1) continue;
    const px = ax + t*dx, py = ay + t*dy;
    const nx = WALL.ccw ? -dy : dy;
    const ny = WALL.ccw ?  dx : -dx;
    if ((wx - px)*nx + (wy - py)*ny <= 0) continue;
    const d = Math.hypot(wx - px, wy - py);
    result.push({ u: s.u0 + t * s.len, dist: d, segIdx: i, closest: false });
    if (d < minDist) { minDist = d; closestIdx = result.length - 1; }
  }
  if (closestIdx >= 0) result[closestIdx].closest = true;
  return result;
}

/* ==========================================================================
   MERGE TARGET DETECTION
   ========================================================================== */

function findMergeTarget(view, sx, sy) {
  const list = (view === "floor") ? state.floorCables : state.wallCables;
  const project  = (view === "floor") ? anchorPlan : anchorWall;
  const toScreen = (view === "floor") ? w2sFloor   : w2sWall;
  const baseId = drawing ? drawing.baseCableId : null;
  let best = null, bestD = 14;
  for (const c of list) {
    if (c.id === baseId) continue;
    if (!c.anchorIds || c.anchorIds.length === 0) continue;
    for (const idx of [0, c.anchorIds.length - 1]) {
      const a = anchors.get(c.anchorIds[idx]);
      if (!a) continue;
      if (view === "wall" && a.space === "wall-edge" && isSegHidden(a.segIdx))
        continue;
      const p = project(a);
      if (!p) continue;
      const [px, py] = toScreen(p[0], p[1]);
      const d = Math.hypot(px - sx, py - sy);
      if (d < bestD) {
        bestD = d;
        best = { cable: c, endpointIdx: idx };
      }
    }
  }
  return best;
}

/* ==========================================================================
   DRAWING LIFECYCLE
   ========================================================================== */

function beginDraw() {
  drawing = { view: null, anchorIds: [], id: null, name: null, baseCableId: null };
  drawingPreview = null;
  state.selectedCable = null;
  state.selectedVertex = null;
  flashStatus("Drawing — click first point in the floor plan OR wall strip · Enter / Esc finishes", "ok");
  draw();
}

function cancelDraw(silent) {
  if (!drawing) return;
  exitFocus();
  drawing = null; drawingPreview = null;
  if (!silent) flashStatus("Draw cancelled", "warn");
  draw();
}

function finishDraw() {
  if (!drawing) return;
  if (drawing.view === null) { cancelDraw(); return; }
  if (drawing.anchorIds.length < 2) {
    flashStatus("Draw cancelled (need at least 2 points)", "warn");
    exitFocus();
    drawing = null; drawingPreview = null; draw();
    return;
  }
  const list = (drawing.view === "floor") ? state.floorCables : state.wallCables;
  if (drawing.baseCableId != null) removeCableById(drawing.baseCableId);
  const c = {
    id: (drawing.id != null) ? drawing.id : idCounter++,
    name: drawing.name || ("Cable " + (state.floorCables.length + state.wallCables.length + 1)),
    anchorIds: drawing.anchorIds.slice(),
  };
  list.push(c);
  state.selectedCable = c;
  gcAnchors();
  recomputeTrueCables();
  exitFocus();
  drawing = null; drawingPreview = null;
  flashStatus("✓ Cable created (" + c.anchorIds.length + " points)", "ok");
  draw();
}

function addDrawPoint(sx, sy, altKey) {
  if (!drawing) return;
  const view = drawing.view;

  /* Merge / adopt is explicit: hold Alt while clicking an existing
     endpoint.  Without Alt, a click near an endpoint shares the anchor
     and starts (or continues) a fresh cable — the two cables connect via
     the shared anchor, and true-cable grouping shows them as one physical
     cable, without being merged into a single stored cable. */
  if (altKey) {
    const target = findMergeTarget(view, sx, sy);
    if (target) {
      if (drawing.anchorIds.length === 0) adoptMergeBase(target);
      else                               mergeDrawingInto(target);
      return;
    }
  }

  let anchorId = null;
  const nearby = findAnchorAtScreen(view, sx, sy);
  if (nearby) anchorId = nearby.id;

  let spec = null;
  if (anchorId == null) {
    if (view === "floor") {
      const [wx, wy] = s2wFloor(sx, sy);
      spec = snapFloorSpec(wx, wy, snapEnabled);
    } else {
      const [u, v] = s2wWall(sx, sy);
      spec = snapWallSpec(u, v, snapEnabled);
      if (!spec) { draw(); return; }
    }
  }

  const scale = (view === "floor")
    ? viewFloor.scale
    : Math.min(viewWall.scaleX, viewWall.scaleY);
  const tolMm = ANCHOR_NEARBY_PX / scale;

  /* Wormhole route planner (wall view only).  When the click resolves to a
     wall-edge on a different segment than the previous anchor, plan a route
     through the physical adjacency graph; the resulting anchors replace
     the single-anchor append. */
  if (view === "wall" && anchorId == null && spec &&
      spec.space === "wall-edge" && drawing.anchorIds.length > 0) {
    const lastId = drawing.anchorIds[drawing.anchorIds.length - 1];
    const last = anchors.get(lastId);
    if (last && last.space === "wall-edge" && last.segIdx !== spec.segIdx) {
      const route = planWormholeRoute(
        { segIdx: last.segIdx, t: last.t, v: last.v },
        { segIdx: spec.segIdx, t: spec.t, v: spec.v }
      );
      if (route && route.length > 1) {
        let inserted = 0;
        for (let i = 1; i < route.length; i++) {
          const rid = resolveSpecToAnchorId(route[i], 5);
          if (rid == null) continue;
          const lastInList = drawing.anchorIds[drawing.anchorIds.length - 1];
          if (lastInList !== rid) {
            drawing.anchorIds.push(rid);
            inserted++;
          }
        }
        if (inserted > 0) {
          const lastAddedId = drawing.anchorIds[drawing.anchorIds.length - 1];
          const la = anchors.get(lastAddedId);
          if (la && la.space === "wall-edge" && la.segIdx !== focusSeg) {
            enterFocus(la.segIdx);
          }
          draw();
          return;
        }
      }
    }
  }

  /* Normal single-anchor path. */
  if (anchorId == null) {
    anchorId = resolveSpecToAnchorId(spec, tolMm);
    if (anchorId == null) { draw(); return; }
  }
  const last = drawing.anchorIds[drawing.anchorIds.length - 1];
  if (last === anchorId) { draw(); return; }
  drawing.anchorIds.push(anchorId);

  if (view === "wall") {
    const a = anchors.get(anchorId);
    let segIdx = -1;
    if (a && a.space === "wall-edge") segIdx = a.segIdx;
    if (segIdx >= 0 && segIdx !== focusSeg) {
      enterFocus(segIdx);
    }
  }

  draw();
}

function findAnchorAtScreen(view, sx, sy) {
  const project  = (view === "floor") ? anchorPlan : anchorWall;
  const toScreen = (view === "floor") ? w2sFloor   : w2sWall;
  let best = null, bestD = ANCHOR_NEARBY_PX;
  for (const a of anchors.values()) {
    if (view === "wall" && a.space === "wall-edge" && isSegHidden(a.segIdx))
      continue;
    const p = project(a);
    if (!p) continue;
    const [px, py] = toScreen(p[0], p[1]);
    const d = Math.hypot(px - sx, py - sy);
    if (d < bestD) { bestD = d; best = a; }
  }
  return best;
}

function adoptMergeBase(target) {
  const E = target.cable;
  const ids = (target.endpointIdx === 0)
    ? E.anchorIds.slice().reverse()
    : E.anchorIds.slice();
  drawing.id = E.id;
  drawing.name = E.name;
  drawing.anchorIds = ids;
  drawing.baseCableId = E.id;
  if (drawing.view === "wall") {
    const lastId = ids[ids.length - 1];
    const a = anchors.get(lastId);
    if (a && a.space === "wall-edge") enterFocus(a.segIdx);
  }
  flashStatus("Adopted existing cable — keep clicking to extend it", "ok");
  draw();
}

function mergeDrawingInto(target) {
  const E = target.cable;
  const otherIds = (target.endpointIdx === 0)
    ? E.anchorIds.slice()
    : E.anchorIds.slice().reverse();
  let tail = otherIds;
  if (drawing.anchorIds.length > 0 && tail.length > 0 &&
      drawing.anchorIds[drawing.anchorIds.length - 1] === tail[0]) {
    tail = tail.slice(1);
  }
  const mergedIds = drawing.anchorIds.concat(tail);

  if (drawing.baseCableId != null && drawing.baseCableId !== E.id)
    removeCableById(drawing.baseCableId);
  removeCableById(E.id);

  const list = (drawing.view === "floor") ? state.floorCables : state.wallCables;
  const c = {
    id: (drawing.id != null) ? drawing.id : idCounter++,
    name: drawing.name || ("Cable " + (state.floorCables.length + state.wallCables.length + 1)),
    anchorIds: mergedIds,
  };
  list.push(c);
  state.selectedCable = c;
  state.selectedVertex = null;
  gcAnchors();
  recomputeTrueCables();
  exitFocus();
  drawing = null; drawingPreview = null;
  flashStatus("✓ Merged into existing cable (" + c.anchorIds.length + " points)", "ok");
  draw();
}

function removeCableById(id) {
  let i = state.floorCables.findIndex(c => c.id === id);
  if (i >= 0) { state.floorCables.splice(i, 1); return; }
  i = state.wallCables.findIndex(c => c.id === id);
  if (i >= 0) { state.wallCables.splice(i, 1); }
}

function gcAnchors() {
  const referenced = new Set();
  for (const c of state.floorCables) for (const id of c.anchorIds) referenced.add(id);
  for (const c of state.wallCables)  for (const id of c.anchorIds) referenced.add(id);
  for (const id of Array.from(anchors.keys())) {
    if (!referenced.has(id)) anchors.delete(id);
  }
}

/* ==========================================================================
   DELETION
   ========================================================================== */

function deleteSelectedCable() {
  const c = state.selectedCable;
  if (!c) { flashStatus("No cable selected", "warn"); return; }
  removeCableById(c.id);
  state.selectedCable = null; state.selectedVertex = null;
  gcAnchors();
  recomputeTrueCables();
  flashStatus("✓ Cable deleted", "ok");
  draw();
}

function deleteSelectedVertex() {
  const sv = state.selectedVertex;
  if (!sv) { flashStatus("No vertex selected — click a cable vertex first", "warn"); return; }
  const { cable, index } = sv;
  if (!cable || !cable.anchorIds ||
      index < 0 || index >= cable.anchorIds.length) {
    state.selectedVertex = null; draw(); return;
  }
  cable.anchorIds.splice(index, 1);
  if (cable.anchorIds.length < 2) {
    removeCableById(cable.id);
    state.selectedCable = null;
  }
  state.selectedVertex = null;
  gcAnchors();
  recomputeTrueCables();
  flashStatus("✓ Vertex deleted", "ok");
  draw();
}

/* ==========================================================================
   DRAGGING
   ========================================================================== */

function convertAnchor(a, spec) {
  delete a.x; delete a.y; delete a.segIdx; delete a.t;
  delete a.gridId; delete a.gridAxis;
  a.space = spec.space;
  for (const k of Object.keys(spec)) {
    if (k === "space") continue;
    a[k] = spec[k];
  }
  if (a.space === "wall-edge") clampAnchorToSegment(a);
}

/* Corner info for a wall-edge anchor at a segment endpoint.  Looked up once
   at mousedown; held for the entire drag.  Partner list is filtered by
   v-proximity: two cables that terminate at the same physical corner but
   at different heights are distinct points in 3D and must not drag
   together. */
function computeCornerInfo(a) {
  const jid = junctionOfAnchor(a);
  if (jid < 0) return null;
  const side = Math.abs(a.t) < 0.01 ? 0 : 1;

  const vSource = a.v || 0;
  const partners = [];
  const j = JUNCTIONS[jid];
  if (j) {
    for (const t of j.terminals) {
      if (t.segIdx === a.segIdx) continue;
      for (const other of anchors.values()) {
        if (other === a) continue;
        if (other.space !== "wall-edge") continue;
        if (other.segIdx !== t.segIdx) continue;
        const oside = Math.abs(other.t) < 0.01 ? 0
                    : Math.abs(other.t - 1) < 0.01 ? 1 : -1;
        if (oside !== t.side) continue;
        if (Math.abs((other.v || 0) - vSource) > 5.0) continue;
        partners.push(other);
      }
    }
  }
  return { junctionId: jid, side, partners };
}

/* Constrained drag: t pinned to the corner's side; only v moves; every
   cached partner mirrors v (clamped into its own legal range). */
function _applyCornerDrag(a, corner, sx, sy) {
  const s = WALL.segments[a.segIdx];
  if (!s) return;
  const [u, v] = s2wWall(sx, sy);

  let nv;
  if (isStepFace(s)) {
    nv = (v > segTop(s) * 0.5) ? segTop(s) : 0;
  } else {
    nv = Math.max(segBottom(s), Math.min(segTop(s), v));
  }
  if (snapEnabled) {
    const SNAP = 22 / Math.min(viewWall.scaleX, viewWall.scaleY);
    if (Math.abs(v) < SNAP) nv = 0;
    if (isStepFace(s) && Math.abs(v - segTop(s)) < SNAP) nv = segTop(s);
  }

  a.t = corner.side;
  a.v = nv;
  clampAnchorToSegment(a);

  for (const p of corner.partners) {
    p.v = a.v;
    clampAnchorToSegment(p);
  }
}

/* Unconstrained drag for non-corner anchors. */
function _applyVertexDrag(a, view, sx, sy) {
  if (view === "floor") {
    const [wx, wy] = s2wFloor(sx, sy);
    if (snapEnabled) {
      const SNAP = 14 / viewFloor.scale;
      for (const g of state.floorGrids) {
        if (g.type === "ns" && Math.abs(wx - g.pos) < SNAP) {
          convertAnchor(a, { space: "floor", x: g.pos, y: wy,
                             gridId: g.id, gridAxis: "ns" });
          return;
        }
        if (g.type === "ew" && Math.abs(wy - g.pos) < SNAP) {
          convertAnchor(a, { space: "floor", x: wx, y: g.pos,
                             gridId: g.id, gridAxis: "ew" });
          return;
        }
      }
      const wall = findWallAttachSpec(wx, wy);
      if (wall) { convertAnchor(a, wall); return; }
    }
    convertAnchor(a, { space: "floor", x: wx, y: wy });
    return;
  }

  const [u, v] = s2wWall(sx, sy);

  if (snapEnabled) {
    const edge = findWallEdgeSnapSpec(u, v);
    if (edge) { convertAnchor(a, edge); return; }
  }

  const attach = uToWallAttach(u, v);
  if (attach) {
    const s = WALL.segments[attach.segIdx];
    const vv = Math.max(segBottom(s), Math.min(segTop(s), v));
    convertAnchor(a, { space: "wall-edge", segIdx: attach.segIdx,
                       t: attach.t, v: vv });
    return;
  }
  const i = nearestSegmentToUV(u, v);
  if (i < 0) return;
  const s = WALL.segments[i];
  const t = Math.max(0, Math.min(1, (u - s.u0) / s.len));
  const vv = Math.max(segBottom(s), Math.min(segTop(s), v));
  convertAnchor(a, { space: "wall-edge", segIdx: i, t, v: vv });
}

function handleVertexDrag(sx, sy) {
  const { cable, index, view, corner } = state.dragVertex;
  const anchorId = cable.anchorIds[index];
  const a = anchors.get(anchorId);
  if (!a) return;

  if (view === "wall" && corner) {
    _applyCornerDrag(a, corner, sx, sy);
    return;
  }

  _applyVertexDrag(a, view, sx, sy);
}

function handleGridDrag(sx, sy) {
  const { grid, view } = state.dragGrid;
  if (view === "floor") {
    const [wx, wy] = s2wFloor(sx, sy);
    grid.pos = (grid.type === "ns") ? wx : wy;
    for (const a of anchors.values()) {
      if (a.space !== "floor") continue;
      if (a.gridId !== grid.id) continue;
      if (grid.type === "ns") a.x = grid.pos;
      else                    a.y = grid.pos;
    }
  } else {
    const [u, v] = s2wWall(sx, sy);
    grid.pos = (grid.type === "v") ? u : v;
    for (const a of anchors.values()) {
      if (a.space !== "wall-edge") continue;
      if (a.gridId !== grid.id) continue;
      const uv = anchorWall(a);
      if (!uv) continue;
      if (grid.type === "v") {
        const attach = uToWallAttach(grid.pos, uv[1]);
        if (attach) {
          a.segIdx = attach.segIdx;
          a.t = attach.t;
        }
      } else {
        a.v = grid.pos;
        clampAnchorToSegment(a);
      }
    }
  }
}

/* ==========================================================================
   MAIN DRAW DISPATCH
   ========================================================================== */

function draw() {
  const cw = window.innerWidth, ch = window.innerHeight;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#fafafa";
  ctx.fillRect(0, 0, cw, ch);

  ctx.save();
  ctx.beginPath(); ctx.rect(0, 0, cw, layout.floorH); ctx.clip();
  drawFloorView();
  ctx.restore();

  ctx.fillStyle = "#334155";
  ctx.fillRect(0, layout.dividerY, cw, 4);

  ctx.save();
  ctx.beginPath(); ctx.rect(0, layout.wallY, cw, layout.wallH); ctx.clip();
  drawWallView();
  ctx.restore();

  drawWallToPlanArrows();
  drawCrossViewGhosts();
  updateStatus();
  updateDrawButton();
}

/* ==========================================================================
   HIT TESTING
   ========================================================================== */

function nearestFloorVertex(sx, sy) {
  for (let ci = state.floorCables.length - 1; ci >= 0; ci--) {
    const c = state.floorCables[ci];
    if (drawing && drawing.baseCableId === c.id) continue;
    for (let i = 0; i < c.anchorIds.length; i++) {
      const a = anchors.get(c.anchorIds[i]);
      const p = a ? anchorPlan(a) : null;
      if (!p) continue;
      const [vx, vy] = w2sFloor(p[0], p[1]);
      if (Math.hypot(sx - vx, sy - vy) < 9) return { cable: c, index: i };
    }
  }
  return null;
}
function nearestWallVertex(sx, sy) {
  for (let ci = state.wallCables.length - 1; ci >= 0; ci--) {
    const c = state.wallCables[ci];
    if (drawing && drawing.baseCableId === c.id) continue;
    for (let i = 0; i < c.anchorIds.length; i++) {
      const a = anchors.get(c.anchorIds[i]);
      if (!a) continue;
      if (a.space === "wall-edge" && isSegHidden(a.segIdx)) continue;
      const p = anchorWall(a);
      if (!p) continue;
      const [vx, vy] = w2sWall(p[0], p[1]);
      if (Math.hypot(sx - vx, sy - vy) < 9) return { cable: c, index: i };
    }
  }
  return null;
}
function nearestFloorGridLine(sx, sy) {
  for (const g of state.floorGrids) {
    if (g.type === "ns") {
      const [gx] = w2sFloor(g.pos, 0);
      if (Math.abs(sx - gx) < 7) return g;
    } else {
      const [, gy] = w2sFloor(0, g.pos);
      if (Math.abs(sy - gy) < 7) return g;
    }
  }
  return null;
}
function nearestWallGridLine(sx, sy) {
  if (focusSavedU) return null;
  for (const g of state.wallGrids) {
    if (g.type === "v") {
      const [gx] = w2sWall(g.pos, 0);
      if (Math.abs(sx - gx) < 7) return g;
    } else {
      const [, gy] = w2sWall(0, g.pos);
      if (Math.abs(sy - gy) < 7) return g;
    }
  }
  return null;
}

function polylineHitView(view, x, y, c, tol) {
  const project  = (view === "floor") ? anchorPlan : anchorWall;
  const pts = [];
  for (const id of c.anchorIds) {
    const a = anchors.get(id);
    if (view === "wall" && a && a.space === "wall-edge" && isSegHidden(a.segIdx))
      continue;
    const p = a ? project(a) : null;
    if (p) pts.push(p);
  }
  return polylineHit(x, y, pts, tol);
}

/* ==========================================================================
   EVENT HANDLERS
   ========================================================================== */

canvas.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const view = whichView(sy);
  if (!view) return;

  if (drawing) {
    if (drawing.view === null) drawing.view = view;
    else if (drawing.view !== view) return;
    addDrawPoint(sx, sy, e.altKey);
    return;
  }

  if (view === "floor") {
    const g = nearestFloorGridLine(sx, sy);
    if (g) { state.dragGrid = { grid: g, view: "floor" }; return; }
    const v = nearestFloorVertex(sx, sy);
    if (v) {
      state.selectedCable = v.cable;
      state.selectedVertex = v;
      state.dragVertex = { ...v, view: "floor", corner: null };
      draw(); return;
    }
    const [wx, wy] = s2wFloor(sx, sy);
    const tol = 12 / viewFloor.scale;
    for (let i = state.floorCables.length - 1; i >= 0; i--) {
      const c = state.floorCables[i];
      if (drawing && drawing.baseCableId === c.id) continue;
      if (polylineHitView("floor", wx, wy, c, tol)) {
        state.selectedCable = c;
        state.selectedVertex = null;
        draw(); return;
      }
    }
    state.selectedCable = null; state.selectedVertex = null; draw();
  } else {
    const g = nearestWallGridLine(sx, sy);
    if (g) { state.dragGrid = { grid: g, view: "wall" }; return; }
    const v = nearestWallVertex(sx, sy);
    if (v) {
      state.selectedCable = v.cable;
      state.selectedVertex = v;
      const a = anchors.get(v.cable.anchorIds[v.index]);
      const corner = computeCornerInfo(a);
      state.dragVertex = { ...v, view: "wall", corner };
      draw(); return;
    }
    const [u, vv] = s2wWall(sx, sy);
    const tol = 12 / Math.min(viewWall.scaleX, viewWall.scaleY);
    for (let i = state.wallCables.length - 1; i >= 0; i--) {
      const c = state.wallCables[i];
      if (drawing && drawing.baseCableId === c.id) continue;
      if (polylineHitView("wall", u, vv, c, tol)) {
        state.selectedCable = c;
        state.selectedVertex = null;
        draw(); return;
      }
    }
    state.selectedCable = null; state.selectedVertex = null; draw();
  }
});

canvas.addEventListener("contextmenu", (e) => {
  if (!drawing) return;
  e.preventDefault();
  if (drawing.anchorIds.length > 0) {
    drawing.anchorIds.pop();
    if (drawing.view === "wall" && drawing.anchorIds.length > 0) {
      const lastId = drawing.anchorIds[drawing.anchorIds.length - 1];
      const a = anchors.get(lastId);
      if (a && a.space === "wall-edge") enterFocus(a.segIdx);
    } else if (drawing.view === "wall") {
      exitFocus();
    }
    draw();
  }
});

window.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  mouse.sx = sx; mouse.sy = sy;
  mouse.inside = (sx >= 0 && sy >= 0 &&
                  sx < window.innerWidth && sy < window.innerHeight);
  mouse.view = mouse.inside ? whichView(sy) : null;

  if (state.dragGrid)   { handleGridDrag(sx, sy);   draw(); return; }
  if (state.dragVertex) { handleVertexDrag(sx, sy); draw(); return; }

  let hovered = null;
  if (!drawing && mouse.inside) hovered = hitTestRoute(sx, sy);
  state.hoveredRoute = hovered;

  if (drawing) {
    if (drawing.view === null) {
      drawingPreview = mouse.inside ? { sx, sy } : null;
    } else if (mouse.inside && mouse.view === drawing.view) {
      drawingPreview = { sx, sy };
    } else {
      drawingPreview = null;
    }
  }
  updateCursor();
  draw();
});

window.addEventListener("mouseup", () => {
  state.dragGrid = null;
  state.dragVertex = null;
  updateCursor();
  draw();
});

/* ==========================================================================
   UI WIRING
   ========================================================================== */

function addFloorNsGrid() {
  const cx = (GEOMETRY.bounds.minX + GEOMETRY.bounds.maxX) / 2;
  state.floorGrids.push({ id: idCounter++, type: "ns", pos: cx }); draw();
}
function addFloorEwGrid() {
  const cy = (GEOMETRY.bounds.minY + GEOMETRY.bounds.maxY) / 2;
  state.floorGrids.push({ id: idCounter++, type: "ew", pos: cy }); draw();
}
function addWallVGrid() {
  state.wallGrids.push({ id: idCounter++, type: "v", pos: WALL.totalU * 0.5 }); draw();
}
function addWallHGrid() {
  state.wallGrids.push({ id: idCounter++, type: "h", pos: WALL_HEIGHT * 0.5 }); draw();
}

const drawBtnEl = document.getElementById("drawBtn");
function updateDrawButton() {
  if (!drawBtnEl) return;
  if (drawing) {
    drawBtnEl.classList.add("active");
    drawBtnEl.textContent = "✖ Cancel draw";
  } else {
    drawBtnEl.classList.remove("active");
    drawBtnEl.textContent = "✏️ Draw cable";
  }
}

let statusTimer = null;
const statusEl = document.getElementById("status");
function updateStatus() {
  if (statusTimer) return;
  if (drawing) {
    const n = drawing.anchorIds.length;
    const snapHint = snapEnabled ? " · snap ON" : " · hold Shift to snap";
    if (drawing.view === null) {
      statusEl.textContent =
        "drawing · click first point in floor plan OR wall strip" +
        " · Alt+click endpoint to adopt/merge" + snapHint;
    } else {
      const base = drawing.baseCableId != null ? " · adopted a base cable" : "";
      let focusHint = "";
      if (drawing.view === "wall" && focusSeg >= 0) {
        const s = WALL.segments[focusSeg];
        const tag = s && s.tag ? s.tag : ("segment " + (focusSeg + 1));
        focusHint = " · FOCUS " + tag;
      }
      statusEl.textContent =
        `drawing on ${drawing.view} · ${n} point${n === 1 ? "" : "s"}${base}${focusHint}` +
        ` · Alt+click endpoint to adopt/merge${snapHint} · Enter / Esc finishes`;
    }
    statusEl.className = "warn"; return;
  }
  if (state.dragGrid) {
    const g = state.dragGrid.grid;
    statusEl.textContent = `dragging ${state.dragGrid.view} grid · ${g.type}=${g.pos.toFixed(0)}`;
    statusEl.className = ""; return;
  }
  if (state.dragVertex) {
    const { cable, index } = state.dragVertex;
    const a = anchors.get(cable.anchorIds[index]);
    let tag = "";
    if (a) {
      if (a.space === "wall-edge") {
        const s = anchorSide(a);
        if (s === "top")         tag = " · STEP-TOP (red)";
        else if (s === "bottom") tag = " · STEP-BOTTOM (cyan)";
        else                     tag = " · WALL-EDGE (cyan)";
        if (junctionOfAnchor(a) >= 0) tag += " · CORNER";
      } else if (a.gridId != null) {
        tag = " · GRID";
      } else {
        tag = " · free";
      }
    }
    const snapHint = snapEnabled ? "" : " · hold Shift to snap";
    statusEl.textContent = `vertex ${index}${tag}${snapHint}`;
    statusEl.className = (a && a.space === "wall-edge") ? "warn" : "";
    return;
  }
  if (state.hoveredRoute) {
    const seg = WALL.segments[state.hoveredRoute.segIdx];
    const tag = seg && seg.tag ? seg.tag : `segment ${state.hoveredRoute.segIdx + 1}`;
    const len = seg ? fmtCm(seg.len) : "?";
    const kind = isStepFace(seg) ? "  ·  step riser" : "";
    statusEl.textContent = `→ hovering ${tag}${kind}  ·  width ${len} cm`;
    statusEl.className = "warn"; return;
  }
  if (state.selectedCable) {
    const sibs = trueCableSiblings(state.selectedCable);
    if (sibs.length > 1) {
      const tid = trueCableIdOf(state.selectedCable);
      const floors = sibs.filter(c => state.floorCables.includes(c)).length;
      const walls  = sibs.filter(c => state.wallCables.includes(c)).length;
      statusEl.textContent =
        `Cable ${tid}  ·  ${sibs.length} parts (${floors} floor, ${walls} wall)`;
      statusEl.className = "ok";
      return;
    }
  }
  if (!mouse.inside || !mouse.view) {
    statusEl.textContent = ""; statusEl.className = ""; return;
  }
  if (mouse.view === "floor") {
    const [wx, wy] = s2wFloor(mouse.sx, mouse.sy);
    const onStep = pointOnStep(wx, wy) ? "  ·  on step" : "";
    statusEl.textContent = `floor  x=${wx.toFixed(0)}  y=${wy.toFixed(0)}${onStep}`;
    statusEl.className = "ok";
  } else {
    const [u, v] = s2wWall(mouse.sx, mouse.sy);
    statusEl.textContent = `wall   u=${u.toFixed(0)}  v=${v.toFixed(0)}`;
    statusEl.className = "ok";
  }
}
function flashStatus(msg, cls) {
  statusEl.textContent = msg; statusEl.className = cls;
  if (statusTimer) clearTimeout(statusTimer);
  statusTimer = setTimeout(() => { statusTimer = null; draw(); }, 1400);
}
function updateCursor() {
  if (!mouse.inside) { canvas.style.cursor = "default"; return; }
  if (drawing) { canvas.style.cursor = "crosshair"; return; }
  if (state.hoveredRoute) { canvas.style.cursor = "pointer"; return; }
  canvas.style.cursor = "crosshair";
}

/* ==========================================================================
   SAVE / LOAD (v3 format; wall-edge anchors carry segRef + world)
   ========================================================================== */

function serializeAnchor(a) {
  if (a.space === "floor") {
    return { id: a.id, space: "floor", x: a.x, y: a.y,
             gridId: a.gridId, gridAxis: a.gridAxis };
  }
  if (a.space === "wall-edge") {
    const s = WALL.segments[a.segIdx];
    const p = wallAttachToPlan(a.segIdx, a.t);
    const out = { id: a.id, space: "wall-edge",
                  t: a.t, v: a.v, world: [p[0], p[1]] };
    if (s) out.segRef = segRefOf(s);
    if (a.gridId != null) { out.gridId = a.gridId; out.gridAxis = a.gridAxis; }
    return out;
  }
  return null;
}

function deserializeAnchor(data) {
  if (!data || typeof data !== "object") return null;
  if (data.space === "floor") {
    return { id: data.id, space: "floor", x: data.x, y: data.y,
             gridId: data.gridId, gridAxis: data.gridAxis };
  }
  if (data.space === "wall-edge" || data.space === "wall") {
    /* Both old 'wall' (u, v) anchors and new 'wall-edge' anchors flow into
       the same wall-edge representation. */
    let segIdx, t, v;
    if (data.space === "wall-edge") {
      segIdx = findSegmentByRef(data.segRef);
      if (segIdx < 0 && Array.isArray(data.world)) {
        segIdx = findSegmentByWorld(data.world);
      }
      if (segIdx < 0 && typeof data.segIdx === "number") {
        segIdx = data.segIdx;
      }
      if (segIdx < 0 || !WALL.segments[segIdx]) return null;
      t = (typeof data.t === "number") ? data.t : 0;
      if (Array.isArray(data.world)) {
        const s = WALL.segments[segIdx];
        const dx = s.b[0] - s.a[0], dy = s.b[1] - s.a[1];
        const len2 = dx * dx + dy * dy;
        if (len2 > 1e-9) {
          t = ((data.world[0] - s.a[0]) * dx +
               (data.world[1] - s.a[1]) * dy) / len2;
          t = Math.max(0, Math.min(1, t));
        }
      }
      v = data.v;
    } else {
      /* Legacy 'wall': (u, v).  Locate the segment containing u. */
      const attach = uToWallAttach(data.u, data.v);
      if (!attach) return null;
      segIdx = attach.segIdx;
      t = attach.t;
      v = data.v;
    }
    const out = { id: data.id, space: "wall-edge", segIdx, t, v };
    if (data.gridId != null) { out.gridId = data.gridId; out.gridAxis = data.gridAxis; }
    clampAnchorToSegment(out);
    return out;
  }
  return null;
}

function serialize() {
  const anchorArr = [];
  for (const a of anchors.values()) {
    const s = serializeAnchor(a);
    if (s) anchorArr.push(s);
  }
  return {
    version: 3,
    idCounter: idCounter,
    floorGrids: state.floorGrids,
    wallGrids:  state.wallGrids,
    anchors: anchorArr,
    floorCables: state.floorCables.map(c => ({
      id: c.id, name: c.name, anchorIds: c.anchorIds.slice(),
    })),
    wallCables: state.wallCables.map(c => ({
      id: c.id, name: c.name, anchorIds: c.anchorIds.slice(),
    })),
  };
}

function migrateV1Cable(c, view) {
  if (c.role === "step-bridge") return null;
  const ids = [];
  for (let i = 0; i < c.points.length; i++) {
    const cap = c.captured && c.captured[i];
    let a = null;
    if (cap && cap.type === "wall-edge") {
      a = makeAnchor("wall-edge", {
        segIdx: cap.segIdx, t: cap.t,
        v: (typeof cap.v === "number") ? cap.v : 0,
      });
      clampAnchorToSegment(a);
    } else if (cap && (cap.type === "ns" || cap.type === "ew")) {
      a = makeAnchor("floor", {
        x: c.points[i][0], y: c.points[i][1],
        gridId: cap.gridId, gridAxis: cap.type,
      });
    } else if (cap && (cap.type === "v" || cap.type === "h")) {
      const attach = uToWallAttach(c.points[i][0], c.points[i][1]);
      if (attach) {
        a = makeAnchor("wall-edge", {
          segIdx: attach.segIdx, t: attach.t, v: c.points[i][1],
          gridId: cap.gridId, gridAxis: cap.type,
        });
        clampAnchorToSegment(a);
      }
    } else if (view === "floor") {
      a = makeAnchor("floor", { x: c.points[i][0], y: c.points[i][1] });
    } else {
      const attach = uToWallAttach(c.points[i][0], c.points[i][1]);
      if (attach) {
        a = makeAnchor("wall-edge", {
          segIdx: attach.segIdx, t: attach.t, v: c.points[i][1],
        });
        clampAnchorToSegment(a);
      }
    }
    if (a) ids.push(a.id);
  }
  return { id: c.id, name: c.name, anchorIds: ids };
}

function applyLoaded(data) {
  if (!data || typeof data !== "object") return;

  state.floorGrids = Array.isArray(data.floorGrids) ? data.floorGrids : [];
  state.wallGrids  = Array.isArray(data.wallGrids)  ? data.wallGrids  : [];

  let maxId = data.idCounter || 0;
  if (Array.isArray(data.anchors)) {
    for (const a of data.anchors)
      if (typeof a.id === "number") maxId = Math.max(maxId, a.id);
  }
  for (const c of (data.floorCables || []))
    if (typeof c.id === "number") maxId = Math.max(maxId, c.id);
  for (const c of (data.wallCables || []))
    if (typeof c.id === "number") maxId = Math.max(maxId, c.id);
  for (const g of state.floorGrids) if (typeof g.id === "number") maxId = Math.max(maxId, g.id);
  for (const g of state.wallGrids)  if (typeof g.id === "number") maxId = Math.max(maxId, g.id);
  idCounter = maxId + 1;

  anchors.clear();
  state.floorCables = [];
  state.wallCables  = [];

  const v = data.version || 1;

  if (v >= 2 && Array.isArray(data.anchors)) {
    for (const raw of data.anchors) {
      const a = deserializeAnchor(raw);
      if (a) anchors.set(a.id, a);
    }
    state.floorCables = (data.floorCables || []).map(c => ({
      id: c.id, name: c.name, anchorIds: c.anchorIds.slice(),
    })).filter(c => c.anchorIds.every(id => anchors.has(id)));
    state.wallCables = (data.wallCables || []).map(c => ({
      id: c.id, name: c.name, anchorIds: c.anchorIds.slice(),
    })).filter(c => c.anchorIds.every(id => anchors.has(id)));
  } else {
    for (const c of (data.floorCables || [])) {
      const migrated = migrateV1Cable(c, "floor");
      if (migrated) state.floorCables.push(migrated);
    }
    for (const c of (data.wallCables || [])) {
      const migrated = migrateV1Cable(c, "wall");
      if (migrated) state.wallCables.push(migrated);
    }
    gcAnchors();
  }

  recomputeTrueCables();
}

async function saveToServer() {
  try {
    const r = await fetch("/save", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(serialize()),
    });
    return r.ok;
  } catch (e) { return false; }
}
async function loadFromServer() {
  try {
    const r = await fetch(STATE_FILE, {cache: "no-store"});
    if (!r.ok) return null;
    const txt = await r.text();
    if (!txt.trim()) return null;
    return JSON.parse(txt);
  } catch (e) { return null; }
}
async function doSave() {
  const ok = await saveToServer();
  if (ok) flashStatus("✓ Saved " + STATE_FILE, "ok");
  else    flashStatus("✗ Save failed (no server?)", "bad");
}

/* ==========================================================================
   PANEL COLLAPSE / RESTORE
   ========================================================================== */

const uiEl = document.getElementById("ui");
const restoreBtnEl = document.getElementById("restoreBtn");
const collapseBtnEl = document.getElementById("collapseBtn");

function collapsePanel() {
  uiEl.classList.add("collapsed");
  restoreBtnEl.classList.add("visible");
}
function restorePanel() {
  uiEl.classList.remove("collapsed");
  restoreBtnEl.classList.remove("visible");
}
collapseBtnEl.onclick = collapsePanel;
restoreBtnEl.onclick = restorePanel;

/* ==========================================================================
   KEYBOARD
   ========================================================================== */

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
  if (e.key === "Shift" && !snapEnabled) {
    snapEnabled = true; updateSnapPill(); draw();
  }
  if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
    e.preventDefault(); doSave(); return;
  }
  if (e.key === "Enter") {
    if (drawing) { e.preventDefault(); finishDraw(); return; }
  }
  if (e.key === "Delete" || e.key === "Backspace") {
    if (drawing) return;
    if (state.selectedVertex) { e.preventDefault(); deleteSelectedVertex(); return; }
    if (state.selectedCable)  { e.preventDefault(); deleteSelectedCable();  return; }
  }
  if (e.key === "Escape") {
    if (drawing) { finishDraw(); return; }
    state.selectedCable = null; state.selectedVertex = null; draw();
  }
  /* 'h' toggles the panel — matching the /keyboard convention. */
  if (e.key === "h" || e.key === "H") {
    if (uiEl.classList.contains("collapsed")) restorePanel();
    else                                       collapsePanel();
  }
});
window.addEventListener("keyup", (e) => {
  if (e.key === "Shift" && snapEnabled) {
    snapEnabled = false; updateSnapPill(); draw();
  }
});
window.addEventListener("blur", () => {
  if (snapEnabled) { snapEnabled = false; updateSnapPill(); draw(); }
});

/* ==========================================================================
   BOOTSTRAP
   ========================================================================== */

document.getElementById("addNsGrid").onclick    = addFloorNsGrid;
document.getElementById("addEwGrid").onclick    = addFloorEwGrid;
document.getElementById("addWallV").onclick     = addWallVGrid;
document.getElementById("addWallH").onclick     = addWallHGrid;
document.getElementById("saveBtn").onclick      = doSave;
document.getElementById("delVertexBtn").onclick = deleteSelectedVertex;
document.getElementById("deleteBtn").onclick    = deleteSelectedCable;
drawBtnEl.onclick = () => { if (drawing) cancelDraw(false); else beginDraw(); };
document.getElementById("clearBtn").onclick = () => {
  if (!confirm("Clear all grids and cables?")) return;
  exitFocus();
  anchors.clear();
  state.floorGrids = []; state.wallGrids = [];
  state.floorCables = []; state.wallCables = [];
  state.trueCables = [];
  state.selectedCable = null; state.selectedVertex = null;
  drawing = null; drawingPreview = null;
  draw();
};

/* Build the junction graph once, before anything that reads it. */
buildJunctionGraph();

resize();
updateSnapPill();
updateDrawButton();
(async () => {
  const data = await loadFromServer();
  if (data) {
    applyLoaded(data);
    const n = state.floorGrids.length + state.wallGrids.length +
              state.floorCables.length + state.wallCables.length;
    if (n) flashStatus(`Loaded ${n} item(s) from ${STATE_FILE}`, "ok");
    draw();
  }
})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# 3. Server
# ---------------------------------------------------------------------------

def serve():
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=os.getcwd(), **kwargs)

        def do_POST(self):
            if self.path == "/save":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = self.rfile.read(length)
                    data = json.loads(body)
                    with open(STATE_FILE, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                    n = (len(data.get("floorGrids", [])) +
                         len(data.get("wallGrids", [])) +
                         len(data.get("floorCables", [])) +
                         len(data.get("wallCables", [])))
                    print(f"  saved {n} item(s) to {STATE_FILE}")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(b'{"ok":true}')
                except Exception as e:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(
                        json.dumps({"error": str(e)}).encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()

        def end_headers(self):
            if self.path.endswith(".json"):
                self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, *args):
            pass

    httpd = None
    used_port = None
    for p in range(PORT, PORT + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            used_port = p
            break
        except OSError:
            continue
    if httpd is None:
        print(f"Could not find a free port in [{PORT}, {PORT+20}).")
        return

    url = f"http://127.0.0.1:{used_port}/{HTML_FILE}"
    print(f"Serving {os.getcwd()}")
    print(f"  → open  {url}")
    print(f"  (state is saved to ./{STATE_FILE})")
    print("  Press Ctrl+C to stop.")

    if OPEN_BROWSER:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()


# ---------------------------------------------------------------------------
# 4. Main
# ---------------------------------------------------------------------------

def main():
    geom = extract_geometry()

    html = HTML_TEMPLATE.replace("__GEOMETRY_JSON__",
                                 json.dumps(geom, separators=(",", ":")))
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    b = geom["bounds"]
    uw = geom["unfoldedWall"]
    n_step = sum(1 for s in uw["segments"] if s.get("kind") == "step")
    n_col  = sum(1 for s in uw["segments"] if s.get("kind") == "column")
    print(f"Wrote {HTML_FILE}")
    print(f"  wall faces extracted  : {len(geom['faces'])}")
    print(f"  step areas extracted  : {len(geom['stepFaces'])}")
    print(f"  bounds (mm)           : "
          f"x [{b['minX']:.1f}, {b['maxX']:.1f}]  "
          f"y [{b['minY']:.1f}, {b['maxY']:.1f}]")
    print(f"  wall cut              : z = {geom['cutZ']:.0f} mm")
    print(f"  unfolded wall         : {len(uw['segments'])} segment(s) "
          f"({n_col} column, {n_step} step), "
          f"perimeter {uw['totalU']:.0f} mm, height {uw['height']:.0f} mm, "
          f"{'CCW' if uw['ccw'] else 'CW'}")
    print(f"  columns               : {len(geom.get('columns', []))}")
    print(f"  openings              : {len(geom.get('openings', []))}")
    print(f"  state file            : {STATE_FILE}")

    if SERVE:
        print()
        serve()
    else:
        print(f"  open                  : file://{os.path.abspath(HTML_FILE)}")


if __name__ == "__main__":
    main()
