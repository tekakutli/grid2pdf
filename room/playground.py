"""
playground.py — turn room.step into a self-contained 2D browser playground.

    1. run room.py       → produces room.step
    2. run playground.py → writes playground.html and (by default) starts a
                           tiny local server so the page can read/write
                           room_boxes.json and print lists to the terminal.
    3. open the URL that playground.py prints.

The playground sections room.step at z = 1200 mm.  At that height:
    • walls and columns are solid and appear in the cut
    • steps (280 mm tall) do NOT appear — they are valid floor
    • doors / windows are open — they are valid floor

Steps are visualized by sectioning at z = 100 mm and subtracting the
z = 1200 mm section.  Result: a dashed black area (monochrome), no
collision, no label.

PNG export
----------
    The "🖼 Print PNG" button renders the same drawing — walls, step
    areas, boxes with names and dimensions — into an offscreen canvas
    at the same scale the original room.py used:

        room.py SVG    : ExportSVG(scale=0.1, margin=20)
        room.py PNG    : cairosvg(scale=0.5)
        ⇒  0.1 × 0.5   = 0.05 px per model mm, 10 px margin

    We multiply that by PNG_RES (default 4) so text stays legible.
    Set PNG_RES = 1 in the JS below to match the original 1:1.

    Black-and-white print style.  Big boxes get their name in the
    centre and each dimension figure attached to the edge it measures.
    Tiny boxes get proper architectural dimension lines outside, one
    per edge, each with its own number on it — unambiguous at any
    rotation.

Persistence
-----------
    • Save button (or Ctrl/Cmd+S)
      → POST /save → overwrites room_boxes.json on disk.  Falls back to
        a browser download when the page was opened via file:// (no
        server).
    • Autoload
      → page loads → fetch room_boxes.json.  If the server doesn't have
        one (first run) we use localStorage.
    • Autosave: REMOVED.  Boxes are only persisted when the Save button
      is clicked — nothing is written to disk or to localStorage on
      drag / resize / rename / rotate / place / delete.

List boxes
----------
    The "📋 List boxes" button posts the current box list to /print on
    the local server, which prints a formatted table to the terminal
    that playground.py is running in:

        Name            Width       Height
        ---------------- ---------- ----------
        Box 2              1188.01    1215.24
        scroto             2000.00    1300.00
        arre                642.33    1500.00

    When there is no server (file:// mode) it falls back to printing
    the same table to the browser console (F12).

File format (room_boxes.json) — trivial to hand-edit
----------------------------------------------------
    {
      "version": 1,
      "boxes": [
        {"name": "Kitchen", "x": 500, "y": 1200,
         "w": 2400, "h": 1800, "rot": 0},
        ...
      ]
    }

    A bare array [ {...}, {...} ] is also accepted.

Invalid loaded boxes
--------------------
    A loaded box whose position collides with a wall or column is still
    loaded, but rendered faded / grey / dashed with a "⚠ invalid" tag.
    It is "dead" for the dynamics of valid boxes:
        • it does not make neighbouring valid boxes turn orange,
        • it does not block placement of new boxes,
        • it can be dragged / rotated / resized / edited freely (no wall
          gate) so you can fix it — as soon as it becomes valid it
          re-joins the normal dynamics.
"""

import json
import os
import threading
import webbrowser
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


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
    from build123d import import_step, Plane, section


STEP_FILE    = "room.step"
HTML_FILE    = "playground.html"
BOXES_FILE   = "room_boxes.json"
WALL_CUT_Z   = 1200.0    # = PLAN_CUT_Z (120 units) × UNIT_MM (10)
STEP_LOW_Z   = 100.0     # below step height (280 mm), above floor

SERVE        = True
PORT         = 8765
OPEN_BROWSER = True


# ---------------------------------------------------------------------------
# 1. Extract collision + step geometry from room.step
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

    return {
        "faces":     wall_polys,
        "stepFaces": step_polys,
        "bounds":    {"minX": min_x, "minY": min_y,
                      "maxX": max_x, "maxY": max_y},
        "cutZ":      WALL_CUT_Z,
    }


# ---------------------------------------------------------------------------
# 2. Self-contained HTML playground
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Floor plan playground</title>
<style>
  html, body { margin:0; padding:0; height:100%; overflow:hidden;
               font-family: -apple-system, system-ui, Segoe UI, sans-serif; }
  #ui {
    position:absolute; top:12px; left:12px; z-index:10;
    background:rgba(255,255,255,0.96); padding:12px 14px;
    border-radius:8px; box-shadow:0 2px 10px rgba(0,0,0,0.18);
    font-size:13px; user-select:none; width:340px;
  }
  #ui h3 { margin:0 0 8px; font-size:14px; font-weight:600; }
  #ui .row { margin:6px 0; display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  #ui label { display:inline-flex; align-items:center; gap:4px; }
  #ui input[type=number] { width:65px; padding:3px 5px; border:1px solid #bbb;
                           border-radius:4px; font-size:12px; }
  #ui input[type=text] { width:100%; padding:4px 6px; border:1px solid #bbb;
                         border-radius:4px; font-size:12px; box-sizing:border-box; }
  #ui input:disabled { background:#f3f3f3; color:#999; }
  #ui button { padding:5px 9px; cursor:pointer; border:1px solid #bbb;
               background:#f7f7f7; border-radius:4px; font-size:12px; }
  #ui button:hover { background:#eee; }
  #ui button.primary { background:#1d4ed8; color:#fff; border-color:#1d4ed8;
                       font-weight:600; }
  #ui button.primary:hover { background:#1e40af; }
  #ui hr { border:0; border-top:1px solid #eee; margin:9px 0; }
  #ui .selinfo { font-size:11px; color:#666; font-family: ui-monospace, Menlo, monospace; }
  #status { margin-top:8px; font-size:12px; min-height:16px; font-weight:600;
            font-family: ui-monospace, Menlo, Consolas, monospace; }
  #status.ok  { color:#15803d; }
  #status.bad { color:#dc2626; }
  #status.warn{ color:#ea580c; }
  #hint { margin-top:8px; color:#555; font-size:11px; line-height:1.55;
          max-width:340px; }
  canvas { display:block; touch-action:none; }
</style>
</head>
<body>
<div id="ui">
  <h3>Box placement playground</h3>
  <div class="row">
    <label>W <input type="number" id="boxW" value="2000" step="100"></label>
    <label>H <input type="number" id="boxH" value="1500" step="100"></label>
    <label>Rot <input type="number" id="boxRot" value="0" step="15">°</label>
  </div>
  <div class="row">
    <button id="rotL90">⟲ 90°</button>
    <button id="rotR90">⟳ 90°</button>
    <button id="rotL15">⟲ 15°</button>
    <button id="rotR15">⟳ 15°</button>
  </div>
  <div class="row">
    <button id="saveBtn" class="primary">💾 Save</button>
    <button id="listBtn">📋 List boxes</button>
    <button id="pngBtn">🖼 Print PNG</button>
    <button id="clearBtn">Clear all</button>
    <button id="undoBtn">Undo last</button>
  </div>
  <hr>
  <div class="row">
    <label style="flex:1;">Name:
      <input type="text" id="boxName" placeholder="(no box selected)">
    </label>
  </div>
  <div class="row">
    <label>W <input type="number" id="selW" step="10" min="10" disabled></label>
    <label>H <input type="number" id="selH" step="10" min="10" disabled></label>
    <label>Rot <input type="number" id="selRot" step="15" disabled>°</label>
  </div>
  <div class="row">
    <span class="selinfo" id="selInfo">no box selected</span>
  </div>
  <div id="status"></div>
  <div id="hint">
    <b>Left click</b> empty floor → place box of W×H, current rot.<br>
    <b>Left drag</b> a box body → move it (slides along walls).<br>
    <b>Corner handle</b> (circle) → drag to <b>rotate</b>.
      Hold <b>Shift</b> to snap 15°.<br>
    <b>Edge handle</b> (square) → drag to <b>resize</b> that side.<br>
    <b>Selected box fields</b> (Name / W / H / Rot) — edit and press
      Enter or click away to apply.<br>
    <b>Save</b> (Ctrl/Cmd+S) writes <code>room_boxes.json</code>.
      Nothing is saved automatically — click Save when you want to keep
      your work.<br>
    <b>List boxes</b> prints a table of every box (name, width, height)
      to the terminal running the server.<br>
    <b>Print PNG</b> saves the current walls + steps + boxes as a
      black-and-white <code>room_boxes.png</code>.<br>
    <b>Double-click</b> a box to edit its name.<br>
    <b>Right click</b> a box → delete. <b>Del</b> → delete selected.<br>
    <b>Scroll</b> zoom · <b>middle-drag</b> pan · <b>Esc</b> deselect.<br>
    The green/red <b>ghost</b> shows where a new box would go.<br>
    A <b>grey dashed</b> box is <b>invalid</b> (inside a wall) — it was
    loaded from disk and needs fixing.  Drag it out and it becomes a
    normal box again.<br>
    Orange boxes overlap each other (allowed, just flagged).<br>
    Light-grey <b>STEP</b> areas are valid floor.<br>
    All dimensions in <b>millimetres</b>.
  </div>
