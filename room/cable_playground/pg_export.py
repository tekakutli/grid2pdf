"""
pg_export.py — print-friendly cable-run diagram exporter.

Extracted from pg_live.py so the playground's volatile UX half stays
focused on screen interaction, while the printable-diagram renderer can
evolve independently.

Design goals
------------
The export is designed to be printed in black and white.  Every
distinction that the on-screen view encodes with a colour is re-encoded
here as a texture or line-style difference:

    colour                  print encoding
    --------------------    --------------------------------
    cable (blue)            solid black, bold, white halo
    wall<->floor link       thin dotted black + filled node
    step face (blue-gray)   forward diagonal hatch, 8 px pitch
    void (pale gray)        backslash diagonal hatch, 12 px pitch
    traversed wall          solid black bar, thick, white-haloed arrows
    plan interior (tint)    faint forward diagonal hatch, 22 px pitch
    all text                solid black

The traversed-wall bar is drawn as a solid 7 px black stroke, exactly as
it was before the print rework.  Arrowheads that terminate on a wall
midpoint carry a thin white halo drawn underneath their black shape, so
they never disappear into the bar.

Exports
-------
The module's only public entry point is `EXPORT_JS`, a JS source string
concatenated into the playground bundle after pg_core.py and pg_live.py.
It installs an "Export cable runs" button, renders one PNG per true
cable, and opens a preview tab where each card carries a
`filter collinear vertices in strip` checkbox.  Toggling the checkbox
re-renders every image and every JSON in place.

Column of truth for collinear filtering
---------------------------------------
`window.filterCollinearVerticesInStrip` (default false) — when true,
_collapseStripCollinear drops any wall-edge anchor lying on the same
segment, at the same height, between two neighbours on the same segment.
Plan view, strip view, vertex labels and the JSON anchor list all see
the same filtered list, so they can never disagree.

Leader / obstacle collision
---------------------------
The strip's pill-to-anchor leaders are tuned by a coordinate-descent
optimiser.  It avoids five obstacle classes:

    1. other leaders' vertical segments,
    2. wall-section boundaries (the vertical edges between strip chunks),
    3. the cable wire itself — the polyline that runs through the
       strip's anchors,
    4. wall chunk edges — the horizontal top (z_hi) and bottom (z_lo)
       of every drawn wall band, plus the strip's outer ceiling and
       floor rules,
    5. other leaders' corner features.

Parallel-overlap detection
--------------------------
A leader that runs alongside the wire is visually indistinguishable from
the wire itself.  Sliding such a leader along the wire cannot fix the
overlap, because the motion has no component perpendicular to the wire.
Every wire/leader contact is therefore classified as *crossing* or
*parallel* and a parallel contact is weighted an order of magnitude
above a crossing.

Dive modes
----------
To actually give the optimiser a way to escape a parallel contact, each
leader has a discrete `diveMode`:

    0 — default: anchor → (anchorX + offA, chanY) → (pillX, chanY) → …
    1 — vertical first: anchor → (anchorX, chanY) → (pillX, chanY) → …
    2 — horizontal first: anchor → (pillX, anchorY) → (pillX, chanY) → …

Modes 1 and 2 introduce one pure-vertical and one pure-horizontal leg
respectively — segments that cannot be parallel to a diagonal wire.
The optimiser tries all three and accepts a switch only when the
full re-evaluated score improves, so a mode change that trades the
original parallel run for a fresh overlap elsewhere is rejected.

Wall-edge avoidance — max-distance criterion
--------------------------------------------
An earlier revision of this penalty excluded any wall edge within
WALL_EDGE_ANCHOR_TRIM of the leader's own anchor.  That was wrong: an
anchor at v = 0 sits exactly on a wall's z_lo edge, and a mode-2
horizontal leg leaves the anchor along that same edge — so the
exclusion was silently swallowing the one case we care about most.

The current criterion has no anchor trim.  Instead, for every
near-horizontal leader segment, `_wallEdgeOverlapPenalty` samples the
leader across its x-overlap with each wall edge and looks at the
*maximum* distance from the edge.  Only if the maximum is under
SEG_MIN_SEP — i.e. the leader never strays more than a line-width from
the edge — is the contact counted.  A perpendicular-ish crossing enters
and leaves the edge's neighbourhood within the overlap, so its max
distance is much larger than SEG_MIN_SEP and it is correctly ignored.

Wall-edge contacts share the wire-parallel tier: in a black-and-white
render a leader parked one line-width below a wall chunk's bottom edge
is exactly as unreadable as a leader running alongside the wire.

Vertex pill format
------------------
Every vertex pill carries, per vertex:

    • a "V<n>" name (only on the first vertex of a multi-vertex pill),
    • the vertex height as "h <cm>",
    • the distance to each end of the wall as "W <cm> . E <cm>".

The name is left-aligned at the pill's top-left.  Each "h <cm>" line
is right-aligned against the pill's right edge, so a stack of heights
reads as a clean column and two merged vertices with different heights
stay visually distinct.  The side distances are left-aligned directly
underneath the name; when a second vertex is merged in, its height is
right-aligned at the same inset and its side distances sit below, so
the pill reads as a two-column block.

Leader style assignment
-----------------------
Leaders alternate between solid and dashed strokes so that two leaders
running *parallel* to each other stay visually separable in a
black-and-white render.  A leader that only *crosses* another is
already unambiguous from the crossing itself and is deliberately left
out of this decision.

The conflict graph is built from the leaders' *rendered polylines*, not
from their abstract L-shape, and it is built AFTER the geometry
optimiser has run — the polylines it sees include offsets, dive modes,
bevels and jogs, i.e. exactly the paths the renderer will draw.  An
earlier revision built the graph before the optimiser ran, from the
mode-0 shapes, so a leader that the optimiser later switched to dive
mode 2 lost its near-anchor vertical leg and gained a new horizontal
leg at anchorY; any conflict edge justified by the abandoned shape was
spurious, and a leader with only such a partner came out dashed while
looking completely isolated.  Building the graph from the final paths
removes that class of stale-edge error entirely.

Two segments conflict when:

    • their angle difference is under STYLE_ANGLE_TOL (30°),
    • their minimum distance is under STYLE_PROX (10 px),
    • their projection overlap along the common direction is at least
      STYLE_MIN_OVERLAP (12 px).

The projection-overlap filter is what excludes pairs that only touch
near a shared anchor and then diverge: the anchors are close, but the
polylines do not run parallel for any length, so alternation would not
help and should not apply.  Crossings of dissimilar segment kinds
never enter the graph either.

The invariant we enforce is "no two solids overlap".  That is exactly a
vertex cover of the conflict graph, and we want the smallest one so the
fewest leaders lose their solid stroke.  The alternation the user sees
on a chain (S D S D) is a consequence of the cover, not a rule applied
in its own right.

The cover is computed per connected component:

    • Bipartite components (chains, trees — the common case) are
      2-coloured and the larger colour class is made solid.  This is
      the exact minimum vertex cover and yields a clean alternation.
    • Non-bipartite components (odd cycles — e.g. three mutually
      parallel leaders) fall back to a degree-greedy vertex cover and
      then run two refinement passes iterated to a fixed point:

        promote — any dashed vertex with no solid neighbour is
                  promoted back to solid.
        swap    — a dashed vertex whose every solid neighbour is also
                  adjacent to some *other* dashed vertex is promoted;
                  the formerly-covered edges stay covered by that
                  other dashed neighbour, so the cover remains valid.

Assigning per component is what keeps the cover local: a leader is
never forced to dashed because some unrelated far-away cluster
consumed a parity slot.

Localisation
------------
Every human-readable string this module writes to the rendered PNG, to
the preview page, or into a status flash comes from T() in pg_i18n.py.
The translation table lives there alongside every other string the
playground shows; this module carries no translation machinery of its
own.

Deliberately NOT translated:

    • wall-segment names — W1, W3, C1.W, SR.edge[1] — these are IDs,
      not prose.  Renaming them would break save-file compatibility.
    • pill contents — "V<n>", "h <cm>", "W <cm> . E <cm>" — the pill
      is a symbolic block and its letters are code.
    • the "..." run-gap separator.
"""


