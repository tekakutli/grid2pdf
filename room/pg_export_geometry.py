"""
pg_export_geometry.py — the pure distance and segment-pair atoms.

The mathematical primitives every other module sits on:

    SEG_MIN_SEP / SEG_MIN_LEN / PARALLEL_TOL   the three constants
                                               every distance test
                                               uses
    PILL_PROX                                   the minimum clearance
                                               a leader segment must
                                               keep from a foreign
                                               pill box.  Equal to
                                               SEG_MIN_SEP: the same
                                               "too close to matter"
                                               threshold that governs
                                               segment-vs-segment
                                               conflicts, applied to
                                               the segment-vs-pill
                                               case
    _pointSegDist / _segSegDist                the two distance atoms
    _pathSegments                              a polyline → segment
                                               records with bounding
                                               boxes, kind, and angle
    _angleDiff                                 the acute angle
    _findSegmentConflicts                      the O(n²) sweep with
                                               bbox pre-reject
    _segBoxOverlap                             segment vs. AABB, via
                                               Liang-Barsky

Two behavioural notes that follow from the constants:

    PILL_PROX == SEG_MIN_SEP
        A leader segment within PILL_PROX of a foreign pill box
        counts as a conflict, exactly as a segment within
        SEG_MIN_SEP of another segment does.  Earlier revisions used
        the exact box (PILL_PROX = 0), which meant a descent column
        2.4 px from a pill edge scored zero — and the pill push, whose
        only job is to reduce the conflict count, had no reason to
        open that gap.  The result was leaders running tangentially
        along pill boxes forever.

    _findSegmentConflicts handles every pair kind uniformly
        V-V, H-H, V-H, V-D, H-D, D-D, and any parallel non-collinear
        offset, all through the same distance test.  An earlier
        revision silently skipped H-H pairs, which was the root cause
        of most of the visible leader overlaps.  That skip is gone.
"""


GEO_JS = r"""
/* ---- Segment collision detection ---- */

const SEG_MIN_SEP  = 6.0;
const SEG_MIN_LEN  = 5.0;

/* Minimum clearance a leader segment must keep from a foreign pill
   box.  Equal to SEG_MIN_SEP: "too close to matter" is the same
   threshold for a segment near another segment and for a segment
   near a pill outline.  Two stroke widths plus a hair of air is
   enough for a reader to see that the two marks are separate; less
   than that and they read as touching. */
const PILL_PROX = SEG_MIN_SEP;

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

function _angleDiff(a, b) {
  let d = Math.abs(a - b) % Math.PI;
  if (d > Math.PI / 2) d = Math.PI - d;
  return d;
}

function _findSegmentConflicts(segs, minSep) {
  const out = [];
  for (let i = 0; i < segs.length; i++) {
    const A = segs[i];
    for (let j = i + 1; j < segs.length; j++) {
      const B = segs[j];
      if (A.pathIdx === B.pathIdx) continue;
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

function _segBoxOverlap(s, box) {
  const x0 = s.ax, y0 = s.ay;
  const dx = s.bx - x0, dy = s.by - y0;
  let t0 = 0, t1 = 1;
  const clip = (p, q) => {
    if (Math.abs(p) < 1e-9) return q >= 0;
    const r = q / p;
    if (p < 0) { if (r > t1) return false; if (r > t0) t0 = r; }
    else       { if (r < t0) return false; if (r < t1) t1 = r; }
    return true;
  };
  if (!clip(-dx, x0 - box.qL)) return 0;
  if (!clip( dx, box.qR - x0)) return 0;
  if (!clip(-dy, y0 - box.qT)) return 0;
  if (!clip( dy, box.qB - y0)) return 0;
  return Math.max(0, t1 - t0);
}
"""