</div>
<canvas id="c"></canvas>
<script>
"use strict";
const GEOMETRY = __GEOMETRY_JSON__;
const BOXES_FILE = "room_boxes.json";

/* ================================================================== */
/*  Geometry helpers                                                  */
/* ================================================================== */

function pointInPolygon(p, poly) {
  const x = p[0], y = p[1];
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i][0], yi = poly[i][1];
    const xj = poly[j][0], yj = poly[j][1];
    if (((yi > y) !== (yj > y)) &&
        (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) {
      inside = !inside;
    }
  }
  return inside;
}

function segsIntersect(ax, ay, bx, by, cx, cy, dx, dy) {
  const d1x = bx - ax, d1y = by - ay;
  const d2x = dx - cx, d2y = dy - cy;
  const denom = d1x * d2y - d1y * d2x;
  if (Math.abs(denom) < 1e-12) return false;
  const t = ((cx - ax) * d2y - (cy - ay) * d2x) / denom;
  const s = ((cx - ax) * d1y - (cy - ay) * d1x) / denom;
  return t >= 0 && t <= 1 && s >= 0 && s <= 1;
}

function pointInFace(p, face) {
  if (!pointInPolygon(p, face.outer)) return false;
  for (const h of face.holes) if (pointInPolygon(p, h)) return false;
  return true;
}

function polygonOverlapsFace(poly, face) {
  for (const p of poly) if (pointInFace(p, face)) return true;
  const rings = [face.outer, ...face.holes];
  for (const ring of rings) {
    for (const p of ring) if (pointInPolygon(p, poly)) return true;
  }
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i], b = poly[(i + 1) % poly.length];
    for (const ring of rings) {
      for (let j = 0; j < ring.length; j++) {
        const c = ring[j], d = ring[(j + 1) % ring.length];
        if (segsIntersect(a[0], a[1], b[0], b[1],
                          c[0], c[1], d[0], d[1])) return true;
      }
    }
  }
  return false;
}

function polygonsOverlap(p1, p2) {
  for (const p of p1) if (pointInPolygon(p, p2)) return true;
  for (const p of p2) if (pointInPolygon(p, p1)) return true;
  for (let i = 0; i < p1.length; i++) {
    const a = p1[i], b = p1[(i + 1) % p1.length];
    for (let j = 0; j < p2.length; j++) {
      const c = p2[j], d = p2[(j + 1) % p2.length];
      if (segsIntersect(a[0], a[1], b[0], b[1],
                        c[0], c[1], d[0], d[1])) return true;
    }
  }
  return false;
}

function boxCorners(cx, cy, w, h, rotDeg) {
  const r = rotDeg * Math.PI / 180;
  const c = Math.cos(r), s = Math.sin(r);
  const hw = w / 2, hh = h / 2;
  const off = [[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]];
  return off.map(([dx, dy]) =>
    [cx + c * dx - s * dy, cy + s * dx + c * dy]);
}

function boxEdgeMidpoints(cx, cy, w, h, rotDeg) {
  const c = boxCorners(cx, cy, w, h, rotDeg);
  return [
    [(c[0][0] + c[1][0]) / 2, (c[0][1] + c[1][1]) / 2],
    [(c[1][0] + c[2][0]) / 2, (c[1][1] + c[2][1]) / 2],
    [(c[2][0] + c[3][0]) / 2, (c[2][1] + c[3][1]) / 2],
    [(c[3][0] + c[0][0]) / 2, (c[3][1] + c[0][1]) / 2],
  ];
}

function boxOverlapsWalls(cx, cy, w, h, rotDeg) {
  const corners = boxCorners(cx, cy, w, h, rotDeg);
  for (const face of GEOMETRY.faces) {
    if (polygonOverlapsFace(corners, face)) return true;
  }
  return false;
}

/* ================================================================== */
/*  Canvas / view state                                               */
/* ================================================================== */

const canvas = document.getElementById("c");
const ctx = canvas.getContext("2d");
let dpr = window.devicePixelRatio || 1;

let placedBoxes = [];
let selectedBox = null;
let hoveredBox  = null;
let drag        = null;
let handleDrag  = null;
let pendingPlace = null;
let panning     = null;
let boxCounter  = 0;

const mouse = { sx: 0, sy: 0, inside: false };
const view = { scale: 1, tx: 0, ty: 0 };

const CORNER_HIT = 11;
const EDGE_HIT   = 10;
const DRAG_CANCEL = 6;
const MIN_BOX_MM = 10;

function fitView() {
  const b = GEOMETRY.bounds;
  const cw = canvas.clientWidth, ch = canvas.clientHeight;
  const bw = (b.maxX - b.minX) || 1;
  const bh = (b.maxY - b.minY) || 1;
  const pad = 60;
  view.scale = Math.min((cw - 2 * pad) / bw, (ch - 2 * pad) / bh);
  const mx = (b.minX + b.maxX) / 2;
  const my = (b.minY + b.maxY) / 2;
  view.tx = cw / 2 - mx * view.scale;
  view.ty = ch / 2 + my * view.scale;
}

function resize() {
  dpr = window.devicePixelRatio || 1;
  canvas.width  = Math.round(window.innerWidth  * dpr);
  canvas.height = Math.round(window.innerHeight * dpr);
  canvas.style.width  = window.innerWidth  + "px";
  canvas.style.height = window.innerHeight + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  fitView();
  draw();
}
window.addEventListener("resize", resize);

function w2s(wx, wy) { return [wx * view.scale + view.tx, -wy * view.scale + view.ty]; }
function s2w(sx, sy) { return [(sx - view.tx) / view.scale, -(sy - view.ty) / view.scale]; }

/* ================================================================== */
/*  Persistence — explicit save only                                  */
/* ================================================================== */

function serializeBoxes() {
  return {
    version: 1,
    boxes: placedBoxes.map(b => ({
      name: b.name || "",
      x: Math.round(b.x * 100) / 100,
      y: Math.round(b.y * 100) / 100,
      w: Math.round(b.w * 100) / 100,
      h: Math.round(b.h * 100) / 100,
      rot: Math.round(b.rot * 100) / 100,
    })),
  };
}

function normalizeBoxes(data) {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.boxes)) return data.boxes;
  return [];
}

function applyLoadedBoxes(list) {
  placedBoxes = [];
  boxCounter = 0;
  for (const item of list) {
    if (!item || typeof item !== "object") continue;
    const w = +item.w, h = +item.h;
    if (!(w > 0) || !(h > 0)) continue;
    const b = {
      x: +item.x || 0,
      y: +item.y || 0,
      w, h,
      rot: ((+item.rot || 0) % 360 + 360) % 360,
      name: (typeof item.name === "string") ? item.name : "",
    };
    placedBoxes.push(b);
    const m = /^Box (\d+)$/.exec(b.name);
    if (m) boxCounter = Math.max(boxCounter, +m[1]);
  }
}

function saveToLocalStorage() {
  try {
    localStorage.setItem("room_boxes", JSON.stringify(serializeBoxes()));
  } catch (e) { /* quota / private mode — ignore */ }
}

function loadFromLocalStorage() {
  try {
    const s = localStorage.getItem("room_boxes");
    return s ? JSON.parse(s) : null;
  } catch (e) { return null; }
}

async function saveToServer() {
  try {
    const r = await fetch("/save", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(serializeBoxes()),
    });
    return r.ok;
  } catch (e) { return false; }
}

async function loadFromServer() {
  try {
    const r = await fetch(BOXES_FILE, {cache: "no-store"});
    if (!r.ok) return null;
    const txt = await r.text();
    if (!txt.trim()) return null;
    return JSON.parse(txt);
  } catch (e) { return null; }
}

function downloadBoxes() {
  const blob = new Blob(
    [JSON.stringify(serializeBoxes(), null, 2)],
    {type: "application/json"});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = BOXES_FILE;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* Note: boxes are only persisted when the user clicks Save (or
 * Ctrl/Cmd+S).  There is no autosave — edits to the canvas do not
 * write to disk, to localStorage, or to the server on their own. */
async function doSave(showFeedback) {
  saveToLocalStorage();
  const ok = await saveToServer();
  if (!ok) {
    downloadBoxes();
    if (showFeedback) flashStatus("✓ Downloaded " + BOXES_FILE, "ok");
  } else if (showFeedback) {
    flashStatus("✓ Saved " + BOXES_FILE, "ok");
  }
}

/* POST the current box list to /print on the local server, which
 * prints a formatted table to the terminal.  When the page is opened
 * via file:// (no server), fall back to printing the same table to
 * the browser console (F12). */
function consolePrintBoxList() {
  if (!placedBoxes.length) {
    console.log("(no boxes placed)");
    return;
  }
  const nameW = Math.max(4, ...placedBoxes.map(b => (b.name || "").length));
  const header = "Name".padEnd(nameW) + "  " +
                 "Width".padStart(10) + "  " +
                 "Height".padStart(10);
  console.log(header);
  console.log("-".repeat(header.length));
  for (const b of placedBoxes) {
    const name = (b.name || "").padEnd(nameW);
    const w = b.w.toFixed(2).padStart(10);
    const h = b.h.toFixed(2).padStart(10);
    console.log(`${name}  ${w}  ${h}`);
  }
}

async function printBoxList() {
  try {
    const r = await fetch("/print", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(serializeBoxes()),
    });
    if (r.ok) {
      flashStatus("✓ Printed to terminal", "ok");
      return;
    }
    consolePrintBoxList();
    flashStatus("✓ Printed to browser console (F12)", "ok");
  } catch (e) {
    consolePrintBoxList();
    flashStatus("✓ Printed to browser console (F12)", "ok");
  }
}