EXPORT_JS = r"""
/* ==========================================================================
   LOCALISATION
   ==========================================================================

   Every user-facing string this module writes — the render's captions,
   the preview page's chrome, the status flash messages — comes from
   T() in pg_i18n.py.  This module carries no LANG, no TRANSLATIONS,
   and no T of its own: a fork that wants a different language
   switches LANG in pg_i18n.py and translates the corresponding table
   entry, and the export follows automatically.

   See pg_i18n.py's module docstring for the pattern: keys with
   arguments are functions of their arguments, keys without are
   plain strings, and any key a partial translation table is missing
   falls back to English. */

/* ==========================================================================
   PRINT-FRIENDLY TEXTURES
   ========================================================================== */

const _PATTERN_CANVASES = Object.create(null);

function _patternCanvas(kind) {
  if (_PATTERN_CANVASES[kind]) return _PATTERN_CANVASES[kind];
  const make = (size, draw) => {
    const pc = document.createElement("canvas");
    pc.width = pc.height = size;
    const p = pc.getContext("2d");
    p.fillStyle = "#ffffff";
    p.fillRect(0, 0, size, size);
    p.strokeStyle = "#000000";
    p.fillStyle   = "#000000";
    p.lineWidth   = 0.7;
    p.lineCap     = "round";
    draw(p, size);
    return pc;
  };
  const defs = {
    step: [8, (p, s) => {
      p.lineWidth = 0.7;
      p.beginPath();
      p.moveTo(-1, s + 1);     p.lineTo(s + 1, -1);
      p.moveTo(-1, 1);         p.lineTo(1, -1);
      p.moveTo(s - 1, s + 1);  p.lineTo(s + 1, s - 1);
      p.stroke();
    }],
    void: [12, (p, s) => {
      p.lineWidth = 0.35;
      p.beginPath();
      p.moveTo(s + 1, -1);     p.lineTo(-1, s + 1);
      p.stroke();
    }],
    roomInterior: [22, (p, s) => {
      p.lineWidth = 0.4;
      p.beginPath();
      p.moveTo(-1, s + 1); p.lineTo(s + 1, -1);
      p.stroke();
    }],
  };
  const [size, draw] = defs[kind];
  _PATTERN_CANVASES[kind] = make(size, draw);
  return _PATTERN_CANVASES[kind];
}

function _pat(ctx, kind) {
  return ctx.createPattern(_patternCanvas(kind), "repeat");
}

/* ==========================================================================
   COLLINEAR VERTEX FILTER TOGGLE
   ========================================================================== */

window.filterCollinearVerticesInStrip = false;

/* Collinear-anchor filter.

   Drops the middle anchor of any three consecutive wall-edge anchors
   that sit on the same wall segment, at the same height, and on a
   straight line through their two neighbours.

   The same-segment check matters.  The room model can represent one
   physical wall as several segments — a column flush against it, an
   opening that punches through, a step riser that splits the stretch —
   and the boundary between those segments is a real vertex a cable can
   cross.  A cable running along a wall past the point where the wall
   was split must still show the vertex there.  Keeping the vertex also
   keeps the pill's two side-distance readings attached to the wall
   they were measured along; collapsing across a segment boundary would
   measure both distances against one wall and print the wrong numbers. */
function _collapseStripCollinear(orderedIds) {
  if (!window.filterCollinearVerticesInStrip) return orderedIds.slice();
  if (orderedIds.length < 3) return orderedIds.slice();

  const out = [orderedIds[0]];
  for (let i = 1; i < orderedIds.length - 1; i++) {
    const prevId = out[out.length - 1];
    const curId  = orderedIds[i];
    const nextId = orderedIds[i + 1];
    const pa = anchors.get(prevId);
    const ca = anchors.get(curId);
    const na = anchors.get(nextId);
    if (!pa || !ca || !na ||
        pa.space !== "wall-edge" ||
        ca.space !== "wall-edge" ||
        na.space !== "wall-edge" ||
        pa.segIdx !== ca.segIdx ||
        ca.segIdx !== na.segIdx) {
      out.push(curId);
      continue;
    }
    const pu = wallAttachToU(pa.segIdx, pa.t), pv = pa.v || 0;
    const cu = wallAttachToU(ca.segIdx, ca.t), cv = ca.v || 0;
    const nu = wallAttachToU(na.segIdx, na.t), nv = na.v || 0;
    const dx1 = cu - pu, dy1 = cv - pv;
    const dx2 = nu - cu, dy2 = nv - cv;
    const cross = Math.abs(dx1 * dy2 - dy1 * dx2);
    const len = Math.hypot(dx2, dy2) || 1;
    const dist = cross / len;
    if (dist < 2.0) {
      /* skip curId */
    } else {
      out.push(curId);
    }
  }
  out.push(orderedIds[orderedIds.length - 1]);
  return out;
}

/* ==========================================================================
   CABLE RUN EXPORT
   ========================================================================== */

/* ---- Linearization ---- */

function computeAnchor3D(a) {
  if (!a) return null;
  if (a.space === "floor") return [a.x, a.y, 0];
  if (a.space === "wall-edge") {
    const p = wallAttachToPlan(a.segIdx, a.t);
    if (!p) return null;
    return [p[0], p[1], a.v || 0];
  }
  return null;
}

function totalCableLength(orderedIds) {
  let total = 0;
  for (let i = 0; i < orderedIds.length - 1; i++) {
    const p = computeAnchor3D(anchors.get(orderedIds[i]));
    const q = computeAnchor3D(anchors.get(orderedIds[i + 1]));
    if (!p || !q) continue;
    total += Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
  }
  return total;
}

function linearizeTrueCable(tcId) {
  const parts = allCables().filter(c => trueCableIdOf(c) === tcId);
  if (!parts.length) return [];

  const anchorIdSet = new Set();
  for (const c of parts) for (const id of c.anchorIds) anchorIdSet.add(id);

  const adj = new Map();
  for (const id of anchorIdSet) adj.set(id, new Set());
  for (const c of parts) {
    for (let i = 0; i < c.anchorIds.length - 1; i++) {
      const a = c.anchorIds[i], b = c.anchorIds[i + 1];
      adj.get(a).add(b);
      adj.get(b).add(a);
    }
  }
  for (const j of JUNCTIONS) {
    const here = [];
    for (const t of j.terminals) {
      for (const aid of anchorIdSet) {
        const a = anchors.get(aid);
        if (!a || a.space !== "wall-edge") continue;
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
        adj.get(here[i].id).add(here[k].id);
        adj.get(here[k].id).add(here[i].id);
      }
    }
  }

  const endpoints = [];
  for (const [id, nbrs] of adj) if (nbrs.size === 1) endpoints.push(id);
  if (endpoints.length < 2) return Array.from(anchorIdSet);

  const visited = new Set();
  const path = [];
  let cur = endpoints[0];
  while (cur != null) {
    path.push(cur);
    visited.add(cur);
    let next = null;
    for (const n of adj.get(cur)) {
      if (!visited.has(n)) { next = n; break; }
    }
    cur = next;
  }
  return path;
}

function isFloorLevel(a) {
  if (!a) return false;
  if (a.space === "floor") return true;
  return Math.abs(a.v || 0) < 1e-3;
}

function groupCableRuns(orderedIds) {
  const runs = [];
  let cur = null;
  for (const id of orderedIds) {
    const a = anchors.get(id);
    if (!a) continue;
    const space = a.space === "floor" ? "floor" : "wall-edge";
    if (!cur || cur.space !== space) {
      cur = { space, ids: [] };
      runs.push(cur);
    }
    cur.ids.push(id);
  }
  return runs;
}

function groupPlanRuns(orderedIds) {
  const runs = [];
  let cur = null;
  for (const id of orderedIds) {
    const a = anchors.get(id);
    if (!a || !isFloorLevel(a)) { cur = null; continue; }
    if (!cur) { cur = { ids: [] }; runs.push(cur); }
    cur.ids.push(id);
  }
  return runs;
}

function buildSegmentDirectory(orderedIds) {
  const dir = new Map();
  let next = 1;
  for (const id of orderedIds) {
    const a = anchors.get(id);
    if (!a || a.space !== "wall-edge") continue;
    if (dir.has(a.segIdx)) continue;
    const s = WALL.segments[a.segIdx];
    dir.set(a.segIdx, {
      order: next,
      tag:   (s && s.tag) ? s.tag : ("seg " + (a.segIdx + 1)),
    });
    next++;
  }
  return dir;
}

function layoutWallRun(ids) {
  const chunks = [];
  let cur = null;
  for (const id of ids) {
    const a = anchors.get(id);
    if (!a || a.space !== "wall-edge") continue;
    const s = WALL.segments[a.segIdx];
    if (!s) continue;
    if (!cur || cur.segIdx !== a.segIdx) {
      cur = {
        segIdx:  a.segIdx,
        len:     s.len,
        z_lo:    (typeof s.z_lo === "number") ? s.z_lo : 0,
        z_hi:    (typeof s.z_hi === "number") ? s.z_hi : WALL_HEIGHT,
        kind:    s.kind || "wall",
        anchors: [],
      };
      chunks.push(cur);
    }
    cur.anchors.push({ aid: id, localU: a.t * s.len, v: a.v || 0 });
  }
  for (const ch of chunks) {
    const first = ch.anchors[0];
    const last  = ch.anchors[ch.anchors.length - 1];
    ch.mirror = first.localU > last.localU;
  }
  let totalLen = 0;
  for (const c of chunks) totalLen += c.len;
  return { chunks, totalLen };
}

function segmentKind(aid, bid, parts) {
  for (const c of parts) {
    for (let i = 0; i < c.anchorIds.length - 1; i++) {
      const a = c.anchorIds[i], b = c.anchorIds[i + 1];
      if ((a === aid && b === bid) || (a === bid && b === aid)) {
        return state.floorCables.includes(c) ? "floor" : "wall";
      }
    }
  }
  return null;
}

function buildCableRunMeta(tcId, orderedIds, segDirectory, chunks, pillNameOf) {
  const parts = allCables().filter(c => trueCableIdOf(c) === tcId);

  const walls = [];
  for (const [segIdx, entry] of segDirectory) {
    const s = WALL.segments[segIdx];
    walls.push({
      badge:     entry.order,
      segIdx:    segIdx,
      tag:       entry.tag,
      length_mm: s ? s.len : 0,
      z_lo_mm:   s ? ((typeof s.z_lo === "number") ? s.z_lo : 0) : 0,
      z_hi_mm:   s ? ((typeof s.z_hi === "number") ? s.z_hi : WALL_HEIGHT) : 0,
      kind:      s ? (s.kind || "wall") : "wall",
    });
  }
  walls.sort((a, b) => a.badge - b.badge);

  let wallLen = 0, floorLen = 0;
  for (let i = 0; i < orderedIds.length - 1; i++) {
    const aid = orderedIds[i], bid = orderedIds[i + 1];
    const p = computeAnchor3D(anchors.get(aid));
    const q = computeAnchor3D(anchors.get(bid));
    if (!p || !q) continue;
    const d = Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
    if (d < 1e-3) continue;
    const kind = segmentKind(aid, bid, parts);
    if (kind === "floor")      floorLen += d;
    else if (kind === "wall")  wallLen += d;
  }

  const anchorLookup = new Map();
  for (const ch of chunks) {
    for (const a of ch.anchors) {
      anchorLookup.set(a.aid, {
        mirror:  ch.mirror,
        len:     ch.len,
        nativeU: a.nativeU,
      });
    }
  }

  const anchorsMeta = [];
  for (let i = 0; i < orderedIds.length; i++) {
    const id = orderedIds[i];
    const a = anchors.get(id);
    if (!a) continue;
    const e = { index: i, anchorId: id, space: a.space };

    if (pillNameOf) {
      const pn = pillNameOf.get(id);
      if (pn) e.pillName = pn;
    }

    if (a.space === "floor") {
      e.x_mm = a.x;
      e.y_mm = a.y;
    } else if (a.space === "wall-edge") {
      const segEntry = segDirectory.get(a.segIdx);
      const seg      = WALL.segments[a.segIdx];
      e.segIdx     = a.segIdx;
      e.segmentTag = segEntry ? segEntry.tag   : null;
      e.badge      = segEntry ? segEntry.order : null;
      e.t          = a.t;
      e.height_mm  = a.v || 0;
      e.segment_z_lo_mm = seg ? ((typeof seg.z_lo === "number") ? seg.z_lo : 0) : 0;
      e.segment_z_hi_mm = seg ? ((typeof seg.z_hi === "number") ? seg.z_hi : WALL_HEIGHT) : 0;
      e.segment_kind    = seg ? (seg.kind || "wall") : "wall";

      const info = anchorLookup.get(id);
      if (info) {
        e.distance_west_mm = info.mirror
          ? (info.len - info.nativeU)
          : info.nativeU;
        e.distance_east_mm = info.mirror
          ? info.nativeU
          : (info.len - info.nativeU);
        e.mirrored               = info.mirror;
        e.distance_from_a_end_mm = info.nativeU;
        e.distance_from_b_end_mm = info.len - info.nativeU;
      }

      const p = wallAttachToPlan(a.segIdx, a.t);
      if (p) {
        e.world_x_mm = p[0];
        e.world_y_mm = p[1];
      }
    }
    anchorsMeta.push(e);
  }

  const totalLen = totalCableLength(orderedIds);

  return {
    schemaVersion:  6,
    cableId:        tcId,
    anchorCount:    orderedIds.length,
    partCount:      parts.length,
    totalLength_mm: totalLen,
    wallLength_mm:  wallLen,
    floorLength_mm: floorLen,
    walls:          walls,
    anchors:        anchorsMeta,
  };
}

/* ---- Badge ---- */

function badgeRadiusFor(n) {
  const digits = String(n).length;
  if (digits <= 1) return 11;
  if (digits === 2) return 12;
  return 13;
}

function drawBadge(c, x, y, num) {
  const r = badgeRadiusFor(num);
  c.save();
  c.beginPath();
  c.arc(x, y, r, 0, Math.PI * 2);
  c.fillStyle = "#ffffff";
  c.fill();
  c.strokeStyle = "#000000";
  c.lineWidth = 1.6;
  c.stroke();
  c.font = "700 12px ui-monospace, monospace";
  c.fillStyle = "#000000";
  c.textAlign = "center"; c.textBaseline = "middle";
  c.fillText(String(num), x, y + 0.5);
  c.restore();
}

/* ---- Arrow styles ---- */

const SEG_ARROW_STYLES = [
  { dash: null,           head: "solid-tri"      },
  { dash: [6, 4],         head: "hollow-tri"     },
  { dash: [2, 3],         head: "solid-circle"   },
  { dash: null,           head: "hollow-diamond" },
  { dash: [8, 3, 2, 3],   head: "solid-tri"      },
  { dash: [10, 4],        head: "solid-square"   },
  { dash: null,           head: "bar"            },
  { dash: [2, 3],         head: "hollow-circle"  },
  { dash: [6, 4],         head: "solid-diamond"  },
  { dash: null,           head: "hollow-square"  },
];

function styleFor(order) {
  return SEG_ARROW_STYLES[(order - 1) % SEG_ARROW_STYLES.length];
}

function drawArrowHead(c, x, y, ux, uy, style) {
  const px = -uy, py = ux;
  c.save();
  c.lineJoin = "round";
  c.lineCap = "round";

  const drawSilhouette = () => {
    c.beginPath();
    switch (style.head) {
      case "solid-tri":
      case "hollow-tri": {
        const s = 8, w = 4.5;
        c.moveTo(x, y);
        c.lineTo(x - ux * s + px * w, y - uy * s + py * w);
        c.lineTo(x - ux * s - px * w, y - uy * s - py * w);
        c.closePath();
        break;
      }
      case "solid-circle":
      case "hollow-circle": {
        c.arc(x, y, 4.5, 0, Math.PI * 2);
        break;
      }
      case "solid-diamond":
      case "hollow-diamond": {
        const s = 5.5;
        c.moveTo(x + ux * s, y + uy * s);
        c.lineTo(x + px * s, y + py * s);
        c.lineTo(x - ux * s, y - uy * s);
        c.lineTo(x - px * s, y - py * s);
        c.closePath();
        break;
      }
      case "solid-square":
      case "hollow-square": {
        const s = 4;
        c.moveTo(x + ux * s + px * s, y + uy * s + py * s);
        c.lineTo(x + ux * s - px * s, y + uy * s - py * s);
        c.lineTo(x - ux * s - px * s, y - uy * s - py * s);
        c.lineTo(x - ux * s + px * s, y - uy * s + py * s);
        c.closePath();
        break;
      }
      case "bar": {
        const s = 5.5;
        c.moveTo(x + px * s, y + py * s);
        c.lineTo(x - px * s, y - py * s);
        break;
      }
    }
  };

  drawSilhouette();
  c.strokeStyle = "#ffffff";
  c.lineWidth = 5;
  c.stroke();

  c.strokeStyle = "#000000";
  c.lineWidth = 1.6;
  drawSilhouette();

  switch (style.head) {
    case "solid-tri":
    case "solid-circle":
    case "solid-diamond":
    case "solid-square": {
      c.fillStyle = "#000000";
      c.fill();
      break;
    }
    case "hollow-tri":
    case "hollow-circle":
    case "hollow-diamond":
    case "hollow-square": {
      c.fillStyle = "#ffffff";
      c.fill();
      c.stroke();
      break;
    }
    case "bar": {
      c.stroke();
      break;
    }
  }
  c.restore();
}

function pathTotalLength(points) {
  let L = 0;
  for (let i = 1; i < points.length; i++) {
    L += Math.hypot(points[i][0] - points[i-1][0],
                    points[i][1] - points[i-1][1]);
  }
  return L;
}

function trimPathStart(points, trimLen) {
  if (trimLen <= 0 || points.length < 2) return points;
  const total = pathTotalLength(points);
  if (total <= trimLen) return [points[points.length - 1]];
  let acc = 0;
  for (let i = 1; i < points.length; i++) {
    const seg = Math.hypot(points[i][0] - points[i-1][0],
                            points[i][1] - points[i-1][1]);
    if (acc + seg >= trimLen) {
      const t = (trimLen - acc) / seg;
      const x = points[i-1][0] + (points[i][0] - points[i-1][0]) * t;
      const y = points[i-1][1] + (points[i][1] - points[i-1][1]) * t;
      return [[x, y], ...points.slice(i)];
    }
    acc += seg;
  }
  return points;
}

function trimPathEnd(points, trimLen) {
  if (trimLen <= 0 || points.length < 2) return points;
  const total = pathTotalLength(points);
  if (total <= trimLen) return [points[0]];
  const target = total - trimLen;
  let acc = 0;
  for (let i = 1; i < points.length; i++) {
    const seg = Math.hypot(points[i][0] - points[i-1][0],
                            points[i][1] - points[i-1][1]);
    if (acc + seg >= target) {
      const t = (target - acc) / seg;
      const x = points[i-1][0] + (points[i][0] - points[i-1][0]) * t;
      const y = points[i-1][1] + (points[i][1] - points[i-1][1]) * t;
      return [...points.slice(0, i), [x, y]];
    }
    acc += seg;
  }
  return points;
}

function drawStyledPath(c, points, style, gapStart) {
  if (points.length < 2) return;
  const HEAD_SIZE = 9;
  const total = pathTotalLength(points);

  if (total <= HEAD_SIZE + 1) {
    const tip  = points[points.length - 1];
    const prev = points[points.length - 2];
    const dx = tip[0] - prev[0], dy = tip[1] - prev[1];
    const d = Math.hypot(dx, dy);
    if (d < 0.1) return;
    drawArrowHead(c, tip[0], tip[1], dx / d, dy / d, style);
    return;
  }

  let pts = points.slice();
  if (gapStart > 0) pts = trimPathStart(pts, gapStart);
  if (pts.length < 2) return;

  const tip = pts[pts.length - 1];
  const beforeTip = pts[pts.length - 2];
  const dxT = tip[0] - beforeTip[0], dyT = tip[1] - beforeTip[1];
  const dT = Math.hypot(dxT, dyT);
  if (dT < 0.1) return;
  const ux = dxT / dT, uy = dyT / dT;

  const body = trimPathEnd(pts, HEAD_SIZE);
  if (body.length < 2) {
    drawArrowHead(c, tip[0], tip[1], ux, uy, style);
    return;
  }

  c.save();
  c.strokeStyle = "#000000";
  c.lineWidth = 1.4;
  c.lineCap = "round";
  c.lineJoin = "round";
  c.setLineDash(style.dash || []);
  c.beginPath();
  c.moveTo(body[0][0], body[0][1]);
  for (let i = 1; i < body.length; i++) {
    c.lineTo(body[i][0], body[i][1]);
  }
  c.stroke();
  c.setLineDash([]);
  c.restore();

  drawArrowHead(c, tip[0], tip[1], ux, uy, style);
}

function drawArrowSample(c, x, y, len, style) {
  drawStyledPath(c, [[x, y], [x + len, y]], style, 0);
}

/* ---- Vertex label geometry constants (shared) ---- */

const LABEL_CHANNEL_Y0   = 6;
const LABEL_CHANNEL_STEP = 8;
const LABEL_CHANNEL_GAP  = 6;
const LABEL_BOTTOM_PAD   = 4;

/* Minimum horizontal separation between a pill's left-aligned label
   (the "V<n>" name on the first vertex, or empty) and its right-aligned
   "h <cm>" height.  Ensures they never touch when the pill is only as
   wide as the widest of the two columns. */
const LABEL_LEFT_RIGHT_GAP = 18;

const LEADER_STYLES = [
  { dash: null   },
  { dash: [6, 4] },
];

/* ---- Leader offset refinement ---- */

function refineLeaderOffsets(placed, topPad, trackOffsets, stripH) {
  const MIN_SEP    = 3.0;
  const FAN_STEP   = 4.0;
  const MAX_OFFSET = 14.0;
  const MAX_PASSES = 20;

  if (!placed) return;
  for (const it of placed) {
    it.offsetA = 0;
    it.offsetP = 0;
  }
  if (placed.length < 2) return;

  for (const it of placed) {
    it._anchorYRel = it.anchorCyRel - stripH;
    it._chanYRel   = it.channelYRel;
  }

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const yOverlap = (a0, a1, b0, b1) =>
    Math.max(a0, b0) < Math.min(a1, b1) - 0.5;

  const anchorXAt = (it, y) => {
    const denom = it._chanYRel - it._anchorYRel;
    const t = denom > 1e-6 ? (y - it._anchorYRel) / denom : 0;
    return it.anchorCx + it.offsetA * t;
  };

  const anchorConflict = (A, B) => {
    if (!yOverlap(A._anchorYRel, A._chanYRel, B._anchorYRel, B._chanYRel))
      return false;
    const y0 = Math.max(A._anchorYRel, B._anchorYRel);
    const y1 = Math.min(A._chanYRel,   B._chanYRel);
    for (let s = 0; s <= 5; s++) {
      const y = y0 + (y1 - y0) * s / 5;
      if (Math.abs(anchorXAt(A, y) - anchorXAt(B, y)) < MIN_SEP) return true;
    }
    return false;
  };

  for (let pass = 0; pass < MAX_PASSES; pass++) {
    let anyChange = false;
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        const A = placed[i], B = placed[j];
        if (anchorConflict(A, B)) {
          const dir = (B.anchorCx >= A.anchorCx) ? 1 : -1;
          const nA = clamp(A.offsetA - dir * FAN_STEP * 0.5,
                           -MAX_OFFSET, MAX_OFFSET);
          const nB = clamp(B.offsetA + dir * FAN_STEP * 0.5,
                           -MAX_OFFSET, MAX_OFFSET);
          if (Math.abs(nA - A.offsetA) > 1e-6) { A.offsetA = nA; anyChange = true; }
          if (Math.abs(nB - B.offsetA) > 1e-6) { B.offsetA = nB; anyChange = true; }
        }
      }
    }
    if (!anyChange) break;
  }

  for (const it of placed) {
    const h = it._chanYRel - it._anchorYRel;
    const cap = Math.min(MAX_OFFSET, Math.max(0, h * 0.5));
    it.offsetA = clamp(it.offsetA, -cap, cap);
  }
}

/* ---- Segment collision detection ---- */

const SEG_MIN_SEP  = 6.0;
const SEG_MIN_LEN  = 5.0;

const PARALLEL_TOL = Math.PI / 12;

function _pointSegDist(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const len2 = dx*dx + dy*dy;
  if (len2 < 1e-9) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax)*dx + (py - ay)*dy) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t*dx), py - (ay + t*dy));
}

function _segSegDist(ax, ay, bx, by, cx, cy, dx, dy) {
  return Math.min(
    _pointSegDist(ax, ay, cx, cy, dx, dy),
    _pointSegDist(bx, by, cx, cy, dx, dy),
    _pointSegDist(cx, cy, ax, ay, bx, by),
    _pointSegDist(dx, dy, ax, ay, bx, by),
  );
}

function _pathSegments(path, pathIdx, minLen) {
  const out = [];
  for (let i = 0; i < path.length - 1; i++) {
    const a = path[i], b = path[i + 1];
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const len = Math.hypot(dx, dy);
    if (len < minLen) continue;
    let kind;
    if (Math.abs(dx) < 0.5)      kind = "vert";
    else if (Math.abs(dy) < 0.5) kind = "horiz";
    else                          kind = "diag";
    out.push({
      pathIdx, segIdx: i, kind, len,
      ax: a[0], ay: a[1], bx: b[0], by: b[1],
      minX: Math.min(a[0], b[0]), maxX: Math.max(a[0], b[0]),
      minY: Math.min(a[1], b[1]), maxY: Math.max(a[1], b[1]),
      angle: Math.atan2(dy, dx),
    });
  }
  return out;
}

function _findSegmentConflicts(segs, minSep) {
  const out = [];
  for (let i = 0; i < segs.length; i++) {
    const A = segs[i];
    for (let j = i + 1; j < segs.length; j++) {
      const B = segs[j];
      if (A.pathIdx === B.pathIdx) continue;
      if (A.kind === "horiz" && B.kind === "horiz") continue;
      if (A.maxX + minSep < B.minX) continue;
      if (B.maxX + minSep < A.minX) continue;
      if (A.maxY + minSep < B.minY) continue;
      if (B.maxY + minSep < A.minY) continue;
      const d = _segSegDist(A.ax, A.ay, A.bx, A.by,
                            B.ax, B.ay, B.bx, B.by);
      if (d < minSep) out.push({ a: A, b: B, dist: d });
    }
  }
  return out;
}

/* ---- Style conflict detection ----

   A style conflict is two leaders whose rendered polylines come
   within STYLE_PROX pixels of each other *while running parallel*.
   Crossings are excluded: two leaders that merely cross are
   unambiguously distinguishable and need no alternation.

   Two segments count as parallel-close when all of:

     • their angle difference is under STYLE_ANGLE_TOL (30°);
     • their minimum distance is under STYLE_PROX (10 px);
     • their projection overlap along the common direction is at
       least STYLE_MIN_OVERLAP (12 px).

   The projection-overlap filter is what rejects leaders that only
   converge at a point — two cables that share a wall anchor and
   diverge immediately touch near the anchor but do not run parallel,
   so alternation would not help and should not apply.  An earlier
   revision used an abstract L-shape per leader and a single
   CONFLICT_DIST on each leg, which fired on any pair of anchors
   within 14 px in x regardless of how briefly they were close.

   The graph is built from the FINAL leader polylines — offsets, dive
   modes, bevels and jogs included — because it must see exactly what
   the renderer will draw.  Building it from the default mode-0 shapes
   left it stale as soon as the optimiser moved a leader: a switch to
   dive mode 2 deletes the near-anchor vertical leg and inserts a new
   horizontal leg at anchorY, so any conflict edge justified by the
   abandoned shape is spurious, and a leader whose only partner was
   such an edge comes out dashed while looking completely isolated. */

const STYLE_ANGLE_TOL   = Math.PI / 6;
const STYLE_PROX        = 10.0;
const STYLE_MIN_OVERLAP = 12.0;

function _segProjectionOverlap(A, B) {
  const lenA = Math.hypot(A.bx - A.ax, A.by - A.ay);
  const lenB = Math.hypot(B.bx - B.ax, B.by - B.ay);
  if (lenA < 1e-6 || lenB < 1e-6) return 0;
  let ux = (A.bx - A.ax) / lenA + (B.bx - B.ax) / lenB;
  let uy = (A.by - A.ay) / lenA + (B.by - B.ay) / lenB;
  const dLen = Math.hypot(ux, uy);
  if (dLen < 1e-6) return 0;
  ux /= dLen; uy /= dLen;
  const a0 = A.ax * ux + A.ay * uy;
  const a1 = A.bx * ux + A.by * uy;
  const b0 = B.ax * ux + B.ay * uy;
  const b1 = B.bx * ux + B.by * uy;
  const aLo = Math.min(a0, a1), aHi = Math.max(a0, a1);
  const bLo = Math.min(b0, b1), bHi = Math.max(b0, b1);
  return Math.max(0, Math.min(aHi, bHi) - Math.max(aLo, bLo));
}

function _buildStyleConflictGraph(placed, stripH, topPad, trackOffsets) {
  const paths = placed.map(it => {
    const base = _buildLeaderPathRel(it, placed, stripH, topPad, trackOffsets);
    const bev  = _bevelPath(base, it.bevel0 || 0, it.bevel1 || 0);
    return _applyJogsToPath(bev, it);
  });
  const segsByPath = paths.map((path, i) => _pathSegments(path, i, 2.0));

  const adj = new Map();
  for (const it of placed) adj.set(it, new Set());

  for (let i = 0; i < segsByPath.length; i++) {
    for (let k = 0; k < segsByPath[i].length; k++) {
      const A = segsByPath[i][k];
      for (let j = i + 1; j < segsByPath.length; j++) {
        for (let l = 0; l < segsByPath[j].length; l++) {
          const B = segsByPath[j][l];
          if (_angleDiff(A.angle, B.angle) > STYLE_ANGLE_TOL) continue;
          if (A.maxX + STYLE_PROX < B.minX) continue;
          if (B.maxX + STYLE_PROX < A.minX) continue;
          if (A.maxY + STYLE_PROX < B.minY) continue;
          if (B.maxY + STYLE_PROX < A.minY) continue;
          const d = _segSegDist(A.ax, A.ay, A.bx, A.by,
                                B.ax, B.ay, B.bx, B.by);
          if (d >= STYLE_PROX) continue;
          if (_segProjectionOverlap(A, B) < STYLE_MIN_OVERLAP) continue;
          adj.get(placed[i]).add(placed[j]);
          adj.get(placed[j]).add(placed[i]);
        }
      }
    }
  }
  return adj;
}

/* ---- Wall-section boundary collision ----

   Vertical boundaries between wall chunks.  Same shape as the wall-edge
   penalty, but mirrored: the leader must be near-vertical for the check
   to apply, and the boundary is a vertical line rather than a horizontal
   one. */

function _boundaryOverlapPenalty(leaderSegs, boundaries, stripH) {
  let count = 0, depth = 0;
  if (!boundaries || !boundaries.length) return { count, depth };

  const SLOPE_MAX = 0.4;

  for (const s of leaderSegs) {
    const dy = s.by - s.ay;
    if (Math.abs(dy) < 1e-6) continue;
    const slope = Math.abs((s.bx - s.ax) / dy);
    if (slope > SLOPE_MAX) continue;

    const yLo = Math.max(s.minY, 0);
    const yHi = Math.min(s.maxY, stripH);
    if (yHi - yLo < SEG_MIN_LEN) continue;

    const dx = s.bx - s.ax;
    for (const bx of boundaries) {
      if (s.maxX + SEG_MIN_SEP < bx) continue;
      if (s.minX - SEG_MIN_SEP > bx) continue;

      let sumDist = 0;
      const N = 5;
      for (let k = 0; k <= N; k++) {
        const y = yLo + (yHi - yLo) * k / N;
        const t = (y - s.ay) / dy;
        const x = s.ax + dx * t;
        sumDist += Math.abs(x - bx);
      }
      const avgDist = sumDist / (N + 1);
      if (avgDist < SEG_MIN_SEP) {
        count++;
        depth += (SEG_MIN_SEP - avgDist);
      }
    }
  }
  return { count, depth };
}

/* ---- Cable-wire collision ----

   The strip's wire is a polyline that runs through every wall-edge
   anchor of the cable, in order.  Leaders leave an anchor and dive into
   the channel below the strip; a leader segment that comes within
   SEG_MIN_SEP of a wire segment is penalised — except when the wire
   segment belongs to the leader's own anchor (a leader always starts
   *on* the wire at its anchor, so proximity there is expected).

   Every contact is further classified as *parallel* or *crossing* by
   comparing segment inclinations.  A parallel contact is weighted an
   order of magnitude above a crossing. */

const WIRE_ANCHOR_TRIM = 8.0;

function _angleDiff(a, b) {
  let d = Math.abs(a - b) % Math.PI;
  if (d > Math.PI / 2) d = Math.PI - d;
  return d;
}

function _wireOverlapPenalty(leaderSegs, placed, wireSegments) {
  let count = 0, depth = 0;
  let parCount = 0, parDepth = 0;
  if (!wireSegments || !wireSegments.length)
    return { count, depth, parCount, parDepth };

  for (const s of leaderSegs) {
    const it = placed[s.pathIdx];
    if (!it) continue;
    const ax = it.anchorCx;
    const ay = it.anchorCyRel;
    const sAngle = s.angle;

    for (const w of wireSegments) {
      const wminX = Math.min(w.ax, w.bx);
      const wmaxX = Math.max(w.ax, w.bx);
      const wminY = Math.min(w.ay, w.by);
      const wmaxY = Math.max(w.ay, w.by);

      if (s.maxX + SEG_MIN_SEP < wminX) continue;
      if (wmaxX + SEG_MIN_SEP < s.minX) continue;
      if (s.maxY + SEG_MIN_SEP < wminY) continue;
      if (wmaxY + SEG_MIN_SEP < s.minY) continue;

      const dAnchor = _pointSegDist(ax, ay, w.ax, w.ay, w.bx, w.by);
      if (dAnchor < WIRE_ANCHOR_TRIM) continue;

      const d = _segSegDist(s.ax, s.ay, s.bx, s.by,
                            w.ax, w.ay, w.bx, w.by);
      if (d < SEG_MIN_SEP) {
        count++;
        depth += (SEG_MIN_SEP - d);

        const wAngle = Math.atan2(w.by - w.ay, w.bx - w.ax);
        if (_angleDiff(sAngle, wAngle) < PARALLEL_TOL) {
          parCount++;
          parDepth += (SEG_MIN_SEP - d);
        }
      }
    }
  }
  return { count, depth, parCount, parDepth };
}

/* ---- Wall-edge collision ----

   Every drawn wall chunk in the strip is a rectangle; its top edge is
   z_hi mapped to strip y, its bottom edge is z_lo mapped to strip y.
   The strip also has two outer rules — the ceiling dashed line at y = 0
   and the floor dashed line at y = stripH — that bound the whole band.

   The criterion is the MAXIMUM distance from the leader to the edge over
   the x-overlap, not the average.  A perpendicular-ish crossing enters
   and leaves the edge's neighbourhood within the overlap, so its max
   distance far exceeds SEG_MIN_SEP and it is ignored.  A run-along
   leader — even one that starts *on* the edge at its own anchor, which
   is exactly what dive mode 2 produces for a floor-level anchor — has
   max distance close to zero and is penalised.

   This deliberately has no "anchor trim" exclusion.  An earlier
   revision skipped any edge within 8 px of the leader's anchor, which
   silently swallowed the case we most care about: an anchor at v = 0
   sits exactly on a wall's z_lo edge, and a mode-2 horizontal leg
   leaves the anchor along that very edge. */

function _wallEdgeOverlapPenalty(leaderSegs, wallEdges) {
  let count = 0, depth = 0;
  if (!wallEdges || !wallEdges.length) return { count, depth };

  const SLOPE_MAX = 0.4;

  for (const s of leaderSegs) {
    const dx = s.bx - s.ax;
    const dy = s.by - s.ay;
    if (Math.abs(dx) < 1e-6) continue;
    const slope = Math.abs(dy / dx);
    if (slope > SLOPE_MAX) continue;

    for (const we of wallEdges) {
      const xLo = Math.max(s.minX, we.x0);
      const xHi = Math.min(s.maxX, we.x1);
      if (xHi - xLo < 2 * SEG_MIN_LEN) continue;

      if (s.minY - SEG_MIN_SEP > we.y) continue;
      if (s.maxY + SEG_MIN_SEP < we.y) continue;

      let maxDist = 0;
      let sumDist = 0;
      const N = 5;
      for (let k = 0; k <= N; k++) {
        const x = xLo + (xHi - xLo) * k / N;
        const t = (x - s.ax) / dx;
        const y = s.ay + dy * t;
        const d = Math.abs(y - we.y);
        if (d > maxDist) maxDist = d;
        sumDist += d;
      }
      if (maxDist < SEG_MIN_SEP) {
        count++;
        depth += (SEG_MIN_SEP - sumDist / (N + 1));
      }
    }
  }
  return { count, depth };
}

/* ---- Corner features and exclusion zones ---- */

const CORNER_MIN_DIST = 24.0;

function _cornerFeature(it, placed, stripH, topPad, trackOffsets, which) {
  const anchorY  = it.anchorCyRel;
  const chanY    = stripH + (it.channelYRel || 0);
  const pillTopY = stripH + topPad + trackOffsets[it.track];
  const basePath = computeLeaderPath(
    it.anchorCx, anchorY, it.offsetA || 0,
    chanY, it.pillCenterX, pillTopY,
    it, placed, stripH, topPad, trackOffsets, it.diveMode || 0);

  if (basePath.length < 3) {
    const p = basePath[0] || [0, 0];
    return { x0: p[0], y0: p[1], x1: p[0], y1: p[1] };
  }

  let A, C, N, b;
  if (which === 0) {
    A = basePath[0];
    C = basePath[1];
    N = basePath[2];
    b = it.bevel0 || 0;
  } else {
    A = basePath[basePath.length - 3];
    C = basePath[basePath.length - 2];
    N = basePath[basePath.length - 1];
    b = it.bevel1 || 0;
  }

  if (b < BEVEL_MIN_APPLY) {
    return { x0: C[0], y0: C[1], x1: C[0], y1: C[1] };
  }
  const lenA = Math.hypot(C[0] - A[0], C[1] - A[1]);
  const lenB = Math.hypot(N[0] - C[0], N[1] - C[1]);
  const bU = Math.min(b, lenA * 0.85, lenB * 0.85);
  if (bU < BEVEL_MIN_APPLY || lenA < 1e-6 || lenB < 1e-6) {
    return { x0: C[0], y0: C[1], x1: C[0], y1: C[1] };
  }
  const t1 = (lenA - bU) / lenA;
  const t2 = bU / lenB;
  const p1 = [A[0] + (C[0] - A[0]) * t1, A[1] + (C[1] - A[1]) * t1];
  const p2 = [C[0] + (N[0] - C[0]) * t2, C[1] + (N[1] - C[1]) * t2];
  return { x0: p1[0], y0: p1[1], x1: p2[0], y1: p2[1] };
}

function _cornerDist(fA, fB) {
  const aIsPt = (Math.abs(fA.x0 - fA.x1) < 0.5 && Math.abs(fA.y0 - fA.y1) < 0.5);
  const bIsPt = (Math.abs(fB.x0 - fB.x1) < 0.5 && Math.abs(fB.y0 - fB.y1) < 0.5);
  if (aIsPt && bIsPt) {
    return Math.hypot(fA.x0 - fB.x0, fA.y0 - fB.y0);
  }
  if (aIsPt) {
    return _pointSegDist(fA.x0, fA.y0, fB.x0, fB.y0, fB.x1, fB.y1);
  }
  if (bIsPt) {
    return _pointSegDist(fB.x0, fB.y0, fA.x0, fA.y0, fA.x1, fA.y1);
  }
  return _segSegDist(fA.x0, fA.y0, fA.x1, fA.y1,
                     fB.x0, fB.y0, fB.x1, fB.y1);
}

/* ---- Leader geometry optimiser ---- */

const BEVEL_STEP      = 3.0;
const BEVEL_MAX       = 45.0;
const BEVEL_MIN_APPLY = 6.0;
const JOG_STEP        = 3.0;
const JOG_MAX         = 24.0;
const JOG_MIN_APPLY   = 6.0;
const OPT_MAX_PASSES  = 30;

function _buildChannelToPill(pillX, chanY, pillTopY, self, placed,
                              stripBottom, topPad, trackOffsets) {
  const M = 4;
  const out = [];
  const blockers = [];
  for (const Q of placed) {
    if (Q === self) continue;
    const qL = Q.pillCenterX - Q.w / 2;
    const qR = Q.pillCenterX + Q.w / 2;
    const qT = stripBottom + topPad + trackOffsets[Q.track];
    const qB = qT + Q.h;
    if (pillX > qL && pillX < qR && qB > chanY && qT < pillTopY) {
      blockers.push({ qL, qR, qT, qB });
    }
  }
  blockers.sort((a, b) => a.qT - b.qT);

  let curY = chanY;
  for (const b of blockers) {
    if (b.qT <= curY) continue;
    const distL = pillX - b.qL;
    const distR = b.qR - pillX;
    const detourX = (distR < distL) ? (b.qR + M) : (b.qL - M);
    out.push([pillX,   b.qT - M]);
    out.push([detourX, b.qT - M]);
    out.push([detourX, b.qB + M]);
    out.push([pillX,   b.qB + M]);
    curY = b.qB + M;
  }
  out.push([pillX, pillTopY]);
  return out;
}

function computeLeaderPath(anchorX, anchorY, offA, chanY, pillX, pillTopY,
                           self, placed, stripBottom, topPad, trackOffsets,
                           diveMode) {
  const mode = diveMode || 0;
  const path = [];
  path.push([anchorX, anchorY]);
  if (mode === 1) {
    path.push([anchorX, chanY]);
    path.push([pillX,   chanY]);
  } else if (mode === 2) {
    path.push([pillX, anchorY]);
    path.push([pillX, chanY]);
  } else {
    path.push([anchorX + offA, chanY]);
    path.push([pillX,          chanY]);
  }

  const tail = _buildChannelToPill(pillX, chanY, pillTopY, self, placed,
                                    stripBottom, topPad, trackOffsets);
  for (const p of tail) path.push(p);
  return path;
}

function _buildLeaderPathRel(it, placed, stripH, topPad, trackOffsets) {
  const anchorY  = it.anchorCyRel;
  const chanY    = stripH + it.channelYRel;
  const pillTopY = stripH + topPad + trackOffsets[it.track];
  return computeLeaderPath(
    it.anchorCx, anchorY, it.offsetA || 0,
    chanY, it.pillCenterX, pillTopY,
    it, placed, stripH, topPad, trackOffsets, it.diveMode || 0);
}

function _bevelPath(path, b0, b1) {
  const n = path.length;
  if (n < 4) return path;
  if (b0 < BEVEL_MIN_APPLY && b1 < BEVEL_MIN_APPLY) return path;

  const out = [path[0]];

  const A   = path[0];
  const C1  = path[1];
  const N1  = path[2];
  const lenA = Math.hypot(C1[0] - A[0],  C1[1] - A[1]);
  const lenB = Math.hypot(N1[0] - C1[0], N1[1] - C1[1]);
  const b0u  = Math.min(b0, lenA * 0.85, lenB * 0.85);
  if (b0u >= BEVEL_MIN_APPLY && lenA > 1e-6 && lenB > 1e-6) {
    const t1 = (lenA - b0u) / lenA;
    const t2 = b0u / lenB;
    out.push([A[0]  + (C1[0] - A[0])  * t1,
              A[1]  + (C1[1] - A[1])  * t1]);
    out.push([C1[0] + (N1[0] - C1[0]) * t2,
              C1[1] + (N1[1] - C1[1]) * t2]);
  } else {
    out.push(C1);
  }

  for (let i = 2; i <= n - 3; i++) out.push(path[i]);

  const M2  = path[n - 3];
  const C2  = path[n - 2];
  const P   = path[n - 1];
  const lenC = Math.hypot(C2[0] - M2[0], C2[1] - M2[1]);
  const lenD = Math.hypot(P[0]  - C2[0], P[1]  - C2[1]);
  const b1u  = Math.min(b1, lenC * 0.85, lenD * 0.85);
  if (b1u >= BEVEL_MIN_APPLY && lenC > 1e-6 && lenD > 1e-6) {
    const t1 = (lenC - b1u) / lenC;
    const t2 = b1u / lenD;
    out.push([M2[0] + (C2[0] - M2[0]) * t1,
              M2[1] + (C2[1] - M2[1]) * t1]);
    out.push([C2[0] + (P[0]  - C2[0]) * t2,
              C2[1] + (P[1]  - C2[1]) * t2]);
  } else {
    out.push(C2);
  }

  out.push(P);
  return out;
}

function _jogSegment(points, segIdx, shift) {
  if (!shift || Math.abs(shift) < JOG_MIN_APPLY) return points;
  const n = points.length;
  if (segIdx < 0 || segIdx >= n - 1) return points;
  const A = points[segIdx];
  const B = points[segIdx + 1];
  if (Math.abs(A[0] - B[0]) > 0.5) return points;
  const d  = Math.abs(shift);
  const X  = A[0];
  const yA = A[1], yB = B[1];
  const L  = Math.abs(yB - yA);
  if (L < 4 * d) return points;
  const sgn = (yB > yA) ? 1 : -1;
  const M1 = [X + shift, yA + sgn * d];
  const M2 = [X + shift, yB - sgn * d];
  return points.slice(0, segIdx + 1)
    .concat([M1, M2], points.slice(segIdx + 1));
}

function _applyJogsToPath(path, it) {
  let p = path;
  if (it.jog0 && Math.abs(it.jog0) >= JOG_MIN_APPLY && p.length >= 2) {
    p = _jogSegment(p, 0, it.jog0);
  }
  if (it.jog1 && Math.abs(it.jog1) >= JOG_MIN_APPLY && p.length >= 2) {
    p = _jogSegment(p, p.length - 2, it.jog1);
  }
  return p;
}

function _candidatesFor(cur, isBevel) {
  const step     = isBevel ? BEVEL_STEP : JOG_STEP;
  const maxV     = isBevel ? BEVEL_MAX  : JOG_MAX;
  const minV     = isBevel ? BEVEL_MIN_APPLY : JOG_MIN_APPLY;
  const allowNeg = !isBevel;

  const set = new Set();

  const snap = (v) => {
    if (v === 0) return 0;
    if (!allowNeg && v < 0) return null;
    const a = Math.abs(v);
    if (a < minV) return null;
    const c = Math.min(maxV, a);
    return allowNeg ? Math.sign(v) * c : c;
  };

  const s1 = snap(cur + step);
  const s2 = snap(cur - step);
  if (s1 !== null) set.add(s1);
  if (s2 !== null) set.add(s2);

  if (cur === 0) {
    set.add(minV);
    if (allowNeg) set.add(-minV);
  }

  if (cur !== 0) set.add(0);

  set.delete(cur);
  return set;
}

const DIVE_MODES = [0, 1, 2];

function optimizeLeaderGeometry(placed, stripH, topPad, trackOffsets,
                                boundaries, wireSegments, wallEdges) {
  for (const it of placed) {
    it.bevel0 = 0; it.bevel1 = 0;
    it.jog0   = 0; it.jog1   = 0;
    it.diveMode = 0;
  }
  if (placed.length < 2) return;

  const bxs   = Array.isArray(boundaries)   ? boundaries   : [];
  const wires = Array.isArray(wireSegments) ? wireSegments : [];
  const wes   = Array.isArray(wallEdges)    ? wallEdges    : [];

  function buildPaths() {
    return placed.map(it => {
      const base = _buildLeaderPathRel(it, placed, stripH, topPad,
                                        trackOffsets);
      const bev  = _bevelPath(base, it.bevel0 || 0, it.bevel1 || 0);
      return _applyJogsToPath(bev, it);
    });
  }

  function evaluate() {
    const paths = buildPaths();

    const allSegs = [];
    for (let i = 0; i < paths.length; i++) {
      for (const s of _pathSegments(paths[i], i, SEG_MIN_LEN)) {
        allSegs.push(s);
      }
    }

    /* --- tier 1: vertical-vertical overlaps (leader vs leader) --- */
    let vvCount = 0, vvDepth = 0;
    for (let i = 0; i < allSegs.length; i++) {
      const A = allSegs[i];
      if (A.kind !== "vert") continue;
      for (let j = i + 1; j < allSegs.length; j++) {
        const B = allSegs[j];
        if (B.kind !== "vert") continue;
        if (A.pathIdx === B.pathIdx) continue;
        const dx = Math.abs(A.ax - B.ax);
        if (dx >= SEG_MIN_SEP) continue;
        const oy0 = Math.max(A.minY, B.minY);
        const oy1 = Math.min(A.maxY, B.maxY);
        if (oy1 <= oy0) continue;
        vvCount++;
        vvDepth += (SEG_MIN_SEP - dx);
      }
    }

    /* --- tier 2: leader vs leader, any non-horizontal/non-horizontal pair --- */
    let segCount = 0, segDepth = 0;
    const segConflicts = _findSegmentConflicts(allSegs, SEG_MIN_SEP);
    for (const c of segConflicts) {
      const isVV = (c.a.kind === "vert" && c.b.kind === "vert");
      if (isVV) continue;
      segCount++;
      segDepth += (SEG_MIN_SEP - c.dist);
    }

    /* --- tier 2b: leader vs wall-section boundary --- */
    const bPen = _boundaryOverlapPenalty(allSegs, bxs, stripH);
    const bCount = bPen.count;
    const bDepth = bPen.depth;

    /* --- tier 2c: leader vs cable wire (parallel vs crossing split) --- */
    const wPen = _wireOverlapPenalty(allSegs, placed, wires);
    const wireCount    = wPen.count;
    const wireDepth    = wPen.depth;
    const wireParCount = wPen.parCount;
    const wireParDepth = wPen.parDepth;

    /* --- tier 2d: leader vs wall chunk edges (horizontal rules) --- */
    const ePen = _wallEdgeOverlapPenalty(allSegs, wes);
    const weCount = ePen.count;
    const weDepth = ePen.depth;

    /* --- tier 3: corner-feature exclusion zones --- */
    const corners = [];
    for (let pi = 0; pi < placed.length; pi++) {
      const it = placed[pi];
      corners.push({
        pathIdx: pi,
        ..._cornerFeature(it, placed, stripH, topPad, trackOffsets, 0),
      });
      corners.push({
        pathIdx: pi,
        ..._cornerFeature(it, placed, stripH, topPad, trackOffsets, 1),
      });
    }

    let cornerCount = 0, cornerDepth = 0;

    for (let i = 0; i < corners.length; i++) {
      for (let j = i + 1; j < corners.length; j++) {
        const A = corners[i], B = corners[j];
        if (A.pathIdx === B.pathIdx) continue;
        const d = _cornerDist(A, B);
        if (d < CORNER_MIN_DIST) {
          cornerCount++;
          cornerDepth += (CORNER_MIN_DIST - d);
        }
      }
    }

    for (const cf of corners) {
      const cfIsPt = (Math.abs(cf.x0 - cf.x1) < 0.5 &&
                      Math.abs(cf.y0 - cf.y1) < 0.5);
      for (const s of allSegs) {
        if (s.pathIdx === cf.pathIdx) continue;
        let d;
        if (cfIsPt) {
          d = _pointSegDist(cf.x0, cf.y0, s.ax, s.ay, s.bx, s.by);
        } else {
          d = _segSegDist(cf.x0, cf.y0, cf.x1, cf.y1,
                          s.ax, s.ay, s.bx, s.by);
        }
        if (d < CORNER_MIN_DIST) {
          cornerCount++;
          cornerDepth += (CORNER_MIN_DIST - d);
        }
      }
    }

    /* Boundary collisions, wire collisions and leader-leader segment
       collisions sit at the base tier.  Wire-parallel overlaps AND
       wall-edge overlaps both sit one tier above: in a black-and-white
       render, running alongside either is genuinely harder to read
       than a crossing, because the eye cannot disentangle the two
       strokes.  Sliding the leader cannot fix such an overlap, so the
       tier is high enough to force the optimiser to consider a
       dive-mode switch instead. */
    const score =
      vvCount      * 1e12 + vvDepth      * 1e11 +
      wireParCount * 1e11 + wireParDepth * 1e10 +
      weCount      * 1e11 + weDepth      * 1e10 +
      segCount     * 1e9  + segDepth     * 1e8  +
      bCount       * 1e9  + bDepth       * 1e8  +
      wireCount    * 1e9  + wireDepth    * 1e8  +
      cornerCount  * 1e6  + cornerDepth  * 1e5;

    return { score };
  }

  let cur = evaluate();
  if (cur.score < 1) return;

  for (let pass = 0; pass < OPT_MAX_PASSES; pass++) {
    let best = null;

    for (const it of placed) {
      for (const field of ["bevel0", "bevel1", "jog0", "jog1"]) {
        const curVal  = it[field] || 0;
        const isBevel = field.startsWith("bevel");
        const cands   = _candidatesFor(curVal, isBevel);

        for (const cand of cands) {
          const saved = it[field];
          it[field] = cand;
          const test = evaluate();
          it[field] = saved;

          if (test.score < cur.score - 0.5) {
            if (!best || test.score < best.test.score) {
              best = { it, field, newValue: cand, test };
            }
          }
        }
      }

      const curMode = it.diveMode || 0;
      for (const cand of DIVE_MODES) {
        if (cand === curMode) continue;
        const saved = it.diveMode;
        it.diveMode = cand;
        const test = evaluate();
        it.diveMode = saved;

        if (test.score < cur.score - 0.5) {
          if (!best || test.score < best.test.score) {
            best = { it, field: 'diveMode', newValue: cand, test };
          }
        }
      }
    }

    if (!best) break;
    best.it[best.field] = best.newValue;
    cur = best.test;
    if (cur.score < 1) break;
  }
}

/* ---- Vertex coordinate labels ---- */

function computeVertexLabelPlacement(c, chunks, stripOffsetX,
                                     stripAreaX0, stripAreaW, stripH,
                                     orderedIds) {
  const fmtCm = (mm) => String(Math.round(mm / 10));
  const FONT  = "700 12px ui-monospace, monospace";
  const PAD_X = 6, PAD_Y = 4, LINE_H = 14;
  const TRACK_GAP_X   = 10;
  const CROSS_MARGIN  = 5;
  const TRACK_V_GAP   = 8;
  const TRACK_PENALTY = 24;
  const DISP_CAP_SWAP = 40;
  const GROUP_TOL     = 8;
  const MAX_TRACKS    = 20;
  const AREA_X0 = stripAreaX0;
  const AREA_X1 = stripAreaX0 + stripAreaW;

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  const boundaries = [];
  const seenBx = new Set();
  for (const ch of chunks) {
    const cands = [stripOffsetX + ch.x0, stripOffsetX + ch.x1];
    for (const bx of cands) {
      const key = Math.round(bx * 2) / 2;
      if (seenBx.has(key)) continue;
      seenBx.add(key);
      boundaries.push(bx);
    }
  }

  const wireSegments = [];
  {
    const byRun = new Map();
    for (const ch of chunks) {
      let list = byRun.get(ch.runIdx);
      if (!list) { list = []; byRun.set(ch.runIdx, list); }
      list.push(ch);
    }
    for (const [, chList] of byRun) {
      const flat = [];
      for (const ch of chList) {
        for (const a of ch.anchors) flat.push(a);
      }
      for (let i = 0; i < flat.length - 1; i++) {
        const ax = stripOffsetX + flat[i].xPx;
        const ay = flat[i].yPx;
        const bx = stripOffsetX + flat[i + 1].xPx;
        const by = flat[i + 1].yPx;
        wireSegments.push({
          ax, ay, bx, by,
          angle: Math.atan2(by - ay, bx - ax),
        });
      }
    }
  }

  const wallEdges = [];
  {
    let stripX0 = Infinity, stripX1 = -Infinity;
    for (const ch of chunks) {
      const x0 = stripOffsetX + ch.x0;
      const x1 = stripOffsetX + ch.x1;
      const yTop = (1 - ch.z_hi / WALL_HEIGHT) * stripH;
      const yBot = (1 - ch.z_lo / WALL_HEIGHT) * stripH;
      wallEdges.push({ y: yTop, x0, x1 });
      wallEdges.push({ y: yBot, x0, x1 });
      if (x0 < stripX0) stripX0 = x0;
      if (x1 > stripX1) stripX1 = x1;
    }
    if (isFinite(stripX0) && isFinite(stripX1)) {
      wallEdges.push({ y: 0,      x0: stripX0, x1: stripX1 });
      wallEdges.push({ y: stripH, x0: stripX0, x1: stripX1 });
    }
  }

  const orderIdx = new Map();
  if (Array.isArray(orderedIds)) {
    orderedIds.forEach((id, i) => orderIdx.set(id, i));
  }

  const rawAnchors = [];
  for (const ch of chunks) {
    const chunkMid = stripOffsetX + (ch.x0 + ch.x1) / 2;
    for (const a of ch.anchors) {
      const anc = anchors.get(a.aid);
      if (!anc || anc.space !== "wall-edge") continue;
      rawAnchors.push({
        anchorCx: stripOffsetX + a.xPx,
        anchorCyRel: a.yPx,
        h: anc.v || 0,
        nativeU: a.nativeU,
        len: ch.len,
        mirror: ch.mirror,
        segIdx: ch.segIdx,
        chunkMid,
        aid: a.aid,
      });
    }
  }

  const clusters = [];
  const used = new Array(rawAnchors.length).fill(false);
  for (let i = 0; i < rawAnchors.length; i++) {
    if (used[i]) continue;
    const A = rawAnchors[i];
    const grp = [A];
    used[i] = true;
    for (let j = i + 1; j < rawAnchors.length; j++) {
      if (used[j]) continue;
      const B = rawAnchors[j];
      if (Math.abs(A.anchorCx - B.anchorCx) < GROUP_TOL &&
          Math.abs(A.anchorCyRel - B.anchorCyRel) < GROUP_TOL) {
        grp.push(B);
        used[j] = true;
      }
    }
    clusters.push(grp);
  }

  const sideLineFor = (m) => {
    const wDist = m.mirror ? (m.len - m.nativeU) : m.nativeU;
    const eDist = m.mirror ? m.nativeU : (m.len - m.nativeU);
    return "W " + fmtCm(wDist) + " . E " + fmtCm(eDist);
  };

  /* Build each pill as a list of { left, right } line descriptors.
     left  — drawn left-aligned at rx + PAD_X (the "V<n>" name only
             appears on the first line of a multi-vertex pill).
     right — drawn right-aligned at rx + it.w - PAD_X.  Every "h <cm>"
             height goes here so a stack of heights reads as a clean
             right-aligned column and two merged vertices with
             different heights stay visually distinct.
     A line may have only left (the "W .. . E .." side distances),
     only right (a subsequent vertex's height with no name), or both
     (the first line of a pill, name on the left, height on the right). */

  const items = [];
  c.save();
  c.font = FONT;
  for (const grp of clusters) {
    let minIdx = Infinity;
    for (const m of grp) {
      const oi = orderIdx.get(m.aid);
      if (typeof oi === "number" && oi < minIdx) minIdx = oi;
    }
    const name = (minIdx !== Infinity) ? ("V" + (minIdx + 1)) : null;

    const lines = [];
    if (grp.length === 1) {
      const m = grp[0];
      lines.push({ left:  name || "", right: "h " + fmtCm(m.h) });
      lines.push({ left:  sideLineFor(m), right: null });
    } else {
      grp.sort((a, b) => {
        const sa = a.chunkMid < a.anchorCx ? 0 : 1;
        const sb = b.chunkMid < b.anchorCx ? 0 : 1;
        return sa - sb;
      });
      for (let gi = 0; gi < grp.length; gi++) {
        const m = grp[gi];
        lines.push({
          left:  (gi === 0 && name) ? name : "",
          right: "h " + fmtCm(m.h),
        });
        lines.push({ left: sideLineFor(m), right: null });
      }
    }

    let w = 0;
    for (const l of lines) {
      const lw = l.left  ? c.measureText(l.left).width  : 0;
      const rw = l.right ? c.measureText(l.right).width : 0;
      const gap = (l.left && l.right) ? LABEL_LEFT_RIGHT_GAP : 0;
      if (lw + gap + rw > w) w = lw + gap + rw;
    }

    items.push({
      anchorCx: grp[0].anchorCx,
      anchorCyRel: grp[0].anchorCyRel,
      lines,
      w: w + PAD_X * 2,
      h: lines.length * LINE_H + PAD_Y * 2,
      name,
      members: grp.map(x => x.aid),
    });
  }
  c.restore();

  /* Collinear-pill filter (checkbox-gated).

     A pill is suppressed only when all of the following hold:

       1. its members form a contiguous run in the ordered cable,
       2. the run has an anchor before it and an anchor after it,
       3. the pill's position lies on the line through those two
          neighbours' positions,
       4. the wall segments the members sit on have comparable lengths
          (ratio <= LEN_RATIO_MAX).

     Point (3) is measured in *strip-mm*, not rendered pixels.  The
     renderer may stretch a chunk shorter than MIN_CHUNK_PX up to the
     minimum to keep it legible, which bends the px-space geometry of
     a vertex that is really straight.  An earlier revision compared
     wallAttachToU() coordinates, which are NATIVE strip-u and so
     disagree with the on-screen direction whenever ch.mirror = true.
     What we want is neither: it is the anchor's position along the
     chunk's *visual* direction, in millimetres.

     Anchors themselves are never removed from `orderedIds`, so the
     dots at V2 / V5 / V11 / ... keep rendering.  Only the pill
     descriptor is dropped. */
  if (window.filterCollinearVerticesInStrip && Array.isArray(orderedIds)) {
    const oi = new Map();
    orderedIds.forEach((id, i) => oi.set(id, i));

    /* Visual strip position (u_mm, v_mm) of every wall-edge anchor. */
    const anchorMm = new Map();
    const segOfAid = new Map();
    {
      const byRun = new Map();
      for (const ch of chunks) {
        let list = byRun.get(ch.runIdx);
        if (!list) { list = []; byRun.set(ch.runIdx, list); }
        list.push(ch);
      }
      for (const [, chs] of byRun) {
        let cumU = 0;
        for (const ch of chs) {
          for (const a of ch.anchors) {
            const anc = anchors.get(a.aid);
            if (!anc || anc.space !== "wall-edge") continue;
            const uInChunk = ch.mirror
              ? (1 - anc.t) * ch.len
              : anc.t * ch.len;
            anchorMm.set(a.aid, { u: cumU + uInChunk, v: anc.v || 0 });
            segOfAid.set(a.aid, ch.segIdx);
          }
          cumU += ch.len;
        }
      }
    }

    const TOL_MM        = 2.0;   /* 2 mm of perpendicular slop */
    const LEN_RATIO_MAX = 3.0;   /* "these walls are comparable" */
    const kept = [];

    for (const it of items) {
      const idxs = it.members
        .map(id => oi.get(id))
        .filter(v => typeof v === "number")
        .sort((a, b) => a - b);

      if (idxs.length === 0) { kept.push(it); continue; }

      const lo = idxs[0];
      const hi = idxs[idxs.length - 1];

      /* Contiguous interior run only. */
      if (hi - lo + 1 !== idxs.length) { kept.push(it); continue; }
      if (lo === 0 || hi === orderedIds.length - 1) {
        kept.push(it); continue;
      }

      const bP = anchorMm.get(orderedIds[lo - 1]);
      const aP = anchorMm.get(orderedIds[hi + 1]);
      const cP = anchorMm.get(it.members[0]);
      if (!bP || !aP || !cP) { kept.push(it); continue; }

      /* Asymmetric-wall guard — real corners only.

         Keep a pill when its members sit on two walls that form a real
         geometric corner in plan view *and* whose lengths differ enough
         that the per-wall side-distance lines read very differently.  The
         V8 case (W12 = 720 mm meets W13 = 160 mm, at right angles) is the
         target: the two "W .. . E .." lines are measured along walls an
         order of magnitude apart, so the pill is worth keeping even if
         the vertex is straight in the strip.

         The V16 case must NOT fire the guard: W7 = 170 mm and W1 = 3060 mm
         share the endpoint (0, 3060) and run along the same line at x = 0.
         They are two collinear segments of one physical wall, split by the
         room model.  A cable passing their joint is running along a
         straight wall; the pill carries no extra information and should be
         filtered whenever it is strip-collinear.

         The direction test below distinguishes the two.  Two wall
         directions are "not collinear" when the absolute cosine of the
         angle between them drops under CORNER_COS_MIN — i.e. the walls
         diverge by more than about 10°.  Anti-parallel walls (cos ≈ 1) are
         treated as collinear, because a wall reversed end-to-end is still
         the same line. */
      const CORNER_COS_MIN = 0.985;
      const segs = new Set();
      for (const id of it.members) {
        const s = segOfAid.get(id);
        if (s != null) segs.add(s);
      }
      if (segs.size >= 2) {
        const segArr = Array.from(segs);
        const lens = segArr
          .map(si => (WALL.segments[si] && WALL.segments[si].len) || 0)
          .filter(l => l > 0);

        let realCorner = false;
        if (segArr.length >= 2) {
          const s0 = WALL.segments[segArr[0]];
          if (s0) {
            const d0x = s0.b[0] - s0.a[0], d0y = s0.b[1] - s0.a[1];
            const l0 = Math.hypot(d0x, d0y) || 1;
            for (let i = 1; i < segArr.length; i++) {
              const si = WALL.segments[segArr[i]];
              if (!si) continue;
              const dix = si.b[0] - si.a[0], diy = si.b[1] - si.a[1];
              const li = Math.hypot(dix, diy) || 1;
              const cos = Math.abs((d0x * dix + d0y * diy) / (l0 * li));
              if (cos < CORNER_COS_MIN) { realCorner = true; break; }
            }
          }
        }

        if (realCorner && lens.length >= 2) {
          const ratio = Math.max(...lens) / Math.min(...lens);
          if (ratio > LEN_RATIO_MAX) { kept.push(it); continue; }
        }
      }

      const dx1 = cP.u - bP.u, dy1 = cP.v - bP.v;
      const dx2 = aP.u - cP.u, dy2 = aP.v - cP.v;
      const len2 = Math.hypot(dx2, dy2);
      if (len2 < 0.5) { kept.push(it); continue; }

      const dist = Math.abs(dx1 * dy2 - dy1 * dx2) / len2;
      if (dist >= TOL_MM) kept.push(it);
    }

    items.length = 0;
    for (const it of kept) items.push(it);
  }

  const pillNameOf = new Map();
  for (const it of items) {
    if (it.name && it.members) {
      for (const aid of it.members) pillNameOf.set(aid, it.name);
    }
  }

  if (!items.length) {
    return {
      placed: [], maxTrack: -1, topPad: 0,
      trackOffsets: [], trackHeights: [],
      PAD_X, PAD_Y, LINE_H,
      pillNameOf,
    };
  }

  items.sort((a, b) => a.anchorCx - b.anchorCx);

  const tracks = [];

  const closestAllowedX = (target, minX, maxX, forbidden) => {
    if (minX > maxX) return null;
    const clipped = [];
    for (const [xLo, xHi] of forbidden) {
      const l = Math.max(xLo, minX);
      const r = Math.min(xHi, maxX);
      if (l <= r) clipped.push([l, r]);
    }
    clipped.sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const [xLo, xHi] of clipped) {
      if (merged.length && xLo <= merged[merged.length - 1][1] + 0.01) {
        merged[merged.length - 1][1] =
          Math.max(merged[merged.length - 1][1], xHi);
      } else {
        merged.push([xLo, xHi]);
      }
    }
    const allowed = [];
    let cur = minX;
    for (const [xLo, xHi] of merged) {
      if (xLo > cur + 0.01) allowed.push([cur, xLo]);
      cur = Math.max(cur, xHi);
    }
    if (cur < maxX - 0.01) allowed.push([cur, maxX]);
    if (!allowed.length) return null;
    let best = null, bestD = Infinity;
    for (const [xLo, xHi] of allowed) {
      const cand = target < xLo ? xLo : target > xHi ? xHi : target;
      const d = Math.abs(cand - target);
      if (d < bestD) { bestD = d; best = cand; }
    }
    return best;
  };

  const forbiddenFor = (it, track) => {
    const forbidden = [];
    const existingSame = (track < tracks.length) ? tracks[track] : [];
    for (const other of existingSame) {
      const half = (it.w + other.w) / 2 + TRACK_GAP_X;
      forbidden.push([other.pillCenterX - half,
                      other.pillCenterX + half]);
    }
    const nLower = Math.min(track, tracks.length);
    for (let tt = 0; tt < nLower; tt++) {
      for (const other of tracks[tt]) {
        const half = other.w / 2 + CROSS_MARGIN;
        forbidden.push([other.pillCenterX - half,
                        other.pillCenterX + half]);
      }
    }
    return forbidden;
  };

  for (const it of items) {
    const minX = AREA_X0 + it.w / 2;
    const maxX = AREA_X1 - it.w / 2;
    if (minX > maxX) continue;
    const target = clamp(it.anchorCx, minX, maxX);

    const maxT = (tracks.length < MAX_TRACKS)
      ? tracks.length
      : MAX_TRACKS - 1;

    let bestOption = null;
    for (let t = 0; t <= maxT; t++) {
      const forbidden = forbiddenFor(it, t);
      const x = closestAllowedX(target, minX, maxX, forbidden);
      if (x === null) continue;
      const disp  = Math.abs(x - target);
      const score = disp + t * TRACK_PENALTY;
      if (bestOption === null || score < bestOption.score - 0.01) {
        bestOption = { track: t, x, score, disp };
      }
    }

    if (bestOption === null) {
      if (tracks.length >= MAX_TRACKS) continue;
      bestOption = { track: tracks.length, x: target, score: 0, disp: 0 };
    }

    if (bestOption.track === tracks.length) tracks.push([]);
    it.track       = bestOption.track;
    it.pillCenterX = bestOption.x;
    it._baseDisp   = bestOption.disp;
    tracks[bestOption.track].push(it);
  }

  const validPlacement = (it, track, lowerTracks) => {
    if (it.pillCenterX - it.w / 2 < AREA_X0 - 0.5) return false;
    if (it.pillCenterX + it.w / 2 > AREA_X1 + 0.5) return false;
    for (const other of track) {
      if (other === it) continue;
      const half = (it.w + other.w) / 2 + TRACK_GAP_X;
      if (Math.abs(it.pillCenterX - other.pillCenterX) < half - 0.5)
        return false;
    }
    for (const other of lowerTracks) {
      const half = other.w / 2 + CROSS_MARGIN;
      if (Math.abs(it.pillCenterX - other.pillCenterX) < half - 0.5)
        return false;
    }
    return true;
  };

  for (let t = 0; t < tracks.length; t++) {
    const track = tracks[t];
    const lowerTracks = [];
    for (let tt = 0; tt < t; tt++)
      for (const o of tracks[tt]) lowerTracks.push(o);

    for (let pass = 0; pass < 30; pass++) {
      track.sort((a, b) => a.pillCenterX - b.pillCenterX);
      let anySwap = false;
      for (let i = 0; i < track.length - 1; i++) {
        const a = track[i];
        const b = track[i + 1];
        if (a.anchorCx <= b.anchorCx) continue;
        const ax = a.pillCenterX;
        const bx = b.pillCenterX;
        if (Math.abs(bx - a.anchorCx) > DISP_CAP_SWAP) continue;
        if (Math.abs(ax - b.anchorCx) > DISP_CAP_SWAP) continue;
        a.pillCenterX = bx;
        b.pillCenterX = ax;
        if (validPlacement(a, track, lowerTracks) &&
            validPlacement(b, track, lowerTracks)) {
          anySwap = true;
        } else {
          a.pillCenterX = ax;
          b.pillCenterX = bx;
        }
      }
      if (!anySwap) break;
    }
  }

  const trackHeights = new Array(tracks.length).fill(0);
  for (let t = 0; t < tracks.length; t++) {
    let maxH = 0;
    for (const it of tracks[t]) maxH = Math.max(maxH, it.h);
    trackHeights[t] = maxH + TRACK_V_GAP;
  }
  const trackOffsets = new Array(tracks.length).fill(0);
  let accOff = 0;
  for (let t = 0; t < tracks.length; t++) {
    trackOffsets[t] = accOff;
    accOff += trackHeights[t];
  }

  const placed = [];
  for (const arr of tracks) for (const it of arr) {
    if (it.track != null) placed.push(it);
  }

  const leaders = placed.map(it => ({
    it,
    anchorX: it.anchorCx,
    pillX:   it.pillCenterX,
    x0: Math.min(it.anchorCx, it.pillCenterX),
    x1: Math.max(it.anchorCx, it.pillCenterX),
  }));
  const sortedBySpan =
    [...leaders].sort((a, b) => (b.x1 - b.x0) - (a.x1 - a.x0));
  const slots = [];
  for (const L of sortedBySpan) {
    let assigned = null;
    for (const slot of slots) {
      let overlap = false;
      for (const [l, r] of slot.spans) {
        if (L.x0 < r + 4 && L.x1 > l - 4) { overlap = true; break; }
      }
      if (!overlap) {
        slot.spans.push([L.x0, L.x1]);
        assigned = slot;
        break;
      }
    }
    if (!assigned) {
      const relY = LABEL_CHANNEL_Y0 + slots.length * LABEL_CHANNEL_STEP;
      assigned = { relY, spans: [[L.x0, L.x1]] };
      slots.push(assigned);
    }
    L.it.channelYRel = assigned.relY;
  }
  const numChannels = slots.length;

  const topPad = numChannels === 0
    ? 0
    : LABEL_CHANNEL_Y0 + (numChannels - 1) * LABEL_CHANNEL_STEP
      + LABEL_CHANNEL_GAP;

  /* ---- Geometry optimisation runs FIRST.

     refineLeaderOffsets fans apart the near-anchor vertical legs of
     leaders that would otherwise overlap; optimizeLeaderGeometry then
     tunes each leader's dive mode, bevels and jogs against the full
     obstacle set.  Both steps change the polyline that gets drawn.

     The style conflict graph must therefore be built AFTER they run —
     it is a decision about the FINAL rendered polylines, not about the
     default mode-0 shapes.  Building it earlier left the graph stale as
     soon as the optimiser moved a leader: a switch to dive mode 2
     deletes the near-anchor vertical leg and inserts a horizontal leg
     at anchorY, so any conflict edge justified by the abandoned shape
     was spurious, and a leader whose only partner was such an edge
     came out dashed while looking completely isolated in the render. */

  refineLeaderOffsets(placed, topPad, trackOffsets, stripH);
  optimizeLeaderGeometry(placed, stripH, topPad, trackOffsets, boundaries,
                          wireSegments, wallEdges);

  /* ---- Style conflict graph — built from the FINAL paths. ---- */
  const conflictAdj = _buildStyleConflictGraph(placed, stripH, topPad,
                                                trackOffsets);

  /* ---- Style assignment, multi-pass ----

     Default: solid.  A leader is dashed only if it would otherwise
     run parallel-close to a leader that is already solid.  The
     minimum such set is a minimum vertex cover of the conflict graph.

     Solved per connected component:

       • Bipartite components (chains, trees — the common case) are
         two-coloured and the larger colour class is made solid.
         This is the exact optimum and yields clean alternation.

       • Non-bipartite components (odd cycles — e.g. three mutually
         parallel leaders) are seeded by a degree-greedy cover, then
         refined by two passes iterated to a fixed point:

           promote — any dashed vertex with no solid neighbour is
                     promoted back to solid.  The cover remains
                     valid (that vertex was covering nothing) and
                     the dash count drops.

           swap    — a dashed vertex v whose every solid neighbour
                     is also adjacent to some *other* dashed vertex
                     is promoted.  This is a plateau move: the dash
                     count drops by one and the cover stays valid,
                     because each formerly-solid-covered edge v–u
                     is still covered by u's other dashed neighbour.

         The two passes are run repeatedly; in practice they
         stabilise in two or three outer iterations. */

  const styleMap = new Map();
  const orderedForStyle =
    [...placed].sort((a, b) => a.anchorCx - b.anchorCx);

  const seen = new Set();
  for (const seed of orderedForStyle) {
    if (seen.has(seed)) continue;

    const comp = [];
    const queue = [seed];
    seen.add(seed);
    while (queue.length) {
      const u = queue.shift();
      comp.push(u);
      for (const v of conflictAdj.get(u) || []) {
        if (!seen.has(v)) { seen.add(v); queue.push(v); }
      }
    }

    if (comp.length === 1) {
      styleMap.set(comp[0], 0);
      continue;
    }

    /* --- Bipartite 2-colour. --- */
    let bipartite = true;
    const color = new Map([[comp[0], 0]]);
    const q2 = [comp[0]];
    while (q2.length && bipartite) {
      const u = q2.shift();
      const cu = color.get(u);
      for (const v of conflictAdj.get(u) || []) {
        if (!color.has(v)) { color.set(v, 1 - cu); q2.push(v); }
        else if (color.get(v) === cu) { bipartite = false; break; }
      }
    }

    if (bipartite) {
      let n0 = 0, n1 = 0;
      for (const u of comp) (color.get(u) === 0 ? n0++ : n1++);
      const solidColor = (n0 >= n1) ? 0 : 1;
      for (const u of comp) {
        styleMap.set(u, color.get(u) === solidColor ? 0 : 1);
      }
      continue;
    }

    /* --- Non-bipartite: greedy cover. --- */
    const dashed = new Set();
    {
      const removed = new Set();
      while (true) {
        let best = null, bestDeg = 0;
        for (const u of comp) {
          if (removed.has(u)) continue;
          let d = 0;
          for (const v of conflictAdj.get(u) || [])
            if (!removed.has(v)) d++;
          if (d > bestDeg) { bestDeg = d; best = u; }
        }
        if (best === null || bestDeg === 0) break;
        dashed.add(best);
        removed.add(best);
      }
    }

    /* --- Refinement passes to a fixed point. --- */
    for (let outer = 0; outer < 30; outer++) {
      let changed = false;

      /* promote: dashed with no solid neighbour → solid */
      for (const v of Array.from(dashed)) {
        let hasSolid = false;
        for (const u of conflictAdj.get(v) || []) {
          if (!dashed.has(u)) { hasSolid = true; break; }
        }
        if (!hasSolid) { dashed.delete(v); changed = true; }
      }

      /* swap: dashed whose every solid neighbour is also adjacent to
         some *other* dashed vertex → promote. */
      for (const v of Array.from(dashed)) {
        let ok = true;
        for (const u of conflictAdj.get(v) || []) {
          if (dashed.has(u)) continue;
          let stillCovered = false;
          for (const w of conflictAdj.get(u) || []) {
            if (w !== v && dashed.has(w)) { stillCovered = true; break; }
          }
          if (!stillCovered) { ok = false; break; }
        }
        if (ok) { dashed.delete(v); changed = true; }
      }

      if (!changed) break;
    }

    for (const u of comp) styleMap.set(u, dashed.has(u) ? 1 : 0);
  }

  for (const it of placed) it.leaderStyle = styleMap.get(it) ?? 0;

  return {
    placed,
    maxTrack: tracks.length - 1,
    topPad,
    trackOffsets,
    trackHeights,
    PAD_X, PAD_Y, LINE_H,
    pillNameOf,
  };
}

/* ---- Vertex labels ---- */

function drawVertexLabels(c, placement, stripY, stripH) {
  if (!placement.placed.length) return;
  const { placed, PAD_X, PAD_Y, LINE_H, topPad, trackOffsets } = placement;
  const stripBottom = stripY + stripH;

  const pillTopYFor = (it) =>
    stripBottom + topPad + trackOffsets[it.track];

  for (const it of placed) {
    const pillTopY = pillTopYFor(it);
    const rx = it.pillCenterX - it.w / 2;
    const ry = pillTopY;

    c.fillStyle = "#ffffff";
    c.fillRect(rx, ry, it.w, it.h);
    c.strokeStyle = "#000000";
    c.lineWidth = 1.2;
    c.strokeRect(rx + 0.5, ry + 0.5, it.w - 1, it.h - 1);

    c.fillStyle = "#000000";
    c.textBaseline = "middle";
    let ty = ry + PAD_Y + LINE_H / 2;
    for (const line of it.lines) {
      if (line.left) {
        c.textAlign = "left";
        c.fillText(line.left, rx + PAD_X, ty);
      }
      if (line.right) {
        c.textAlign = "right";
        c.fillText(line.right, rx + it.w - PAD_X, ty);
      }
      ty += LINE_H;
    }
  }

  c.save();
  c.strokeStyle = "#000000";
  c.lineWidth = 1.4;
  c.lineJoin = "round";
  c.lineCap = "butt";
  for (const it of placed) {
    if (it.channelYRel == null) continue;
    const anchorX  = it.anchorCx;
    const anchorY  = stripY + it.anchorCyRel;
    const pillX    = it.pillCenterX;
    const chanY    = stripBottom + it.channelYRel;
    const pillTopY = pillTopYFor(it);
    const offA     = it.offsetA || 0;

    const basePath = computeLeaderPath(
      anchorX, anchorY, offA, chanY, pillX, pillTopY,
      it, placed, stripBottom, topPad, trackOffsets, it.diveMode || 0);

    const bevelled = _bevelPath(basePath, it.bevel0 || 0, it.bevel1 || 0);
    const path = _applyJogsToPath(bevelled, it);

    const style = LEADER_STYLES[it.leaderStyle % LEADER_STYLES.length];
    c.setLineDash(style.dash || []);
    c.beginPath();
    c.moveTo(path[0][0], path[0][1]);
    for (let i = 1; i < path.length; i++) {
      c.lineTo(path[i][0], path[i][1]);
    }
    c.stroke();
  }
  c.setLineDash([]);
  c.restore();
}

/* ---- Crop and rasterise ---- */

function cropCanvasToContent(srcCanvas, pad) {
  const w = srcCanvas.width, h = srcCanvas.height;
  const srcCtx = srcCanvas.getContext("2d");
  const data = srcCtx.getImageData(0, 0, w, h).data;

  let minX = w, minY = h, maxX = -1, maxY = -1;
  for (let y = 0; y < h; y++) {
    const row = y * w * 4;
    for (let x = 0; x < w; x++) {
      const i = row + x * 4;
      if (data[i] < 250 || data[i + 1] < 250 || data[i + 2] < 250) {
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (maxX < 0) return srcCanvas;

  minX = Math.max(0, minX - pad);
  minY = Math.max(0, minY - pad);
  maxX = Math.min(w - 1, maxX + pad);
  maxY = Math.min(h - 1, maxY + pad);

  const cw = maxX - minX + 1;
  const ch = maxY - minY + 1;
  const out = document.createElement("canvas");
  out.width  = cw;
  out.height = ch;
  out.getContext("2d").drawImage(srcCanvas, minX, minY, cw, ch, 0, 0, cw, ch);
  return out;
}

function scaleCanvasToWidth(srcCanvas, targetW) {
  const w = srcCanvas.width, h = srcCanvas.height;
  if (Math.abs(w - targetW) / targetW < 0.10) return srcCanvas;
  const scale = targetW / w;
  const out = document.createElement("canvas");
  out.width  = Math.round(w * scale);
  out.height = Math.round(h * scale);
  const octx = out.getContext("2d");
  octx.imageSmoothingEnabled = true;
  octx.imageSmoothingQuality = "high";
  octx.drawImage(srcCanvas, 0, 0, out.width, out.height);
  return out;
}

/* ---- Main renderer ---- */

function renderCableRunToCanvas(tcId) {
  const orderedIds = _collapseStripCollinear(linearizeTrueCable(tcId));
  if (orderedIds.length < 1) return null;

  const segDirectory = buildSegmentDirectory(orderedIds);
  const hasWalls     = segDirectory.size > 0;

  const runs     = groupCableRuns(orderedIds);
  const wallRuns = runs.filter(r => r.space === "wall-edge");
  const planRuns = groupPlanRuns(orderedIds);

  const wallLayouts  = wallRuns.map(r => layoutWallRun(r.ids));
  const stripTotalMM = wallLayouts.reduce((a, l) => a + l.totalLen, 0);
  const stripActive  = stripTotalMM > 0;
  const planActive   = planRuns.length > 0;

  const fb = GEOMETRY.bounds;
  const fw = Math.max(fb.maxX - fb.minX, 1);
  const fh = Math.max(fb.maxY - fb.minY, 1);

  const IMG_W         = 1000;
  const MARGIN        = 20;
  const TITLE_H       = 44;
  const STRIP_HEADER_H = 28;
  const STRIP_FIXED_H = 130;
  const STRIP_AXIS_W  = 52;
  const STRIP_AXIS_GAP = 8;
  const ROW_GAP_AFTER_STRIP = 20;
  const INFO_W        = 240;
  const INFO_PLAN_GAP = 26;
  const BADGE_COL_W   = 30;
  const BADGE_GAP     = 44;
  const PLAN_MAX_H    = 440;
  const RUN_GAP       = 24;
  const MIN_CHUNK_PX  = 44;
  const BADGE_STEP    = 30;

  const availW        = IMG_W - 2 * MARGIN;
  const badgeR        = 12;

  const stripAreaX0  = MARGIN + STRIP_AXIS_W + STRIP_AXIS_GAP;
  const stripAreaX1  = IMG_W - MARGIN;
  const stripAreaW   = stripAreaX1 - stripAreaX0;

  const chunkWidthsPx = [];
  let stripScale = 0, stripW = 0;
  if (stripActive) {
    const nRuns  = wallLayouts.length;
    const gapsW  = RUN_GAP * Math.max(0, nRuns - 1);
    const usable = Math.max(1, stripAreaW - gapsW);

    const lengths = [];
    for (const layout of wallLayouts)
      for (const ch of layout.chunks) lengths.push(ch.len);
    const nChunks = lengths.length;
    const sumLen  = lengths.reduce((a, b) => a + b, 0) || 1;

    if (nChunks * MIN_CHUNK_PX >= usable) {
      stripScale = usable / sumLen;
      for (const L of lengths) chunkWidthsPx.push(L * stripScale);
    } else {
      let lo = 0;
      let hi = usable / sumLen;
      for (let iter = 0; iter < 40; iter++) {
        const mid = (lo + hi) / 2;
        let total = 0;
        for (const L of lengths) total += Math.max(L * mid, MIN_CHUNK_PX);
        if (total > usable) hi = mid; else lo = mid;
      }
      stripScale = (lo + hi) / 2;
      for (const L of lengths) {
        chunkWidthsPx.push(Math.max(L * stripScale, MIN_CHUNK_PX));
      }
    }
    stripW = chunkWidthsPx.reduce((a, b) => a + b, 0) + gapsW;
  }

  const stripOffsetX = stripActive
    ? stripAreaX0 + (stripAreaW - stripW) / 2
    : 0;

  const chunks = [];
  if (stripActive) {
    let xAbs = 0;
    let chunkIdx = 0;
    for (let ri = 0; ri < wallLayouts.length; ri++) {
      const layout = wallLayouts[ri];
      for (const ch of layout.chunks) {
        const wPx = chunkWidthsPx[chunkIdx++];
        const localScale = wPx / ch.len;
        const x0 = xAbs;
        const x1 = xAbs + wPx;
        chunks.push({
          runIdx: ri, segIdx: ch.segIdx,
          x0, x1,
          mirror: ch.mirror,
          len:    ch.len,
          z_lo:   ch.z_lo,
          z_hi:   ch.z_hi,
          kind:   ch.kind,
          anchors: ch.anchors.map(a => {
            const visualU = ch.mirror ? (ch.len - a.localU) : a.localU;
            return {
              aid: a.aid,
              xPx: x0 + visualU * localScale,
              yPx: (1 - a.v / WALL_HEIGHT) * STRIP_FIXED_H,
              nativeU: a.localU,
            };
          }),
        });
        xAbs = x1;
      }
      xAbs += RUN_GAP;
    }
  }

  let labelPlacement = { placed: [], maxTrack: -1, topPad: 0,
                         trackOffsets: [], trackHeights: [],
                         pillNameOf: new Map() };
  if (stripActive) {
    const measureCtx = document.createElement("canvas").getContext("2d");
    labelPlacement = computeVertexLabelPlacement(
      measureCtx, chunks, stripOffsetX, stripAreaX0, stripAreaW, STRIP_FIXED_H,
      orderedIds);
  }
  const totalTrackH = labelPlacement.trackHeights
    ? labelPlacement.trackHeights.reduce((a, b) => a + b, 0)
    : 0;
  const labelBandH = labelPlacement.maxTrack < 0
    ? 0
    : labelPlacement.topPad + totalTrackH + LABEL_BOTTOM_PAD;

  const planRoomAvailW = availW - INFO_W - INFO_PLAN_GAP - BADGE_GAP - BADGE_COL_W;
  let planScale = 0, planW = 0, planH = 0;
  if (planActive) {
    planScale = Math.min(planRoomAvailW / fw, PLAN_MAX_H / fh);
    planW = fw * planScale;
    planH = fh * planScale;
  }

  const INFO_HEADER_H = 22;
  const INFO_ROW_H    = 30;
  const INFO_GAP      = 16;
  const WALL_ROW_H    = 28;
  const nWalls        = segDirectory.size;

  const BREAKDOWN_H   = INFO_HEADER_H + 3 * INFO_ROW_H;
  const LEGEND_H      = INFO_HEADER_H + 5 * INFO_ROW_H;
  const WALLS_H       = hasWalls
    ? INFO_HEADER_H + nWalls * WALL_ROW_H
    : 0;

  /* The verbose hint paragraph that used to sit between the legend and
     the wall-segment list has been removed.  Its facts — pill format,
     west/east meaning, hatch legend — are either already on the page
     (the swatches) or belong in the JSON schema doc, not on every
     printed diagram.  Deleting it also fixes a bookkeeping mismatch:
     HINTS_H under-declared the paragraph's real drawn height by 84 px,
     so infoH was short, planRegionH was short, imgH was short, and the
     bottom of the canvas — the last wall-segment legend rows — was
     being clipped by cropCanvasToContent. */

  const infoH = BREAKDOWN_H
              + INFO_GAP
              + LEGEND_H
              + INFO_GAP
              + WALLS_H;

  const badgeStackH = nWalls > 0 ? (nWalls - 1) * BADGE_STEP + 2 * badgeR : 0;

  const planRegionH = Math.max(
    planH + 2 * badgeR + 24,
    infoH + 40,
    badgeStackH + 20
  );

  let y = MARGIN + TITLE_H;
  const stripHeaderY = y;
  if (stripActive) y += STRIP_HEADER_H;
  const stripY = y;
  if (stripActive) y += STRIP_FIXED_H;
  if (stripActive) y += labelBandH;
  if (stripActive) y += ROW_GAP_AFTER_STRIP;
  const planRegionY = y;
  if (planActive || hasWalls) y += planRegionH;
  const imgH = y + 2 * MARGIN;

  const DPR = 3;
  const cv = document.createElement("canvas");
  cv.width  = IMG_W * DPR;
  cv.height = imgH * DPR;
  const c = cv.getContext("2d");
  c.scale(DPR, DPR);
  c.fillStyle = "#ffffff";
  c.fillRect(0, 0, IMG_W, imgH);

  const fmtM = (mm) => (mm / 1000).toFixed(2) + " m";
  const totalLen = totalCableLength(orderedIds);
  const nParts = allCables().filter(x => trueCableIdOf(x) === tcId).length;

  c.font = "700 22px -apple-system, system-ui, sans-serif";
  c.fillStyle = "#000000";
  c.textAlign = "left"; c.textBaseline = "middle";
  c.fillText(T("cableTitle") + " " + tcId, MARGIN, MARGIN + TITLE_H / 2);

  c.font = "600 13px ui-monospace, monospace";
  c.fillStyle = "#333333";
  c.textAlign = "right";
  c.fillText(
    T("subtitle")(orderedIds.length, nParts, fmtM(totalLen)),
    IMG_W - MARGIN, MARGIN + TITLE_H / 2
  );
  c.textAlign = "left"; c.textBaseline = "top";

  const anchorStripPos = new Map();
  if (stripActive) {
    const yFloor  = stripY + STRIP_FIXED_H;
    const yCeil   = stripY;
    const voidX0  = stripOffsetX - 8;
    const voidX1  = stripOffsetX + stripW + 8;

    c.fillStyle = _pat(c, "void");
    c.fillRect(voidX0, yCeil, voidX1 - voidX0, yFloor - yCeil);

    c.save();
    c.strokeStyle = "rgba(0, 0, 0, 0.35)";
    c.lineWidth = 1;
    c.setLineDash([4, 4]);
    c.beginPath();
    c.moveTo(voidX0, yCeil + 0.5); c.lineTo(voidX1, yCeil + 0.5);
    c.moveTo(voidX0, yFloor - 0.5); c.lineTo(voidX1, yFloor - 0.5);
    c.stroke();
    c.setLineDash([]);
    c.restore();

    for (const ch of chunks) {
      const rx   = stripOffsetX + ch.x0;
      const rw   = ch.x1 - ch.x0;
      const yTop = stripY + (1 - ch.z_hi / WALL_HEIGHT) * STRIP_FIXED_H;
      const yBot = stripY + (1 - ch.z_lo / WALL_HEIGHT) * STRIP_FIXED_H;
      const rh   = Math.max(1, yBot - yTop);

      const isStep = (ch.kind === "step");
      c.fillStyle = isStep ? _pat(c, "step") : "#ffffff";
      c.fillRect(rx, yTop, rw, rh);
      c.strokeStyle = "#000000";
      c.lineWidth = isStep ? 1.2 : 0.8;
      c.strokeRect(rx + 0.5, yTop + 0.5, rw - 1, rh - 1);
    }

    const stripRuns = [];
    for (let ri = 0; ri < wallLayouts.length; ri++) {
      const lineAnchors = [];
      for (const ch of chunks) {
        if (ch.runIdx !== ri) continue;
        for (const a of ch.anchors) lineAnchors.push(a);
      }
      for (const a of lineAnchors) {
        anchorStripPos.set(a.aid, [stripOffsetX + a.xPx, stripY + a.yPx]);
      }
      stripRuns.push(lineAnchors);
    }

    const _strokeRun = (lineAnchors) => {
      if (lineAnchors.length < 2) return;
      c.beginPath();
      c.moveTo(stripOffsetX + lineAnchors[0].xPx,
               stripY      + lineAnchors[0].yPx);
      for (let i = 1; i < lineAnchors.length; i++) {
        c.lineTo(stripOffsetX + lineAnchors[i].xPx,
                 stripY      + lineAnchors[i].yPx);
      }
      c.stroke();
    };

    c.save();
    c.lineCap = "round"; c.lineJoin = "round";

    c.strokeStyle = "#ffffff";
    c.lineWidth = 7;
    for (const run of stripRuns) _strokeRun(run);

    c.strokeStyle = "#000000";
    c.lineWidth = 3.2;
    for (const run of stripRuns) _strokeRun(run);

    c.fillStyle = "#000000";
    for (const run of stripRuns) {
      if (run.length !== 1) continue;
      c.beginPath();
      c.arc(stripOffsetX + run[0].xPx, stripY + run[0].yPx, 5, 0,
            Math.PI * 2);
      c.fill();
    }
    c.restore();

    for (const ch of chunks) {
      const entry = segDirectory.get(ch.segIdx);
      if (!entry) continue;
      const cx = stripOffsetX + (ch.x0 + ch.x1) / 2;
      const by = stripHeaderY + STRIP_HEADER_H / 2;
      c.strokeStyle = "#000000";
      c.lineWidth = 1;
      c.beginPath();
      c.moveTo(cx, by + badgeRadiusFor(entry.order) + 1);
      c.lineTo(cx, stripY);
      c.stroke();
      drawBadge(c, cx, by, entry.order);
    }

    drawVertexLabels(c, labelPlacement, stripY, STRIP_FIXED_H);

    c.font = "700 18px sans-serif";
    c.fillStyle = "#333333";
    c.textAlign = "center"; c.textBaseline = "middle";
    for (let ri = 0; ri < wallLayouts.length - 1; ri++) {
      let lastX1 = 0;
      for (const ch of chunks) if (ch.runIdx === ri) lastX1 = ch.x1;
      const xMid = stripOffsetX + lastX1 + RUN_GAP / 2;
      const yMid = stripY + STRIP_FIXED_H / 2;
      c.fillText("...", xMid, yMid);
    }
    c.textAlign = "left"; c.textBaseline = "top";

    const axisRight = stripOffsetX - STRIP_AXIS_GAP;
    const axisLeft  = axisRight - STRIP_AXIS_W;
    const ticks = [
      { v: 0,             label: "0" },
      { v: WALL_HEIGHT/2, label: String(Math.round(WALL_HEIGHT / 2 / 10)) },
      { v: WALL_HEIGHT,   label: String(Math.round(WALL_HEIGHT / 10)) },
    ];
    c.save();
    c.strokeStyle = "#000000";
    c.fillStyle = "#000000";
    c.lineWidth = 1;
    c.font = "600 11px ui-monospace, monospace";
    c.textAlign = "right";
    c.textBaseline = "middle";
    for (const t of ticks) {
      const ty = stripY + (1 - t.v / WALL_HEIGHT) * STRIP_FIXED_H;
      c.beginPath();
      c.moveTo(axisRight - 6, ty);
      c.lineTo(axisRight, ty);
      c.stroke();
      c.fillText(t.label, axisRight - 9, ty);
    }
    c.strokeStyle = "#666666";
    c.beginPath();
    c.moveTo(axisRight + 0.5, stripY);
    c.lineTo(axisRight + 0.5, stripY + STRIP_FIXED_H);
    c.stroke();
    c.save();
    c.translate(axisLeft + 4, stripY + STRIP_FIXED_H / 2);
    c.rotate(-Math.PI / 2);
    c.textAlign = "center"; c.textBaseline = "middle";
    c.font = "600 10px ui-monospace, monospace";
    c.fillStyle = "#333333";
    c.fillText(T("heightAxis"), 0, 0);
    c.restore();
    c.restore();
  }

  const anchorPlanPos = new Map();
  if (planActive || hasWalls) {
    let infoX, planOriginX, badgeColX;

    if (planActive) {
      const totalRowW = INFO_W + INFO_PLAN_GAP + planW + BADGE_GAP + BADGE_COL_W;
      const rowX0 = (IMG_W - totalRowW) / 2;
      infoX       = rowX0;
      planOriginX = infoX + INFO_W + INFO_PLAN_GAP;
      badgeColX   = planOriginX + planW + BADGE_GAP + BADGE_COL_W / 2;
    } else {
      infoX       = (IMG_W - INFO_W) / 2;
      planOriginX = 0;
      badgeColX   = 0;
    }

    const infoY       = planRegionY + 20;
    const planOriginY = planRegionY + (planRegionH - planH) / 2;

    if (planActive) {
      const f2p = (px, py) => [
        planOriginX + (px - fb.minX) * planScale,
        planOriginY + (fb.maxY - py) * planScale,
      ];

      for (const face of GEOMETRY.faces) {
        const outer = face.outer;
        if (!outer || !outer.length) continue;
        c.beginPath();
        let [sx, sy] = f2p(outer[0][0], outer[0][1]);
        c.moveTo(sx, sy);
        for (let i = 1; i < outer.length; i++) {
          const [x, y] = f2p(outer[i][0], outer[i][1]);
          c.lineTo(x, y);
        }
        c.closePath();
        for (const h of face.holes) {
          if (!h || !h.length) continue;
          let [x0, y0] = f2p(h[0][0], h[0][1]);
          c.moveTo(x0, y0);
          for (let i = 1; i < h.length; i++) {
            const [x, y] = f2p(h[i][0], h[i][1]);
            c.lineTo(x, y);
          }
          c.closePath();
        }
        c.fillStyle = _pat(c, "roomInterior");
        c.fill("evenodd");
        c.strokeStyle = "#000000";
        c.lineWidth = 0.8;
        c.stroke();
      }

      for (const [segIdx] of segDirectory) {
        const seg = WALL.segments[segIdx];
        if (!seg) continue;
        const [ax, ay] = f2p(seg.a[0], seg.a[1]);
        const [bx, by] = f2p(seg.b[0], seg.b[1]);
        c.strokeStyle = "#000000";
        c.lineWidth = 7;
        c.lineCap = "round";
        c.beginPath();
        c.moveTo(ax, ay);
        c.lineTo(bx, by);
        c.stroke();
      }

      const planPts = [];
      for (const run of planRuns) {
        const pts = [];
        for (const id of run.ids) {
          const a = anchors.get(id);
          if (!a) continue;
          let px, py;
          if (a.space === "floor") { px = a.x; py = a.y; }
          else { const p = wallAttachToPlan(a.segIdx, a.t); px = p[0]; py = p[1]; }
          const [cx, cy] = f2p(px, py);
          pts.push([cx, cy]);
          anchorPlanPos.set(id, [cx, cy]);
        }
        planPts.push(pts);
      }

      const _strokePlan = (pts) => {
        if (pts.length < 2) return;
        c.beginPath();
        c.moveTo(pts[0][0], pts[0][1]);
        for (let i = 1; i < pts.length; i++) c.lineTo(pts[i][0], pts[i][1]);
        c.stroke();
      };

      c.save();
      c.lineCap = "round"; c.lineJoin = "round";

      c.strokeStyle = "#ffffff";
      c.lineWidth = 7;
      for (const pts of planPts) _strokePlan(pts);

      c.strokeStyle = "#000000";
      c.lineWidth = 3.2;
      for (const pts of planPts) _strokePlan(pts);

      c.fillStyle = "#000000";
      for (const pts of planPts) {
        if (pts.length !== 1) continue;
        c.beginPath();
        c.arc(pts[0][0], pts[0][1], 5, 0, Math.PI * 2);
        c.fill();
      }
      c.restore();

      for (const [, [px, py]] of anchorPlanPos) {
        c.beginPath();
        c.arc(px, py, 4, 0, Math.PI * 2);
        c.fillStyle = "#ffffff"; c.fill();
        c.strokeStyle = "#000000"; c.lineWidth = 1.8; c.stroke();
      }

      if (hasWalls) {
        const arrows = [];
        for (const [segIdx, entry] of segDirectory) {
          const seg = WALL.segments[segIdx];
          if (!seg) continue;
          const [ax, ay] = f2p(seg.a[0], seg.a[1]);
          const [bx, by] = f2p(seg.b[0], seg.b[1]);
          const wmx = (ax + bx) / 2;
          const wmy = (ay + by) / 2;
          arrows.push({
            order:    entry.order,
            wallMidX: wmx,
            wallMidY: wmy,
            style:    styleFor(entry.order),
            badgeX:   badgeColX,
            badgeY:   wmy,
          });
        }
        arrows.sort((a, b) => a.badgeY - b.badgeY);

        const MIN_GAP = 2 * badgeR + 6;
        for (let pass = 0; pass < 8; pass++) {
          let moved = false;
          for (let i = 1; i < arrows.length; i++) {
            const gap = arrows[i].badgeY - arrows[i-1].badgeY;
            if (gap < MIN_GAP) {
              const push = (MIN_GAP - gap) / 2 + 0.1;
              arrows[i-1].badgeY -= push;
              arrows[i].badgeY   += push;
              moved = true;
            }
          }
          if (!moved) break;
        }

        const topB = planRegionY + badgeR + 6;
        const botB = planRegionY + planRegionH - badgeR - 6;
        if (arrows.length) {
          const minY = Math.min(...arrows.map(a => a.badgeY));
          const maxY = Math.max(...arrows.map(a => a.badgeY));
          let shift = 0;
          if (minY < topB) shift = topB - minY;
          else if (maxY > botB) shift = botB - maxY;
          if (shift) for (const a of arrows) a.badgeY += shift;
        }

        for (const a of arrows) {
          const startX = a.badgeX - badgeR - 1;
          const path = [
            [startX, a.badgeY],
            [a.wallMidX, a.wallMidY],
          ];
          drawStyledPath(c, path, a.style, 0);
        }
        for (const a of arrows) {
          drawBadge(c, a.badgeX, a.badgeY, a.order);
        }
      }
    }

    {
      let infoCurY = infoY;
      const infoX0 = infoX;

      c.font = "700 14px -apple-system, system-ui, sans-serif";
      c.fillStyle = "#000000";
      c.textAlign = "left"; c.textBaseline = "top";
      c.fillText(T("lengthBreakdown"), infoX0, infoCurY);
      infoCurY += INFO_HEADER_H;

      const parts = allCables().filter(x => trueCableIdOf(x) === tcId);
      let wallLen = 0, floorLen = 0;
      for (let i = 0; i < orderedIds.length - 1; i++) {
        const aid = orderedIds[i], bid = orderedIds[i + 1];
        const p = computeAnchor3D(anchors.get(aid));
        const q = computeAnchor3D(anchors.get(bid));
        if (!p || !q) continue;
        const d = Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
        if (d < 1e-3) continue;
        const kind = segmentKind(aid, bid, parts);
        if (kind === "floor")      floorLen += d;
        else if (kind === "wall")  wallLen += d;
      }
      const rows = [];
      if (wallLen  > 0.5) rows.push([T("wallRuns"),  fmtM(wallLen)]);
      if (floorLen > 0.5) rows.push([T("floorRuns"), fmtM(floorLen)]);
      rows.push([T("totalRow"), fmtM(totalLen)]);

      c.font = "500 14px ui-monospace, monospace";
      c.fillStyle = "#000000";

      /* Two-column table: label on the left, value on the right.
         The value column x is measured from the widest label in the
         table, not hard-coded — the old fixed +110 px offset was
         tuned for English and Spanish ("Tramos de pared") overran it,
         printing over the value.  A minimum keeps the English layout
         from collapsing when every label happens to be short. */
      const LABEL_MIN_COL_W = 96;
      const VALUE_GUTTER_PX = 10;
      let labelColW = LABEL_MIN_COL_W;
      for (const [label] of rows) {
        const w = c.measureText(label).width;
        if (w > labelColW) labelColW = w;
      }
      const valueColX = infoX0 + 4 + labelColW + VALUE_GUTTER_PX;

      for (const [label, val] of rows) {
        c.fillText(label, infoX0 + 4, infoCurY);
        c.fillText(val,   valueColX,  infoCurY);
        infoCurY += INFO_ROW_H;
      }
      infoCurY += INFO_GAP;

      c.font = "700 14px -apple-system, system-ui, sans-serif";
      c.fillStyle = "#000000";
      c.fillText(T("legend"), infoX0, infoCurY);
      infoCurY += INFO_HEADER_H;

      const _swatch = (label, h, draw) => {
        const swX = infoX0 + 4;
        const swY = infoCurY + 8;
        const swW = 28;
        c.save();
        c.strokeStyle = "#000000";
        c.fillStyle   = "#000000";
        c.lineCap     = "round";
        c.lineJoin    = "round";
        draw(swX, swY, swW, h);
        c.restore();
        c.fillStyle = "#000000";
        c.font = "500 14px -apple-system, system-ui, sans-serif";
        c.textAlign = "left"; c.textBaseline = "top";
        c.fillText(label, swX + swW + 8, infoCurY);
        infoCurY += INFO_ROW_H;
      };

      _swatch(T("legendCable"), 9, (x, y, w) => {
        c.lineWidth = 3.2;
        c.beginPath(); c.moveTo(x, y); c.lineTo(x + w, y); c.stroke();
      });
      _swatch(T("legendWallFloorLink"), 9, (x, y, w) => {
        c.lineWidth = 1.4;
        c.setLineDash([2, 3]);
        c.beginPath(); c.moveTo(x, y); c.lineTo(x + w, y); c.stroke();
        c.setLineDash([]);
      });
      _swatch(T("legendTraversedWall"), 9, (x, y, w) => {
        c.lineWidth = 6;
        c.beginPath(); c.moveTo(x + 3, y); c.lineTo(x + w - 3, y); c.stroke();
      });
      _swatch(T("legendStepFace"), 11, (x, y, w, h) => {
        c.fillStyle = _pat(c, "step");
        c.fillRect(x, y - h / 2, w, h);
        c.strokeStyle = "#000000";
        c.lineWidth = 0.8;
        c.strokeRect(x + 0.5, y - h / 2 + 0.5, w - 1, h - 1);
      });
      _swatch(T("legendVoid"), 11, (x, y, w, h) => {
        c.fillStyle = _pat(c, "void");
        c.fillRect(x, y - h / 2, w, h);
        c.strokeStyle = "rgba(0, 0, 0, 0.35)";
        c.lineWidth = 0.6;
        c.setLineDash([2, 2]);
        c.strokeRect(x + 0.5, y - h / 2 + 0.5, w - 1, h - 1);
        c.setLineDash([]);
      });

      infoCurY += INFO_GAP;

      if (hasWalls) {
        c.font = "700 14px -apple-system, system-ui, sans-serif";
        c.fillStyle = "#000000";
        c.textAlign = "left"; c.textBaseline = "top";
        c.fillText(T("wallSegmentsArrowStyle"), infoX0, infoCurY);
        infoCurY += INFO_HEADER_H;

        const arrowLen = 36;
        for (const [segIdx, entry] of segDirectory) {
          const r = badgeRadiusFor(entry.order);
          drawBadge(c, infoX0 + 4 + r, infoCurY + WALL_ROW_H / 2, entry.order);
          const ax0 = infoX0 + 4 + r * 2 + 6;
          drawArrowSample(c, ax0, infoCurY + WALL_ROW_H / 2, arrowLen,
                          styleFor(entry.order));
          c.font = "600 14px ui-monospace, monospace";
          c.fillStyle = "#000000";
          c.textAlign = "left"; c.textBaseline = "middle";
          c.fillText(entry.tag, ax0 + arrowLen + 8, infoCurY + WALL_ROW_H / 2);
          infoCurY += WALL_ROW_H;
        }
        c.textBaseline = "top";
      }
    }
  }

  c.setLineDash([2, 3]);
  c.strokeStyle = "#000000";
  c.lineWidth = 1.4;

  const drawDottedWire = (wp, fp) => {
    c.beginPath();
    c.moveTo(wp[0], wp[1]);
    c.lineTo(fp[0], fp[1]);
    c.stroke();
    c.setLineDash([]);

    /* White halo behind each node so it stays legible when the node
       lands on a wall bar (which is a 7 px solid black stroke). */
    c.fillStyle = "#ffffff";
    c.beginPath(); c.arc(wp[0], wp[1], 6.5, 0, Math.PI * 2); c.fill();
    c.beginPath(); c.arc(fp[0], fp[1], 6.5, 0, Math.PI * 2); c.fill();

    /* Black node on top. */
    c.fillStyle = "#000000";
    c.beginPath(); c.arc(wp[0], wp[1], 4, 0, Math.PI * 2); c.fill();
    c.beginPath(); c.arc(fp[0], fp[1], 4, 0, Math.PI * 2); c.fill();

    c.setLineDash([2, 3]);
  };

  for (const id of orderedIds) {
    const sp = anchorStripPos.get(id);
    const pp = anchorPlanPos.get(id);
    if (!sp || !pp) continue;
    drawDottedWire(sp, pp);
  }
  for (let i = 0; i < orderedIds.length - 1; i++) {
    const a = anchors.get(orderedIds[i]);
    const b = anchors.get(orderedIds[i + 1]);
    if (!a || !b) continue;
    const aE = a.space === "wall-edge" && Math.abs(a.v || 0) > 1e-3;
    const bE = b.space === "wall-edge" && Math.abs(b.v || 0) > 1e-3;
    if (aE && b.space === "floor") {
      const wp = anchorStripPos.get(a.id);
      const fp = anchorPlanPos.get(b.id);
      if (wp && fp) drawDottedWire(wp, fp);
    }
    if (bE && a.space === "floor") {
      const wp = anchorStripPos.get(b.id);
      const fp = anchorPlanPos.get(a.id);
      if (wp && fp) drawDottedWire(wp, fp);
    }
  }
  c.setLineDash([]);

  const cropped = cropCanvasToContent(cv, 14 * DPR);
  const canvas = scaleCanvasToWidth(cropped, 2200);

  const meta = buildCableRunMeta(tcId, orderedIds, segDirectory, chunks,
                                  labelPlacement.pillNameOf);

  return { canvas, meta };
}

/* ---- Preview page ---- */

function openExportPreview(images) {
  const w = window.open("", "_blank");
  if (!w) {
    flashStatus(T("popupBlocked"), "bad");
    return;
  }
  let html = `<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>${T("previewTitle")}</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif;
         margin: 0; padding: 24px; background: #f1f5f9; color: #000000; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  p.sub { margin: 0 0 16px; color: #333333; font-size: 13px; }
  .toolbar { margin: 0 0 24px; display: flex; gap: 10px;
             align-items: center; flex-wrap: wrap; }
  .toolbar button {
    padding: 8px 16px; cursor: pointer; border: 0;
    background: #000000; color: #ffffff; border-radius: 5px;
    font-size: 13px; font-weight: 600;
  }
  .toolbar button:hover { background: #333333; }
  .toolbar button.secondary { background: #555555; }
  .toolbar button.secondary:hover { background: #333333; }
  .toolbar button:disabled { background: #999999; cursor: default; }
  .toolbar label.toggle {
    display: inline-flex; align-items: center; gap: 6px;
    font-size: 13px; font-weight: 600; color: #000000;
    cursor: pointer; user-select: none;
    padding: 6px 10px; border: 1px solid #000000;
    border-radius: 5px; background: #ffffff;
  }
  .toolbar label.toggle:hover { background: #f0f0f0; }
  .toolbar .hint { color: #333333; font-size: 12px; }
  .printHint { background: #f0f0f0; border-left: 3px solid #000000;
               padding: 10px 14px; margin: 0 0 24px;
               font-size: 13px; color: #000000; border-radius: 0 4px 4px 0; }
  .card { background: #ffffff; border-radius: 8px; padding: 18px;
          margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
  .card h2 { font-size: 15px; margin: 0 0 4px;
             font-family: ui-monospace, monospace; color: #000000; }
  .card .meta { font-size: 12px; color: #333333; margin: 0 0 12px;
                font-family: ui-monospace, monospace; }
  .card img { max-width: 100%; height: auto; display: block;
              border: 1px solid #cccccc; border-radius: 4px; }
  .actions { margin-top: 12px; display: flex; gap: 8px; flex-wrap: wrap; }
  .actions a { display: inline-block; padding: 6px 14px;
               background: #000000; color: #ffffff; text-decoration: none;
               border-radius: 4px; font-size: 12px; font-weight: 600;
               cursor: pointer; }
  .actions a:hover { background: #333333; }
  .actions a.json { background: #555555; }
  .actions a.json:hover { background: #333333; }
</style></head><body>
<h1>${T("previewTitle")}</h1>
<p class="sub">${T("previewSubtitle")(images.length)}</p>
<div class="printHint">
  ${T("previewHint")}
</div>
<div class="toolbar">
  <button id="downloadAllBtn">${T("downloadAllPngs")(images.length)}</button>
  <button id="downloadAllJsonBtn" class="secondary">${T("downloadAllJson")(images.length)}</button>
  <label class="toggle" title="${T("filterCollinearTitle")}">
    <input type="checkbox" id="filterCollinear">
    ${T("filterCollinear")}
  </label>
  <span class="hint" id="dlHint"></span>
</div>`;

  for (const img of images) {
    let url;
    try { url = img.canvas.toDataURL("image/png"); }
    catch (e) { continue; }
    const wpx = img.canvas.width;
    const hpx = img.canvas.height;
    const mm  = Math.round(wpx * 25.4 / 300);

    const jsonText = JSON.stringify(img.meta, null, 2);
    const jsonUrl  = "data:application/json;charset=utf-8,"
                   + encodeURIComponent(jsonText);

    html += `
<div class="card" data-cable-id="${img.id}">
  <h2>${T("cableTitle")} ${img.id}</h2>
  <p class="meta">${T("cardSizeMeta")(wpx, hpx, mm)}</p>
  <img src="${url}" alt="${T("cardAlt")(img.id)}">
  <div class="actions">
    <a href="${url}"     download="cable_${img.id}.png">${T("downloadPng")}</a>
    <a href="${jsonUrl}" class="json" download="cable_${img.id}.json">${T("downloadJson")}</a>
  </div>
</div>`;
  }
  html += `</body></html>`;

  w.document.open();
  w.document.write(html);
  w.document.close();

  const doc = w.document;
  const allBtn     = doc.getElementById("downloadAllBtn");
  const allJsonBtn = doc.getElementById("downloadAllJsonBtn");
  const hint       = doc.getElementById("dlHint");
  const filterCb   = doc.getElementById("filterCollinear");

  if (allBtn) {
    allBtn.addEventListener("click", () => {
      allBtn.disabled = true;
      const delay = 180;
      for (let i = 0; i < images.length; i++) {
        const img = images[i];
        let url;
        try { url = img.canvas.toDataURL("image/png"); }
        catch (e) { continue; }
        w.setTimeout(() => {
          const a = doc.createElement("a");
          a.href = url;
          a.download = "cable_" + img.id + ".png";
          a.style.display = "none";
          doc.body.appendChild(a);
          a.click();
          doc.body.removeChild(a);
          if (i === images.length - 1) {
            hint.textContent = T("doneHint");
            allBtn.disabled = false;
          }
        }, i * delay);
      }
    });
  }

  if (allJsonBtn) {
    allJsonBtn.addEventListener("click", () => {
      const bundle = {
        schemaVersion: 6,
        generatedAt:   new Date().toISOString(),
        cables:        images.map(img => img.meta),
      };
      const text = JSON.stringify(bundle, null, 2);
      const url  = "data:application/json;charset=utf-8,"
                 + encodeURIComponent(text);
      const a = doc.createElement("a");
      a.href = url;
      a.download = "cable_runs.json";
      a.style.display = "none";
      doc.body.appendChild(a);
      a.click();
      doc.body.removeChild(a);
      hint.textContent = T("downloadedBundle");
    });
  }

  if (filterCb) {
    filterCb.checked = !!window.filterCollinearVerticesInStrip;
    filterCb.addEventListener("change", () => {
      window.filterCollinearVerticesInStrip = filterCb.checked;

      for (const tc of state.trueCables) {
        let result = null;
        try {
          result = renderCableRunToCanvas(tc.id);
        } catch (err) {
          console.error("re-render failed for cable " + tc.id, err);
          continue;
        }
        if (!result || !result.canvas) continue;

        const card = doc.querySelector(
          `[data-cable-id="${tc.id}"]`);
        if (!card) continue;

        const url = result.canvas.toDataURL("image/png");
        const imgEl = card.querySelector("img");
        if (imgEl) imgEl.src = url;

        const metaEl = card.querySelector(".meta");
        if (metaEl) {
          const wpx = result.canvas.width;
          const hpx = result.canvas.height;
          const mm  = Math.round(wpx * 25.4 / 300);
          metaEl.textContent = T("cardSizeMeta")(wpx, hpx, mm);
        }

        const links = card.querySelectorAll("a");
        if (links[0]) links[0].href = url;
        if (links[1]) {
          const jsonUrl = "data:application/json;charset=utf-8,"
                        + encodeURIComponent(
                            JSON.stringify(result.meta, null, 2));
          links[1].href = jsonUrl;
        }

        const stored = images.find(im => im.id === tc.id);
        if (stored) {
          stored.canvas = result.canvas;
          stored.meta   = result.meta;
        }
      }
    });
  }
}

/* ---- Button + entry point ---- */

(function installExportButton() {
  const hr = document.querySelector("#ui hr");
  if (!hr) return;
  const row = document.createElement("div");
  row.className = "row";
  const btn = document.createElement("button");
  btn.id = "exportBtn";
  btn.textContent = T("exportButton");
  btn.title       = T("exportButtonTitle");
  row.appendChild(btn);
  hr.parentNode.insertBefore(row, hr);
  btn.addEventListener("click", exportAllCableRuns);
})();

function exportAllCableRuns() {
  if (!state.trueCables.length) {
    flashStatus(T("noCablesToExport"), "warn");
    return;
  }
  const images = [];
  const failures = [];
  for (const tc of state.trueCables) {
    try {
      const result = renderCableRunToCanvas(tc.id);
      if (result && result.canvas) {
        images.push({ id: tc.id, canvas: result.canvas, meta: result.meta });
      } else {
        failures.push(tc.id);
      }
    } catch (err) {
      failures.push(tc.id);
      console.error("renderCableRunToCanvas failed for cable " + tc.id, err);
    }
  }
  if (!images.length) {
    flashStatus(T("nothingToExport")(failures.length), "bad");
    return;
  }
  openExportPreview(images);
  const msg = failures.length
    ? T("exportedWithFailures")(images.length, failures.length)
    : T("exportedOk")(images.length);
  flashStatus(msg, failures.length ? "warn" : "ok");
}
"""