async function autoload() {
  let data = await loadFromServer();
  let source = data ? "file" : null;
  if (!data) {
    data = loadFromLocalStorage();
    source = data ? "localStorage" : null;
  }
  if (data) {
    applyLoadedBoxes(normalizeBoxes(data));
    const n = placedBoxes.length;
    const bad = placedBoxes.filter(
      b => boxOverlapsWalls(b.x, b.y, b.w, b.h, b.rot)).length;
    if (n) {
      let msg = `Loaded ${n} box${n === 1 ? "" : "es"} from ${source}`;
      if (bad) msg += ` — ${bad} invalid (grey dashed)`;
      flashStatus(msg, bad ? "warn" : "ok");
    }
  }
  draw();
}

/* ================================================================== */
/*  Drawing (interactive canvas)                                      */
/* ================================================================== */

function pathFace(face) {
  ctx.beginPath();
  const o = face.outer;
  if (!o.length) return;
  let [x0, y0] = w2s(o[0][0], o[0][1]);
  ctx.moveTo(x0, y0);
  for (let i = 1; i < o.length; i++) {
    const [x, y] = w2s(o[i][0], o[i][1]);
    ctx.lineTo(x, y);
  }
  ctx.closePath();
  for (const h of face.holes) {
    if (!h.length) continue;
    [x0, y0] = w2s(h[0][0], h[0][1]);
    ctx.moveTo(x0, y0);
    for (let i = 1; i < h.length; i++) {
      const [x, y] = w2s(h[i][0], h[i][1]);
      ctx.lineTo(x, y);
    }
    ctx.closePath();
  }
}

function pathBox(b) {
  const c = boxCorners(b.x, b.y, b.w, b.h, b.rot);
  ctx.beginPath();
  let [sx, sy] = w2s(c[0][0], c[0][1]);
  ctx.moveTo(sx, sy);
  for (let i = 1; i < 4; i++) {
    [sx, sy] = w2s(c[i][0], c[i][1]);
    ctx.lineTo(sx, sy);
  }
  ctx.closePath();
}

function drawBoxShape(b, stroke, fill, lw, dashed) {
  pathBox(b);
  if (fill) { ctx.fillStyle = fill; ctx.fill(); }
  if (stroke) {
    if (dashed) ctx.setLineDash([7, 5]);
    ctx.strokeStyle = stroke;
    ctx.lineWidth = lw || 2;
    ctx.stroke();
    if (dashed) ctx.setLineDash([]);
  }
}

function drawGhost(b, invalid) {
  const stroke = invalid ? "#dc2626" : "#16a34a";
  const fill   = invalid ? "rgba(220,38,38,0.22)" : "rgba(22,163,74,0.28)";

  pathBox(b);
  ctx.fillStyle = fill;
  ctx.fill();

  pathBox(b);
  ctx.strokeStyle = "rgba(255,255,255,0.9)";
  ctx.lineWidth = 6;
  ctx.stroke();

  pathBox(b);
  if (invalid) ctx.setLineDash([7, 5]);
  ctx.strokeStyle = stroke;
  ctx.lineWidth = 2.2;
  ctx.stroke();
  ctx.setLineDash([]);

  if (invalid) {
    const sc = boxCorners(b.x, b.y, b.w, b.h, b.rot).map(([x, y]) => w2s(x, y));
    const edgeW = Math.hypot(sc[1][0] - sc[0][0], sc[1][1] - sc[0][1]);
    const edgeH = Math.hypot(sc[3][0] - sc[0][0], sc[3][1] - sc[0][1]);
    if (Math.min(edgeW, edgeH) > 46) {
      const [cx, cy] = w2s(b.x, b.y);
      const label = "✗ collision";
      ctx.save();
      ctx.translate(cx, cy);
      ctx.font = "600 12px -apple-system, system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      const tw = ctx.measureText(label).width;
      ctx.fillStyle = "rgba(255,255,255,0.92)";
      ctx.fillRect(-tw / 2 - 6, -10, tw + 12, 20);
      ctx.strokeStyle = "rgba(220,38,38,0.35)";
      ctx.lineWidth = 1;
      ctx.strokeRect(-tw / 2 - 6, -10, tw + 12, 20);
      ctx.fillStyle = "#dc2626";
      ctx.fillText(label, 0, 0);
      ctx.restore();
    }
  }
}

function drawBoxLabel(b, faded) {
  if (!b.name) return;
  const c = boxCorners(b.x, b.y, b.w, b.h, b.rot);
  const sc = c.map(([x, y]) => w2s(x, y));
  const screenW = Math.abs(sc[1][0] - sc[0][0]) + Math.abs(sc[2][0] - sc[1][0]);
  const screenH = Math.abs(sc[3][1] - sc[0][1]) + Math.abs(sc[2][1] - sc[1][1]);
  const minDim = Math.min(screenW, screenH);
  const fontSize = Math.max(8, Math.min(16, minDim * 0.13));
  if (fontSize < 8) return;

  ctx.save();
  ctx.beginPath();
  ctx.moveTo(sc[0][0], sc[0][1]);
  for (let i = 1; i < 4; i++) ctx.lineTo(sc[i][0], sc[i][1]);
  ctx.closePath();
  ctx.clip();

  const [cxs, cys] = w2s(b.x, b.y);
  let tRot = b.rot;
  while (tRot >  90) tRot -= 180;
  while (tRot < -90) tRot += 180;

  ctx.translate(cxs, cys);
  ctx.rotate(-tRot * Math.PI / 180);
  ctx.fillStyle = faded ? "rgba(107,114,128,0.75)" : "#1e3a8a";
  ctx.font = `600 ${fontSize.toFixed(1)}px -apple-system, system-ui, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(b.name, 0, 0);
  ctx.restore();
}

function drawInvalidChip(b) {
  const sc = boxCorners(b.x, b.y, b.w, b.h, b.rot).map(([x, y]) => w2s(x, y));
  const minY = Math.min(sc[0][1], sc[1][1], sc[2][1], sc[3][1]);
  const cxs = (sc[0][0] + sc[1][0] + sc[2][0] + sc[3][0]) / 4;
  const label = "⚠ invalid";
  ctx.save();
  ctx.translate(cxs, minY - 11);
  ctx.font = "600 11px -apple-system, system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const tw = ctx.measureText(label).width;
  ctx.fillStyle = "rgba(254, 226, 226, 0.98)";
  ctx.fillRect(-tw / 2 - 6, -9, tw + 12, 18);
  ctx.strokeStyle = "#dc2626";
  ctx.lineWidth = 1;
  ctx.strokeRect(-tw / 2 - 6, -9, tw + 12, 18);
  ctx.fillStyle = "#dc2626";
  ctx.fillText(label, 0, 0);
  ctx.restore();
}

function drawHandles(b) {
  const corners = boxCorners(b.x, b.y, b.w, b.h, b.rot);
  const edges   = boxEdgeMidpoints(b.x, b.y, b.w, b.h, b.rot);

  for (let i = 0; i < 4; i++) {
    const [sx, sy] = w2s(edges[i][0], edges[i][1]);
    ctx.beginPath();
    ctx.rect(sx - 4.5, sy - 4.5, 9, 9);
    ctx.fillStyle = "#fff"; ctx.fill();
    ctx.strokeStyle = "#16a34a"; ctx.lineWidth = 1.6; ctx.stroke();
  }
  for (let i = 0; i < 4; i++) {
    const [sx, sy] = w2s(corners[i][0], corners[i][1]);
    ctx.beginPath();
    ctx.arc(sx, sy, 6.5, 0, Math.PI * 2);
    ctx.fillStyle = "#fff"; ctx.fill();
    ctx.strokeStyle = "#16a34a"; ctx.lineWidth = 2; ctx.stroke();
    ctx.beginPath();
    ctx.arc(sx, sy, 3, -Math.PI * 0.75, Math.PI * 0.25);
    ctx.strokeStyle = "#16a34a"; ctx.lineWidth = 1.2; ctx.stroke();
  }
}

function computeOverlapFlags(validFlags) {
  const n = placedBoxes.length;
  const flags = new Array(n).fill(false);
  const corners = placedBoxes.map(b =>
    boxCorners(b.x, b.y, b.w, b.h, b.rot));
  for (let i = 0; i < n; i++) {
    if (!validFlags[i]) continue;
    for (let j = i + 1; j < n; j++) {
      if (!validFlags[j]) continue;
      if (polygonsOverlap(corners[i], corners[j])) {
        flags[i] = true;
        flags[j] = true;
      }
    }
  }
  return flags;
}

function draw() {
  const cw = canvas.clientWidth, ch = canvas.clientHeight;
  ctx.fillStyle = "#fafafa";
  ctx.fillRect(0, 0, cw, ch);

  /* --- STEP areas (dashed light-blue, no label) ----------------------- */
  for (const face of GEOMETRY.stepFaces) {
    pathFace(face);
    ctx.fillStyle = "rgba(186, 214, 244, 0.55)";
    ctx.fill("evenodd");
    ctx.strokeStyle = "rgba(80, 130, 200, 0.75)";
    ctx.lineWidth = 1;
    ctx.setLineDash([7, 5]);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  /* --- walls / columns ------------------------------------------------ */
  for (const face of GEOMETRY.faces) {
    pathFace(face);
    ctx.fillStyle = "#2b2b2b";
    ctx.fill("evenodd");
    ctx.strokeStyle = "#000";
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  /* --- placed boxes --------------------------------------------------- */
  const validFlags = placedBoxes.map(
    b => !boxOverlapsWalls(b.x, b.y, b.w, b.h, b.rot));
  const overlapFlags = computeOverlapFlags(validFlags);

  for (let i = 0; i < placedBoxes.length; i++) {
    const b = placedBoxes[i];
    const isSel   = (b === selectedBox);
    const isHover = (b === hoveredBox);
    const valid = validFlags[i];
    const ov    = overlapFlags[i];

    let stroke, fill, lw, dashed = false;

    if (!valid) {
      stroke = "#6b7280";
      fill   = "rgba(156, 163, 175, 0.14)";
      dashed = true;
    } else if (ov) {
      stroke = "#ea580c";
      fill   = "rgba(251, 146, 60, 0.40)";
    } else {
      stroke = "#1d4ed8";
      fill   = "rgba(59, 130, 246, 0.35)";
    }

    if (isSel) {
      if (valid && ov)      { stroke = "#16a34a"; fill = "rgba(251,146,60,0.45)"; }
      else if (valid)       { stroke = "#16a34a"; fill = "rgba(59,130,246,0.45)"; }
      else                  { stroke = "#4b5563"; fill = "rgba(156,163,175,0.22)"; }
    }
    lw = isSel ? 3 : (isHover ? 2.6 : 1.8);

    drawBoxShape(b, stroke, fill, lw, dashed);
  }

  /* --- labels --------------------------------------------------------- */
  for (let i = 0; i < placedBoxes.length; i++) {
    drawBoxLabel(placedBoxes[i], !validFlags[i]);
  }

  /* --- invalid chip --------------------------------------------------- */
  for (let i = 0; i < placedBoxes.length; i++) {
    if (!validFlags[i]) drawInvalidChip(placedBoxes[i]);
  }

  /* --- handles for the selected box ----------------------------------- */
  if (selectedBox) drawHandles(selectedBox);

  /* --- ghost of the pending placement --------------------------------- */
  const overHandle = !!hitTestHandle(mouse.sx, mouse.sy);
  const canGhost =
    mouse.inside && !panning && !drag && !handleDrag &&
    !hoveredBox && !overHandle;

  if (canGhost) {
    const [wx, wy] = s2w(mouse.sx, mouse.sy);
    const { w, h, rot } = getBoxParams();
    const invalid = boxOverlapsWalls(wx, wy, w, h, rot);
    drawGhost({ x: wx, y: wy, w, h, rot }, invalid);
  }

  updateStatus(validFlags);
}

/* ================================================================== */
/*  PNG export — black-and-white print style                          */
/* ================================================================== */

function renderFloorPlanPNG() {
  const PNG_RES   = 4;
  const PX_PER_MM = 0.05 * PNG_RES;
  const MARGIN_PX = 10   * PNG_RES;

  const PEN_PLAN = 0.7 * 0.5 * PNG_RES;
  const PEN_STEP = 0.5 * 0.5 * PNG_RES;
  const PEN_BOX  = 1.8 * 0.5 * PNG_RES;

  const MIN_FONT = 4 * PNG_RES;

  const b = GEOMETRY.bounds;
  const mmW = b.maxX - b.minX;
  const mmH = b.maxY - b.minY;

  const W = Math.max(1, Math.round(mmW * PX_PER_MM + 2 * MARGIN_PX));
  const H = Math.max(1, Math.round(mmH * PX_PER_MM + 2 * MARGIN_PX));

  const cv = document.createElement("canvas");
  cv.width = W;
  cv.height = H;
  const g = cv.getContext("2d");

  g.fillStyle = "#ffffff";
  g.fillRect(0, 0, W, H);

  const P = (x, y) => [
    (x - b.minX) * PX_PER_MM + MARGIN_PX,
    (b.maxY - y) * PX_PER_MM + MARGIN_PX,
  ];

  const traceFace = (face) => {
    g.beginPath();
    const o = face.outer;
    if (!o.length) return;
    let [px, py] = P(o[0][0], o[0][1]);
    g.moveTo(px, py);
    for (let i = 1; i < o.length; i++) {
      [px, py] = P(o[i][0], o[i][1]);
      g.lineTo(px, py);
    }
    g.closePath();
    for (const h of face.holes) {
      if (!h.length) continue;
      [px, py] = P(h[0][0], h[0][1]);
      g.moveTo(px, py);
      for (let i = 1; i < h.length; i++) {
        [px, py] = P(h[i][0], h[i][1]);
        g.lineTo(px, py);
      }
      g.closePath();
    }
  };

  for (const face of GEOMETRY.stepFaces) {
    traceFace(face);
    g.fillStyle = "rgba(0, 0, 0, 0.05)";
    g.fill("evenodd");
    g.strokeStyle = "rgba(0, 0, 0, 0.85)";
    g.lineWidth = PEN_STEP;
    g.setLineDash([7 * PNG_RES, 5 * PNG_RES]);
    g.stroke();
    g.setLineDash([]);
  }

  for (const face of GEOMETRY.faces) {
    traceFace(face);
    g.fillStyle = "#000000";
    g.fill("evenodd");
    g.strokeStyle = "#000000";
    g.lineWidth = PEN_PLAN;
    g.stroke();
  }

  const validFlags = placedBoxes.map(
    bx => !boxOverlapsWalls(bx.x, bx.y, bx.w, bx.h, bx.rot));
  const overlapFlags = computeOverlapFlags(validFlags);

  for (let i = 0; i < placedBoxes.length; i++) {
    const bx = placedBoxes[i];
    const valid = validFlags[i];
    const ov    = overlapFlags[i];

    let stroke, fill, dashed = false;
    if (!valid) {
      stroke = "rgba(0, 0, 0, 0.45)";
      fill   = "rgba(0, 0, 0, 0.03)";
      dashed = true;
    } else if (ov) {
      stroke = "#000000";
      fill   = "rgba(0, 0, 0, 0.16)";
    } else {
      stroke = "#000000";
      fill   = "#ffffff";
    }

    const sc = boxCorners(bx.x, bx.y, bx.w, bx.h, bx.rot)
                 .map(([x, y]) => P(x, y));

    g.beginPath();
    g.moveTo(sc[0][0], sc[0][1]);
    for (let k = 1; k < 4; k++) g.lineTo(sc[k][0], sc[k][1]);
    g.closePath();
    g.fillStyle = fill;
    g.fill();
    if (dashed) g.setLineDash([7 * PNG_RES, 5 * PNG_RES]);
    g.strokeStyle = stroke;
    g.lineWidth = PEN_BOX;
    g.stroke();
    g.setLineDash([]);
  }

  /* --- two-pass labels --- */
  const placedRects = [];
  const numberRects = [];
  const boxRects    = [];

  const wallFacesProj = GEOMETRY.faces.map(face => ({
    outer: face.outer.map(([x, y]) => P(x, y)),
    holes: face.holes.map(h => h.map(([x, y]) => P(x, y))),
  }));
  const boxPolysProj = placedBoxes.map(bx =>
    boxCorners(bx.x, bx.y, bx.w, bx.h, bx.rot).map(([x, y]) => P(x, y)));

  for (let i = 0; i < placedBoxes.length; i++) {
    const sc = boxPolysProj[i];
    boxRects.push({
      x0: Math.min(sc[0][0], sc[1][0], sc[2][0], sc[3][0]),
      y0: Math.min(sc[0][1], sc[1][1], sc[2][1], sc[3][1]),
      x1: Math.max(sc[0][0], sc[1][0], sc[2][0], sc[3][0]),
      y1: Math.max(sc[0][1], sc[1][1], sc[2][1], sc[3][1]),
    });
  }

  const rectsOverlap = (a, b) =>
    !(a.x1 < b.x0 || a.x0 > b.x1 || a.y1 < b.y0 || a.y0 > b.y1);

  const rectToPoly = (r) =>
    [[r.x0, r.y0], [r.x1, r.y0], [r.x1, r.y1], [r.x0, r.y1]];

  function countHits(r, skipBoxIdx) {
    let n = 0;
    for (const q of placedRects) if (rectsOverlap(r, q)) n += 1;
    for (const q of numberRects) if (rectsOverlap(r, q)) n += 1;
    for (let k = 0; k < boxRects.length; k++) {
      if (k === skipBoxIdx) continue;
      if (rectsOverlap(r, boxRects[k])) n += 1;
    }
    const p = rectToPoly(r);
    for (const face of wallFacesProj) {
      if (polygonOverlapsFace(p, face)) n += 1;
    }
    return n;
  }

  const rectInsidePoly = (r, poly) => {
    const cs = [[r.x0, r.y0], [r.x1, r.y0], [r.x1, r.y1], [r.x0, r.y1]];
    for (const c of cs) if (!pointInPolygon(c, poly)) return false;
    return true;
  };

  function drawDimLine(A, B, text, fs, color, lw) {
    const dx = B[0] - A[0], dy = B[1] - A[1];
    const len = Math.hypot(dx, dy);
    if (len < 1) return;
    const ux = dx / len, uy = dy / len;
    const px = -uy, py = ux;

    g.strokeStyle = color;
    g.lineWidth = lw;
    g.beginPath();
    g.moveTo(A[0], A[1]);
    g.lineTo(B[0], B[1]);
    g.stroke();

    const ah  = Math.max(2.5, fs * 0.7);
    const ahw = Math.max(1.5, fs * 0.35);

    g.fillStyle = color;
    g.beginPath();
    g.moveTo(A[0], A[1]);
    g.lineTo(A[0] + ux * ah + px * ahw, A[1] + uy * ah + py * ahw);
    g.lineTo(A[0] + ux * ah - px * ahw, A[1] + uy * ah - py * ahw);
    g.closePath();
    g.fill();

    g.beginPath();
    g.moveTo(B[0], B[1]);
    g.lineTo(B[0] - ux * ah + px * ahw, B[1] - uy * ah + py * ahw);
    g.lineTo(B[0] - ux * ah - px * ahw, B[1] - uy * ah - py * ahw);
    g.closePath();
    g.fill();

    const mid = [(A[0] + B[0]) / 2, (A[1] + B[1]) / 2];
    let rot = Math.atan2(dy, dx);
    if (rot >  Math.PI / 2) rot -= Math.PI;
    if (rot < -Math.PI / 2) rot += Math.PI;

    g.font = `500 ${fs.toFixed(1)}px -apple-system, system-ui, sans-serif`;
    const textW = g.measureText(text).width;

    g.save();
    g.translate(mid[0], mid[1]);
    g.rotate(rot);
    g.fillStyle = "#ffffff";
    g.fillRect(-textW / 2 - fs * 0.2, -fs * 0.62,
               textW + fs * 0.4, fs * 1.24);
    g.fillStyle = color;
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(text, 0, 0);
    g.restore();
  }

  /* ---------- pass 1: decide inside vs outside ---------- */
  const insideFlags = new Array(placedBoxes.length).fill(false);
  const insideFS    = new Array(placedBoxes.length).fill(0);

  for (let i = 0; i < placedBoxes.length; i++) {
    const bx = placedBoxes[i];
    const hasName = !!(bx.name && bx.name.length);
    const nameStr = bx.name || "";
    const wStr = String(Math.round(bx.w));
    const hStr = String(Math.round(bx.h));
    const sc = boxPolysProj[i];

    const edgeW = Math.hypot(sc[1][0] - sc[0][0], sc[1][1] - sc[0][1]);
    const edgeH = Math.hypot(sc[2][0] - sc[1][0], sc[2][1] - sc[1][1]);
    const minDim = Math.min(edgeW, edgeH);
    const maxDim = Math.max(edgeW, edgeH);

    let fs = Math.min(14 * PNG_RES, minDim * 0.16, maxDim * 0.12);
    if (fs < MIN_FONT) continue;
    for (let tries = 0; tries < 15; tries++) {
      const dimFS = fs * 0.82;
      g.font = `600 ${fs}px -apple-system, system-ui, sans-serif`;
      const nameW = hasName ? g.measureText(nameStr).width : 0;
      g.font = `500 ${dimFS}px -apple-system, system-ui, sans-serif`;
      const wW = g.measureText(wStr).width;
      const hW = g.measureText(hStr).width;
      const margin = 2.2 * dimFS;
      const ok =
        (nameW + margin <= edgeW) &&
        (fs + margin <= edgeH) &&
        (wW <= edgeW * 0.85) &&
        (hW <= edgeH * 0.85);
      if (ok) { insideFlags[i] = true; insideFS[i] = fs; break; }
      fs *= 0.9;
      if (fs < MIN_FONT) break;
    }
  }

  /* ---------- pass 2: draw inside labels ---------- */
  for (let i = 0; i < placedBoxes.length; i++) {
    if (!insideFlags[i]) continue;
    const bx = placedBoxes[i];
    const valid = validFlags[i];
    const hasName = !!(bx.name && bx.name.length);
    const nameStr = bx.name || "";
    const wStr = String(Math.round(bx.w));
    const hStr = String(Math.round(bx.h));
    const sc = boxPolysProj[i];
    const fs = insideFS[i];
    const dimFS = fs * 0.82;

    const nameColor = valid ? "#000000" : "rgba(0, 0, 0, 0.45)";
    const dimColor  = valid ? "rgba(0, 0, 0, 0.85)" : "rgba(0, 0, 0, 0.40)";

    g.save();
    g.beginPath();
    g.moveTo(sc[0][0], sc[0][1]);
    for (let k = 1; k < 4; k++) g.lineTo(sc[k][0], sc[k][1]);
    g.closePath();
    g.clip();

    if (hasName) {
      const [cxs, cys] = P(bx.x, bx.y);
      let tRot = bx.rot;
      while (tRot >  90) tRot -= 180;
      while (tRot < -90) tRot += 180;
      g.save();
      g.translate(cxs, cys);
      g.rotate(-tRot * Math.PI / 180);
      g.fillStyle = nameColor;
      g.font = `600 ${fs.toFixed(1)}px -apple-system, system-ui, sans-serif`;
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillText(nameStr, 0, 0);
      g.restore();
    }

    {
      const mx = (sc[0][0] + sc[1][0]) / 2;
      const my = (sc[0][1] + sc[1][1]) / 2;
      const ox = (sc[2][0] + sc[3][0]) / 2 - mx;
      const oy = (sc[2][1] + sc[3][1]) / 2 - my;
      const ol = Math.hypot(ox, oy) || 1;
      const off = dimFS * 0.95;
      const px = mx + ox / ol * off;
      const py = my + oy / ol * off;
      let ang = Math.atan2(sc[1][1] - sc[0][1], sc[1][0] - sc[0][0]);
      if (ang >  Math.PI / 2) ang -= Math.PI;
      if (ang < -Math.PI / 2) ang += Math.PI;
      g.save();
      g.translate(px, py);
      g.rotate(ang);
      g.fillStyle = dimColor;
      g.font = `500 ${dimFS.toFixed(1)}px -apple-system, system-ui, sans-serif`;
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillText(wStr, 0, 0);
      g.restore();
    }

    {
      const mx = (sc[1][0] + sc[2][0]) / 2;
      const my = (sc[1][1] + sc[2][1]) / 2;
      const ox = (sc[3][0] + sc[0][0]) / 2 - mx;
      const oy = (sc[3][1] + sc[0][1]) / 2 - my;
      const ol = Math.hypot(ox, oy) || 1;
      const off = dimFS * 0.95;
      const px = mx + ox / ol * off;
      const py = my + oy / ol * off;
      let ang = Math.atan2(sc[2][1] - sc[1][1], sc[2][0] - sc[1][0]);
      if (ang >  Math.PI / 2) ang -= Math.PI;
      if (ang < -Math.PI / 2) ang += Math.PI;
      g.save();
      g.translate(px, py);
      g.rotate(ang);
      g.fillStyle = dimColor;
      g.font = `500 ${dimFS.toFixed(1)}px -apple-system, system-ui, sans-serif`;
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillText(hStr, 0, 0);
      g.restore();
    }

    g.restore();
  }

  /* ---------- pass 3: outside boxes get architectural dim lines ---------- */
  for (let i = 0; i < placedBoxes.length; i++) {
    if (insideFlags[i]) continue;
    const bx = placedBoxes[i];
    const valid = validFlags[i];
    const hasName = !!(bx.name && bx.name.length);
    const nameStr = bx.name || "";
    const wStr = String(Math.round(bx.w));
    const hStr = String(Math.round(bx.h));
    const sc = boxPolysProj[i];

    const outFS = 5 * PNG_RES;
    const numFS = outFS * 0.9;

    const BL = sc[0], BR = sc[1], TR = sc[2];
    const cx0 = (sc[0][0] + sc[1][0] + sc[2][0] + sc[3][0]) / 4;
    const cy0 = (sc[0][1] + sc[1][1] + sc[2][1] + sc[3][1]) / 4;

    const wdx = BR[0] - BL[0], wdy = BR[1] - BL[1];
    const wlen = Math.hypot(wdx, wdy) || 1;
    let wnx = -wdy / wlen, wny = wdx / wlen;
    const wmx = (BL[0] + BR[0]) / 2, wmy = (BL[1] + BR[1]) / 2;
    if ((cx0 - wmx) * wnx + (cy0 - wmy) * wny > 0) { wnx = -wnx; wny = -wny; }

    const hdx = TR[0] - BR[0], hdy = TR[1] - BR[1];
    const hlen = Math.hypot(hdx, hdy) || 1;
    let hnx = -hdy / hlen, hny = hdx / hlen;
    const hmx = (BR[0] + TR[0]) / 2, hmy = (BR[1] + TR[1]) / 2;
    if ((cx0 - hmx) * hnx + (cy0 - hmy) * hny > 0) { hnx = -hnx; hny = -hny; }

    const off = numFS * 1.8;

    const wA = [BL[0] + wnx * off, BL[1] + wny * off];
    const wB = [BR[0] + wnx * off, BR[1] + wny * off];
    const hA = [BR[0] + hnx * off, BR[1] + hny * off];
    const hB = [TR[0] + hnx * off, TR[1] + hny * off];

    const strokeColor = valid ? "#000000" : "rgba(0, 0, 0, 0.40)";
    const extLW = Math.max(1, 0.35 * PNG_RES);
    const dimLW = Math.max(1, 0.6 * PNG_RES);

    g.strokeStyle = strokeColor;
    g.lineWidth = extLW;
    g.beginPath();
    g.moveTo(BL[0], BL[1]); g.lineTo(wA[0], wA[1]);
    g.moveTo(BR[0], BR[1]); g.lineTo(wB[0], wB[1]);
    g.moveTo(BR[0], BR[1]); g.lineTo(hA[0], hA[1]);
    g.moveTo(TR[0], TR[1]); g.lineTo(hB[0], hB[1]);
    g.stroke();

    drawDimLine(wA, wB, wStr, numFS, strokeColor, dimLW);
    drawDimLine(hA, hB, hStr, numFS, strokeColor, dimLW);

    const wMid = [(wA[0] + wB[0]) / 2, (wA[1] + wB[1]) / 2];
    const hMid = [(hA[0] + hB[0]) / 2, (hA[1] + hB[1]) / 2];
    const nPad = numFS * 1.2;
    numberRects.push({
      x0: wMid[0] - nPad, y0: wMid[1] - nPad,
      x1: wMid[0] + nPad, y1: wMid[1] + nPad,
    });
    numberRects.push({
      x0: hMid[0] - nPad, y0: hMid[1] - nPad,
      x1: hMid[0] + nPad, y1: hMid[1] + nPad,
    });

    if (!hasName) continue;

    g.font = `600 ${outFS.toFixed(1)}px -apple-system, system-ui, sans-serif`;
    const nameW = g.measureText(nameStr).width;
    const nameH = outFS * 1.15;

    const minX = Math.min(sc[0][0], sc[1][0], sc[2][0], sc[3][0]);
    const maxX = Math.max(sc[0][0], sc[1][0], sc[2][0], sc[3][0]);
    const minY = Math.min(sc[0][1], sc[1][1], sc[2][1], sc[3][1]);
    const maxY = Math.max(sc[0][1], sc[1][1], sc[2][1], sc[3][1]);
    const ncx0 = (minX + maxX) / 2;
    const ncy0 = (minY + maxY) / 2;
    const gap = outFS * 0.8;

    let chosenName = null;

    const insideCand = {
      x0: ncx0 - nameW / 2, y0: ncy0 - nameH / 2,
      x1: ncx0 + nameW / 2, y1: ncy0 + nameH / 2,
    };
    if (rectInsidePoly(insideCand, boxPolysProj[i]) &&
        countHits(insideCand, i) === 0) {
      chosenName = insideCand;
    } else {
      const cands = [
        { x0: ncx0 - nameW / 2, y0: minY - gap - nameH,
          x1: ncx0 + nameW / 2, y1: minY - gap },
        { x0: ncx0 - nameW / 2, y0: maxY + gap,
          x1: ncx0 + nameW / 2, y1: maxY + gap + nameH },
        { x0: maxX + gap, y0: ncy0 - nameH / 2,
          x1: maxX + gap + nameW, y1: ncy0 + nameH / 2 },
        { x0: minX - gap - nameW, y0: ncy0 - nameH / 2,
          x1: minX - gap, y1: ncy0 + nameH / 2 },
        { x0: ncx0 - nameW / 2, y0: minY - 3 * gap - nameH,
          x1: ncx0 + nameW / 2, y1: minY - 3 * gap },
        { x0: ncx0 - nameW / 2, y0: maxY + 3 * gap,
          x1: ncx0 + nameW / 2, y1: maxY + 3 * gap + nameH },
        { x0: maxX + 3 * gap, y0: ncy0 - nameH / 2,
          x1: maxX + 3 * gap + nameW, y1: ncy0 + nameH / 2 },
        { x0: minX - 3 * gap - nameW, y0: ncy0 - nameH / 2,
          x1: minX - 3 * gap, y1: ncy0 + nameH / 2 },
      ];

      let fallbackName = null, bestHits = Infinity;
      for (const c of cands) {
        const ccx = (c.x0 + c.x1) / 2, ccy = (c.y0 + c.y1) / 2;
        if (ccx < 0 || ccx > W || ccy < 0 || ccy > H) continue;
        const hits = countHits(c, i);
        if (hits === 0) { chosenName = c; break; }
        if (hits < bestHits) { bestHits = hits; fallbackName = c; }
      }
      if (!chosenName) chosenName = fallbackName || cands[0];
    }

    placedRects.push(chosenName);

    const ncxFin = (chosenName.x0 + chosenName.x1) / 2;
    const ncyFin = (chosenName.y0 + chosenName.y1) / 2;

    g.fillStyle = valid ? "#000000" : "rgba(0, 0, 0, 0.40)";
    g.font = `600 ${outFS.toFixed(1)}px -apple-system, system-ui, sans-serif`;
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(nameStr, ncxFin, ncyFin);
  }

  cv.toBlob((blob) => {
    if (!blob) { flashStatus("✗ PNG export failed", "bad"); return; }
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "room_boxes.png";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    flashStatus(`✓ PNG written (${W}×${H})`, "ok");
  }, "image/png");
}

/* ================================================================== */
/*  Movement with wall-slide + swept path                             */
/* ================================================================== */

function moveBoxSafely(b, targetX, targetY) {
  if (boxOverlapsWalls(b.x, b.y, b.w, b.h, b.rot)) {
    b.x = targetX;
    b.y = targetY;
    return;
  }

  const totalDx = targetX - b.x, totalDy = targetY - b.y;
  const dist = Math.hypot(totalDx, totalDy);
  if (dist < 1e-6) return;
  const steps = Math.max(1, Math.ceil(dist / 50));
  const stepDx = totalDx / steps, stepDy = totalDy / steps;
  for (let i = 0; i < steps; i++) {
    const tx = b.x + stepDx;
    const ty = b.y + stepDy;
    if      (!boxOverlapsWalls(tx, ty, b.w, b.h, b.rot)) { b.x = tx; b.y = ty; }
    else if (!boxOverlapsWalls(tx, b.y, b.w, b.h, b.rot)) { b.x = tx; }
    else if (!boxOverlapsWalls(b.x, ty, b.w, b.h, b.rot)) { b.y = ty; }
    else break;
  }
}

/* ================================================================== */
/*  Hit tests                                                         */
/* ================================================================== */

function hitTestBox(wx, wy) {
  for (let i = placedBoxes.length - 1; i >= 0; i--) {
    const b = placedBoxes[i];
    if (pointInPolygon([wx, wy], boxCorners(b.x, b.y, b.w, b.h, b.rot))) {
      return i;
    }
  }
  return -1;
}

function hitTestHandle(sx, sy) {
  if (!selectedBox) return null;
  const b = selectedBox;
  const corners = boxCorners(b.x, b.y, b.w, b.h, b.rot);
  const edges   = boxEdgeMidpoints(b.x, b.y, b.w, b.h, b.rot);

  for (let i = 0; i < 4; i++) {
    const [px, py] = w2s(corners[i][0], corners[i][1]);
    if (Math.abs(sx - px) <= CORNER_HIT && Math.abs(sy - py) <= CORNER_HIT) {
      return { type: "corner", index: i };
    }
  }
  for (let i = 0; i < 4; i++) {
    const [px, py] = w2s(edges[i][0], edges[i][1]);
    if (Math.abs(sx - px) <= EDGE_HIT && Math.abs(sy - py) <= EDGE_HIT) {
      return { type: "edge", index: i };
    }
  }
  return null;
}

/* ================================================================== */
/*  Handle drag implementations                                       */
/* ================================================================== */

function startRotate(b, wx, wy) {
  handleDrag = {
    type: "rotate",
    b,
    orig: { x: b.x, y: b.y, w: b.w, h: b.h, rot: b.rot },
    startAngle: Math.atan2(wy - b.y, wx - b.x),
  };
}

function updateRotate(wx, wy, shift) {
  const hd = handleDrag, b = hd.b;
  const phi = Math.atan2(wy - b.y, wx - b.x);
  let delta = (phi - hd.startAngle) * 180 / Math.PI;
  let newRot = hd.orig.rot + delta;
  if (shift) newRot = Math.round(newRot / 15) * 15;
  newRot = ((newRot % 360) + 360) % 360;

  const curInvalid = boxOverlapsWalls(b.x, b.y, b.w, b.h, b.rot);
  if (curInvalid || !boxOverlapsWalls(b.x, b.y, b.w, b.h, newRot)) {
    b.rot = newRot;
    return true;
  }
  return false;
}

function startResize(b, edgeIndex) {
  handleDrag = {
    type: "resize",
    edge: edgeIndex,
    b,
    orig: { x: b.x, y: b.y, w: b.w, h: b.h, rot: b.rot },
  };
}

function updateResize(wx, wy) {
  const hd = handleDrag, b = hd.b, o = hd.orig;
  const r = o.rot * Math.PI / 180;
  const ux = Math.cos(r),  uy = Math.sin(r);
  const vx = -Math.sin(r), vy = Math.cos(r);

  let newW = o.w, newH = o.h, newCx = o.x, newCy = o.y;

  if (hd.edge === 1) {
    const fx = o.x - ux * o.w / 2, fy = o.y - uy * o.w / 2;
    const t  = (wx - fx) * ux + (wy - fy) * uy;
    newW = Math.max(MIN_BOX_MM, t);
    newCx = fx + ux * newW / 2; newCy = fy + uy * newW / 2;
  } else if (hd.edge === 3) {
    const fx = o.x + ux * o.w / 2, fy = o.y + uy * o.w / 2;
    const t  = (wx - fx) * ux + (wy - fy) * uy;
    newW = Math.max(MIN_BOX_MM, -t);
    newCx = fx - ux * newW / 2; newCy = fy - uy * newW / 2;
  } else if (hd.edge === 2) {
    const fx = o.x - vx * o.h / 2, fy = o.y - vy * o.h / 2;
    const t  = (wx - fx) * vx + (wy - fy) * vy;
    newH = Math.max(MIN_BOX_MM, t);
    newCx = fx + vx * newH / 2; newCy = fy + vy * newH / 2;
  } else if (hd.edge === 0) {
    const fx = o.x + vx * o.h / 2, fy = o.y + vy * o.h / 2;
    const t  = (wx - fx) * vx + (wy - fy) * vy;
    newH = Math.max(MIN_BOX_MM, -t);
    newCx = fx - vx * newH / 2; newCy = fy - vy * newH / 2;
  }

  const curInvalid = boxOverlapsWalls(b.x, b.y, b.w, b.h, b.rot);
  if (curInvalid ||
      !boxOverlapsWalls(newCx, newCy, newW, newH, o.rot)) {
    b.x = newCx; b.y = newCy; b.w = newW; b.h = newH;
    return true;
  }
  return false;
}

/* ================================================================== */
/*  Selection                                                         */
/* ================================================================== */

const nameInput   = document.getElementById("boxName");
const selInfo     = document.getElementById("selInfo");
const selWInput   = document.getElementById("selW");
const selHInput   = document.getElementById("selH");
const selRotInput = document.getElementById("selRot");

function syncSelectionInputs() {
  if (selectedBox) {
    const b = selectedBox;
    nameInput.disabled = false;
    nameInput.value = b.name || "";

    selWInput.disabled = false;
    selHInput.disabled = false;
    selRotInput.disabled = false;
    selWInput.value   = Math.round(b.w);
    selHInput.value   = Math.round(b.h);
    selRotInput.value = Math.round(b.rot * 100) / 100;

    selInfo.textContent =
      `x=${b.x.toFixed(0)} y=${b.y.toFixed(0)}`;
  } else {
    nameInput.disabled = true;
    nameInput.value = "";
    selWInput.disabled = true;
    selHInput.disabled = true;
    selRotInput.disabled = true;
    selWInput.value = "";
    selHInput.value = "";
    selRotInput.value = "";
    selInfo.textContent = "no box selected";
  }
}

function setSelected(b) {
  selectedBox = b;
  syncSelectionInputs();
  draw();
}

function tryApplyBoxChange(b, changes) {
  const test = { x: b.x, y: b.y, w: b.w, h: b.h, rot: b.rot };
  Object.assign(test, changes);
  const curInvalid = boxOverlapsWalls(b.x, b.y, b.w, b.h, b.rot);
  if (!curInvalid &&
      boxOverlapsWalls(test.x, test.y, test.w, test.h, test.rot)) {
    flashStatus("✗ Change blocked by wall", "bad");
    return false;
  }
  Object.assign(b, changes);
  return true;
}

nameInput.addEventListener("input", () => {
  if (selectedBox) {
    selectedBox.name = nameInput.value;
    draw();
  }
});

selWInput.addEventListener("change", () => {
  if (!selectedBox) return;
  const v = parseFloat(selWInput.value);
  if (!(v > 0)) { syncSelectionInputs(); return; }
  const newW = Math.max(MIN_BOX_MM, v);
  tryApplyBoxChange(selectedBox, { w: newW });
  syncSelectionInputs();
  draw();
});

selHInput.addEventListener("change", () => {
  if (!selectedBox) return;
  const v = parseFloat(selHInput.value);
  if (!(v > 0)) { syncSelectionInputs(); return; }
  const newH = Math.max(MIN_BOX_MM, v);
  tryApplyBoxChange(selectedBox, { h: newH });
  syncSelectionInputs();
  draw();
});

selRotInput.addEventListener("change", () => {
  if (!selectedBox) return;
  const v = parseFloat(selRotInput.value);
  if (!isFinite(v)) { syncSelectionInputs(); return; }
  const newRot = ((v % 360) + 360) % 360;
  tryApplyBoxChange(selectedBox, { rot: newRot });
  syncSelectionInputs();
  draw();
});

/* ================================================================== */
/*  Mouse / keyboard                                                  */
/* ================================================================== */

function updateCursor(sx, sy) {
  if (panning || handleDrag || drag) { canvas.style.cursor = "grabbing"; return; }
  if (hitTestHandle(sx, sy))         { canvas.style.cursor = "grab";     return; }
  const [wx, wy] = s2w(sx, sy);
  if (hitTestBox(wx, wy) >= 0)       { canvas.style.cursor = "move";     return; }
  canvas.style.cursor = "crosshair";
}

canvas.addEventListener("mousedown", (e) => {
  if (e.button === 1) {
    e.preventDefault();
    panning = { sx: e.clientX, sy: e.clientY, tx: view.tx, ty: view.ty };
    updateCursor(mouse.sx, mouse.sy);
    return;
  }
  if (e.button !== 0) return;

  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const [wx, wy] = s2w(sx, sy);

  const h = hitTestHandle(sx, sy);
  if (h) {
    if (h.type === "corner") startRotate(selectedBox, wx, wy);
    else                     startResize(selectedBox, h.index);
    updateCursor(sx, sy);
    return;
  }

  const idx = hitTestBox(wx, wy);
  if (idx >= 0) {
    const b = placedBoxes.splice(idx, 1)[0];
    placedBoxes.push(b);
    setSelected(b);
    drag = { index: placedBoxes.length - 1, ox: b.x - wx, oy: b.y - wy };
    updateCursor(sx, sy);
    return;
  }

  pendingPlace = { wx, wy, sx, sy };
});

window.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const inside = (sx >= 0 && sy >= 0 &&
                  sx < canvas.clientWidth && sy < canvas.clientHeight);

  if (panning) {
    view.tx = panning.tx + (e.clientX - panning.sx);
    view.ty = panning.ty + (e.clientY - panning.sy);
    draw(); return;
  }

  if (inside) { mouse.sx = sx; mouse.sy = sy; mouse.inside = true; }
  else        { mouse.inside = false; }

  if (handleDrag) {
    const [wx, wy] = s2w(sx, sy);
    if (handleDrag.type === "rotate") updateRotate(wx, wy, e.shiftKey);
    else                              updateResize(wx, wy);
    updateCursor(sx, sy);
    draw(); return;
  }

  if (drag) {
    const [wx, wy] = s2w(sx, sy);
    const b = placedBoxes[drag.index];
    moveBoxSafely(b, wx + drag.ox, wy + drag.oy);
    updateCursor(sx, sy);
    draw(); return;
  }

  if (pendingPlace) {
    const dx = sx - pendingPlace.sx, dy = sy - pendingPlace.sy;
    if (Math.hypot(dx, dy) > DRAG_CANCEL) {
      pendingPlace = null;
    } else {
      const [wx, wy] = s2w(sx, sy);
      pendingPlace.wx = wx; pendingPlace.wy = wy;
    }
  }

  if (inside) {
    const [wx, wy] = s2w(sx, sy);
    hoveredBox = null;
    const idx = hitTestBox(wx, wy);
    if (idx >= 0) hoveredBox = placedBoxes[idx];
  } else {
    hoveredBox = null;
  }
  updateCursor(sx, sy);
  draw();
});

window.addEventListener("mouseup", (e) => {
  if (e.button === 1) { panning = null; updateCursor(mouse.sx, mouse.sy); return; }
  if (e.button !== 0) return;

  if (handleDrag) {
    handleDrag = null;
    syncSelectionInputs();
    updateCursor(mouse.sx, mouse.sy);
    draw(); return;
  }
  if (drag) {
    drag = null;
    syncSelectionInputs();
    updateCursor(mouse.sx, mouse.sy);
    draw(); return;
  }

  if (pendingPlace) {
    const p = pendingPlace;
    pendingPlace = null;
    const { w, h, rot } = getBoxParams();
    if (boxOverlapsWalls(p.wx, p.wy, w, h, rot)) {
      flashStatus("✗ Collision with wall — cannot place here", "bad");
    } else {
      boxCounter += 1;
      const b = { x: p.wx, y: p.wy, w, h, rot, name: `Box ${boxCounter}` };
      placedBoxes.push(b);
      setSelected(b);
      flashStatus("✓ Placed (click Save to keep)", "ok");
    }
    draw();
  }
});

canvas.addEventListener("dblclick", (e) => {
  const rect = canvas.getBoundingClientRect();
  const [wx, wy] = s2w(e.clientX - rect.left, e.clientY - rect.top);
  const idx = hitTestBox(wx, wy);
  if (idx >= 0) {
    setSelected(placedBoxes[idx]);
    nameInput.focus();
    nameInput.select();
  }
});

canvas.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const [wx, wy] = s2w(e.clientX - rect.left, e.clientY - rect.top);
  const idx = hitTestBox(wx, wy);
  if (idx >= 0) {
    const wasSel = placedBoxes[idx] === selectedBox;
    if (placedBoxes[idx] === hoveredBox) hoveredBox = null;
    placedBoxes.splice(idx, 1);
    if (wasSel) setSelected(null);
    draw();
  }
});

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const [wx, wy] = s2w(sx, sy);
  const factor = Math.exp(-e.deltaY * 0.0015);
  view.scale *= factor;
  view.tx = sx - wx * view.scale;
  view.ty = sy + wy * view.scale;
  draw();
}, { passive: false });

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;

  if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
    e.preventDefault();
    doSave(true);
    return;
  }
  if (e.key === "r" || e.key === "R") {
    e.preventDefault();
    const deg = e.shiftKey ? 15 : 90;
    const b = hoveredBox || selectedBox;
    if (b) {
      const newRot = ((b.rot + deg) % 360 + 360) % 360;
      const curInvalid = boxOverlapsWalls(b.x, b.y, b.w, b.h, b.rot);
      if (!curInvalid && boxOverlapsWalls(b.x, b.y, b.w, b.h, newRot)) {
        flashStatus("✗ Rotation blocked by wall", "bad");
      } else {
        b.rot = newRot;
        if (b === selectedBox) setSelected(b);
      }
    } else {
      const cur = parseFloat(document.getElementById("boxRot").value) || 0;
      setRotation(cur + deg);
    }
    draw();
  } else if (e.key === "Escape") {
    setSelected(null);
  } else if (e.key === "Delete" || e.key === "Backspace") {
    if (selectedBox) {
      const idx = placedBoxes.indexOf(selectedBox);
      if (idx >= 0) placedBoxes.splice(idx, 1);
      setSelected(null);
    }
  }
});

/* ================================================================== */
/*  UI                                                                */
/* ================================================================== */

function getBoxParams() {
  const w = parseFloat(document.getElementById("boxW").value) || 100;
  const h = parseFloat(document.getElementById("boxH").value) || 100;
  let rot = parseFloat(document.getElementById("boxRot").value) || 0;
  rot = ((rot % 360) + 360) % 360;
  return { w, h, rot };
}

function setRotation(deg) {
  const v = ((deg % 360) + 360) % 360;
  document.getElementById("boxRot").value = v;
}

function rotate(deg) {
  const b = hoveredBox || selectedBox;
  if (b) {
    const newRot = ((b.rot + deg) % 360 + 360) % 360;
    const curInvalid = boxOverlapsWalls(b.x, b.y, b.w, b.h, b.rot);
    if (!curInvalid && boxOverlapsWalls(b.x, b.y, b.w, b.h, newRot)) {
      flashStatus("✗ Rotation blocked by wall", "bad");
      return;
    }
    b.rot = newRot;
    if (b === selectedBox) setSelected(b);
  } else {
    const cur = parseFloat(document.getElementById("boxRot").value) || 0;
    setRotation(cur + deg);
  }
}

document.getElementById("rotL90").onclick  = () => { rotate(-90); draw(); };
document.getElementById("rotR90").onclick  = () => { rotate( 90); draw(); };
document.getElementById("rotL15").onclick  = () => { rotate(-15); draw(); };
document.getElementById("rotR15").onclick  = () => { rotate( 15); draw(); };

document.getElementById("saveBtn").onclick = () => doSave(true);
document.getElementById("listBtn").onclick = () => printBoxList();
document.getElementById("pngBtn").onclick  = () => renderFloorPlanPNG();

document.getElementById("clearBtn").onclick = () => {
  if (placedBoxes.length && !confirm("Delete all boxes?")) return;
  placedBoxes = []; setSelected(null);
};
document.getElementById("undoBtn").onclick = () => {
  if (!placedBoxes.length) return;
  const last = placedBoxes[placedBoxes.length - 1];
  placedBoxes.pop();
  if (last === selectedBox) setSelected(null);
  else { syncSelectionInputs(); draw(); }
};

for (const id of ["boxW", "boxH", "boxRot"]) {
  document.getElementById(id).addEventListener("input", draw);
}

let statusTimer = null;
function updateStatus(validFlags) {
  const el = document.getElementById("status");
  if (statusTimer) return;

  if (handleDrag) {
    const b = handleDrag.b;
    el.textContent =
      (handleDrag.type === "rotate" ? "rotating" : "resizing") +
      ` · x=${b.x.toFixed(0)} y=${b.y.toFixed(0)} ` +
      `w=${b.w.toFixed(0)} h=${b.h.toFixed(0)} rot=${b.rot.toFixed(0)}°`;
    el.className = "";
    return;
  }
  if (drag) {
    const b = placedBoxes[drag.index];
    const inv = boxOverlapsWalls(b.x, b.y, b.w, b.h, b.rot);
    el.textContent = (inv ? "dragging (invalid) · " : "dragging · ") +
                     `x=${b.x.toFixed(0)} y=${b.y.toFixed(0)} ` +
                     `rot=${b.rot.toFixed(0)}°`;
    el.className = inv ? "warn" : "";
    return;
  }
  if (!mouse.inside) { el.textContent = ""; el.className = ""; return; }

  if (hoveredBox) {
    const b = hoveredBox;
    const inv = boxOverlapsWalls(b.x, b.y, b.w, b.h, b.rot);
    el.textContent = (inv ? "hover (invalid) · " : "hover · ") +
                     `${b.name || "(unnamed)"} · ` +
                     `x=${b.x.toFixed(0)} y=${b.y.toFixed(0)} ` +
                     `rot=${b.rot.toFixed(0)}°`;
    el.className = inv ? "warn" : "";
    return;
  }

  const [wx, wy] = s2w(mouse.sx, mouse.sy);
  const { w, h, rot } = getBoxParams();
  const bad = boxOverlapsWalls(wx, wy, w, h, rot);
  el.textContent = `x=${wx.toFixed(0)} y=${wy.toFixed(0)} rot=${rot.toFixed(0)}° · ` +
                   (bad ? "COLLISION (would not place)" : "OK");
  el.className = bad ? "bad" : "ok";
}

function flashStatus(msg, cls) {
  const el = document.getElementById("status");
  el.textContent = msg;
  el.className = cls;
  if (statusTimer) clearTimeout(statusTimer);
  statusTimer = setTimeout(() => { statusTimer = null; draw(); }, 1400);
}

/* ================================================================== */
/*  Go                                                                */
/* ================================================================== */

resize();
setRotation(0);
setSelected(null);
autoload();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# 3. Tiny local server for /save, /print, and static files
# ---------------------------------------------------------------------------

def print_box_list(boxes):
    """Format and print the box list to the terminal."""
    if not boxes:
        print("  (no boxes placed)")
        return

    name_w = max(4, max(len(str(b.get("name", ""))) for b in boxes))

    header = (f"  {'Name'.ljust(name_w)}  "
              f"{'Width'.rjust(10)}  {'Height'.rjust(10)}")
    rule = "  " + "-" * (name_w + 26)

    print()
    print(header)
    print(rule)
    for b in boxes:
        name = str(b.get("name", ""))
        try:
            w = float(b.get("w", 0))
        except (TypeError, ValueError):
            w = 0.0
        try:
            h = float(b.get("h", 0))
        except (TypeError, ValueError):
            h = 0.0
        print(f"  {name.ljust(name_w)}  {w:10.2f}  {h:10.2f}")
    print()


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
                    with open(BOXES_FILE, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                    n = len(data.get("boxes", [])) if isinstance(data, dict) else len(data)
                    print(f"  saved {n} boxes to {BOXES_FILE}")
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
            elif self.path == "/print":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = self.rfile.read(length)
                    data = json.loads(body)
                    boxes = (data.get("boxes", [])
                             if isinstance(data, dict) else data)
                    print_box_list(boxes)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
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
    print(f"  (boxes are saved to ./{BOXES_FILE})")
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
    print(f"Wrote {HTML_FILE}")
    print(f"  wall faces extracted : {len(geom['faces'])}")
    print(f"  step areas extracted : {len(geom['stepFaces'])}")
    print(f"  bounds (mm)          : "
          f"x [{b['minX']:.1f}, {b['maxX']:.1f}]  "
          f"y [{b['minY']:.1f}, {b['maxY']:.1f}]")
    print(f"  wall cut             : z = {geom['cutZ']:.0f} mm")
    print(f"  step slice           : z = {STEP_LOW_Z:.0f} mm "
          f"− z = {WALL_CUT_Z:.0f} mm")
    print(f"  box file             : {BOXES_FILE}")

    if SERVE:
        print()
        serve()
    else:
        print(f"  open                 : file://{os.path.abspath(HTML_FILE)}")
        print("  (SERVE=False — Save falls back to a browser download, and")
        print("   List boxes falls back to the browser console)")


if __name__ == "__main__":
    main()
