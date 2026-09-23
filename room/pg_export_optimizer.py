"""
pg_export_optimizer.py — the leader-geometry optimiser.

Six move families, all tried in one pass, plus the jog-direction
guard that keeps a back-step jog from ever being accepted:

    field          moves                              step grid
    -------------  ---------------------------------  ----------
    bevel0/bevel1  the two corner cuts                3 px
    jog0/jog1      the two nearest-leg X offsets      3 px
    diveMode       0/1/2, the anchor leg's shape      discrete
    channelYRel    the horizontal run's Y             8 items
    offsetA        the anchor leg's X displacement    10 items
    detourBias     the tail's detour column offset    4 items

Every parameter that describes a leader's geometry is optimisable.

The detour bias
---------------
The tail — the descent from the channel down to the pill — is built
by _buildChannelToPill from pillX, chanY and pillTopY.  When a
blocker sits on the pillX column, the tail jogs around it: out at
entryY to a detour column, down to exitY, back at exitY to pillX.
That detour rectangle is what frequently intersects a neighbour's
descent twice, and neither channelYRel nor offsetA can move it —
those parameters control the horizontal run and the anchor leg
upstream of the tail, not the tail itself.

detourBias is a per-leader numeric offset added to the detour column
after the auto-search picks one.  It is applied only when the biased
column is still clear of every foreign box in the tail's y-range, so
it can never make a good detour worse.  The optimiser's single-move
loop and the coordinated pair refinement both try the same four
candidate values (-40, -20, +20, +40), which is enough to shift a
detour rectangle clear of a neighbouring descent on either side.

The conflicted-leader filter
----------------------------
The pass loop tries moves only for leaders that participate in some
conflict, using the obstacle context so the filter sees boundary,
wall-edge, and parallel-wire conflicts exactly as the score does.

Coordinated refinement
----------------------
A single move can usually fix a base conflict, a boundary hug, or a
pill proximity.  It cannot fix a double crossing: eliminating the
second crossing between two specific leaders usually requires both
of them to shift at once, and any single move that shifts one leader
far enough to break the double crossing tends to introduce a new
conflict somewhere else, so the score rejects the move and the pass
loop stalls.

The coordinated pass runs once at the end of optimizeLeaderGeometry.
It has two phases:

    Pass 1 — 2-way.  For each bad pair (A, B) with 2 or more proper
    crossings, try every combination of channelYRel / offsetA /
    detourBias on both leaders and every cross-parameter combination
    (A's lane with B's offset, and so on).  Apply the best.

    Pass 2 — 3-way.  A 2-way move sometimes fixes the pair while
    creating a fresh double crossing with a third leader C — the
    V24 × V27 case, where V24's diveMode 2 clears V27 but puts
    V24's anchor leg through V23's horizontal.  For that case the
    pair cannot be fixed without also moving C.  The 3-way pass
    identifies C as the leader whose path is nearest to the pair's
    crossing points, and then searches (A.diveMode, C.diveMode) and
    (A.diveMode, C.channelYRel) and (A.diveMode, C.offsetA) pairs
    — the three most productive combinations in practice — for the
    highest-severity bad pairs.

The search then runs a second time on the new state, so a two-step
fix still lands.

Pill-box proximity
------------------
The tier-2b segment-vs-foreign-pill test expands the pill box by
PILL_PROX before the Liang-Barsky clip.  A leader segment running
within PILL_PROX of a foreign pill edge counts as a conflict even
when it does not intersect the box.

The channel-to-pill tail
------------------------
A tail goes from (pillX, chanY) down to (pillX, pillTopY), always
ending at (pillX, pillTopY).  If the pillX column is clear of every
foreign box in that y-range the tail is a straight drop; otherwise
each blocker band is jogged around:

    • collect every foreign box overlapping [chanY, pillTopY];
    • compute the bands where pillX is blocked (merged when within
      2*M of each other);
    • for each band: descend at pillX to the band's entry, jog at
      entryY to the chosen detourX, descend at detourX to exitY,
      jog back at exitY to pillX;
    • after the last band, descend at pillX to pillTopY.

entryY and exitY are clamped to [chanY, pillTopY], and a band whose
clamp collapses is skipped entirely.  The detour column for each
band is the nearest edge of the band's union, walked outward in 6-px
steps if the immediate candidate is blocked by another box.

Jog-direction guard
-------------------
_applyJogsToPath rejects a jog whose sign is opposite the direction
of the leader's own horizontal run (sign of pillCenterX − anchorCx
− offsetA).  A non-zero jog in the opposite direction — the "bump"
case where the descent briefly steps back the way the horizontal
came from — is ignored, so the optimiser never sees an improvement
for it and never picks it.
"""


OPTIMIZER_JS = r"""
/* ---- Leader geometry optimiser ---- */

const BEVEL_STEP      = 3.0;
const BEVEL_MAX       = 45.0;
const BEVEL_MIN_APPLY = 6.0;
const JOG_STEP        = 3.0;
const JOG_MAX         = 24.0;
const JOG_MIN_APPLY   = 6.0;
const OPT_MAX_PASSES  = 8;

/* ==========================================================================
   CHANNEL-TO-PILL TAIL
   ==========================================================================

   The tail is the leader's descent from the channel band (y = chanY)
   down to the leader's own pill top edge (y = pillTopY), always
   ENDING at (pillX, pillTopY).

   Blockers and bands
   ------------------
   A blocker is a foreign pill whose x-range contains pillX (within
   EPS slack).  Bands are the blockers merged along y when their gap
   is at most 2*M, so consecutive blockers behave as one.

   The walk
   --------
   Start at (pillX, chanY).  For each band, in ascending y order:

     • descend at pillX from curY to entryY  (entryY = band.qT - M);
     • jog at entryY to the chosen detourX;
     • descend at detourX to exitY           (exitY = band.qB + M);
     • jog back at exitY to pillX.

   After the last band, descend at pillX to pillTopY.

   Both entry and exit are clamped to [chanY, pillTopY], and a band
   whose clamp collapses is skipped entirely.

   Detour column
   -------------
   For a band, the candidate edges are leftX = band.qL - M and
   rightX = band.qR + M.  The nearer one is tried first; if either
   fails the three clearance tests below, the search walks outward in
   6-px steps from the last tried edge.  The three tests:

     • vClear(detourX, entryY, exitY)      — the descent;
     • hClear(entryY, pillX, detourX)      — the top jog;
     • hClear(exitY,  pillX, detourX)      — the bottom jog.

   If no candidate within the walk passes all three, the band falls
   back to the preferred edge regardless.

   detourBias
   ----------
   After the auto-search picks a detourX for a band, the leader's
   detourBias is added to it.  The biased column is used only when it
   still passes all three clearance tests; otherwise the auto column
   is kept.  This lets the optimiser move the entire detour rectangle
   sideways — which neither channelYRel nor offsetA can do — without
   ever making a good detour worse. */
function _buildChannelToPill(pillX, chanY, pillTopY, self, placed,
                              stripBottom, topPad, trackOffsets) {
  const M = 4;
  const EPS = 2;
  const bias = (self && self.detourBias) || 0;

  const boxes = [];
  for (const Q of placed) {
    if (Q === self) continue;
    const qT = stripBottom + topPad + trackOffsets[Q.track];
    const qB = qT + Q.h;
    if (qB <= chanY) continue;
    if (qT >= pillTopY) continue;
    boxes.push({
      qL: Q.pillCenterX - Q.w / 2,
      qR: Q.pillCenterX + Q.w / 2,
      qT, qB,
    });
  }

  const blockers = [];
  for (const b of boxes) {
    if (pillX >= b.qL - EPS && pillX <= b.qR + EPS) blockers.push(b);
  }
  if (!blockers.length) return [[pillX, pillTopY]];

  blockers.sort((a, b) => a.qT - b.qT);
  const bands = [];
  let cur = { qT: blockers[0].qT, qB: blockers[0].qB,
              qL: blockers[0].qL, qR: blockers[0].qR };
  for (let i = 1; i < blockers.length; i++) {
    const b = blockers[i];
    if (b.qT <= cur.qB + 2 * M) {
      cur.qB = Math.max(cur.qB, b.qB);
      cur.qL = Math.min(cur.qL, b.qL);
      cur.qR = Math.max(cur.qR, b.qR);
    } else {
      bands.push(cur);
      cur = { qT: b.qT, qB: b.qB, qL: b.qL, qR: b.qR };
    }
  }
  bands.push(cur);

  const hClear = (y, xa, xb) => {
    const lo = Math.min(xa, xb), hi = Math.max(xa, xb);
    for (const b of boxes) {
      if (b.qT - EPS > y || b.qB + EPS < y) continue;
      if (b.qR < lo || b.qL > hi) continue;
      return false;
    }
    return true;
  };

  const vClear = (x, ya, yb) => {
    const lo = Math.min(ya, yb), hi = Math.max(ya, yb);
    for (const b of boxes) {
      if (b.qL - EPS > x || b.qR + EPS < x) continue;
      if (b.qB <= lo || b.qT >= hi) continue;
      return false;
    }
    return true;
  };

  const out = [];
  let curY = chanY;

  for (const band of bands) {
    const entryY = Math.max(curY, band.qT - M);
    const exitY  = Math.min(pillTopY, band.qB + M);
    if (entryY >= exitY - 0.5) continue;

    if (entryY > curY + 0.5) {
      out.push([pillX, entryY]);
      curY = entryY;
    }

    const leftX  = band.qL - M;
    const rightX = band.qR + M;
    const preferRight = (rightX - pillX) < (pillX - leftX);
    const tryOrder = preferRight ? [rightX, leftX] : [leftX, rightX];

    let detourX = null;
    for (const base of tryOrder) {
      const dir = base > pillX ? 1 : -1;
      let probe = base;
      for (let k = 0; k < 60; k++) {
        if (vClear(probe, entryY, exitY) &&
            hClear(entryY, pillX, probe) &&
            hClear(exitY,  pillX, probe)) {
          detourX = probe;
          break;
        }
        probe += dir * 6;
      }
      if (detourX !== null) break;
    }
    if (detourX === null) {
      detourX = preferRight ? rightX : leftX;
    }

    /* Apply detourBias only when the biased column remains clear.
       If the bias points into a blocker, keep the auto column. */
    if (bias !== 0) {
      const biased = detourX + bias;
      if (vClear(biased, entryY, exitY) &&
          hClear(entryY, pillX, biased) &&
          hClear(exitY,  pillX, biased)) {
        detourX = biased;
      }
    }

    out.push([detourX, entryY]);
    out.push([detourX, exitY]);
    out.push([pillX,   exitY]);
    curY = exitY;
  }

  if (pillTopY > curY + 0.5) {
    out.push([pillX, pillTopY]);
  }
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

/* ==========================================================================
   JOG DIRECTION GUARD
   ==========================================================================
   A jog slides a vertical leg of the leader's polyline horizontally.
   If the slide goes in the direction the leader is already travelling
   (the direction of its own horizontal run — the sign of
   pillCenterX minus anchorCx minus offsetA), the path continues
   smoothly past the jog.  If the slide goes the other way, the path
   reverses direction.

   _applyJogsToPath rejects a jog whose sign is opposite the direction
   of the leader's own horizontal run.  The optimiser still tries the
   same numeric candidates, but the ones that would create a back-step
   produce an unchanged path, so the score does not improve and the
   move is not selected. */

function _applyJogsToPath(path, it) {
  let p = path;

  const anchorEndX = (it.anchorCx || 0) + (it.offsetA || 0);
  const fwd = Math.sign((it.pillCenterX || 0) - anchorEndX);

  const jogAllowed = (jog) => {
    if (fwd === 0) return true;
    if (jog === 0) return true;
    return Math.sign(jog) === fwd;
  };

  if (it.jog0 && Math.abs(it.jog0) >= JOG_MIN_APPLY && p.length >= 2
      && jogAllowed(it.jog0)) {
    p = _jogSegment(p, 0, it.jog0);
  }
  if (it.jog1 && Math.abs(it.jog1) >= JOG_MIN_APPLY && p.length >= 2
      && jogAllowed(it.jog1)) {
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

const CHANNEL_Y_STEPS = [
  -24, -16, -8, -4, 4, 8, 16, 24, 32, 40, -32, -40,
];

/* offsetA candidates.  The current value is tested against each of
   these deltas.  The earlier ±8 range was too tight for the case
   where a leader's anchor is far to one side of its pill: V24's
   anchor is 81 px right of its pill, and only a shift of ~80 px in
   the lateral direction clears V24's anchor leg out of its
   neighbour's horizontal run.  The larger magnitudes let the
   optimiser reach that shift in one step instead of relying on many
   small passes to accumulate. */
const OFFSET_A_STEPS = [-80, -48, -24, -8, -2, 2, 8, 24, 48, 80];

/* Detour bias candidates.  Each value is an absolute offset from the
   auto-chosen detour column, in pixels.  Applied only when the biased
   column still passes the three clearance tests inside the tail. */
const DETOUR_BIAS_STEPS = [-40, -20, 20, 40];

/* ==========================================================================
   LEADER GEOMETRY OPTIMISER
   ==========================================================================
   Six move families, all tried in one pass.  Each candidate move is
   scored against the layout with the complete exhaustive tier list.
   Only strictly-improving moves are accepted.

   The pass loop skips leaders with no conflict at all.  A conflict
   here means any conflict the score charges for — segment-segment,
   segment-pill, boundary, wall-edge, parallel-wire, or a second
   crossing between the same pair.

   After the pass loop, a coordinated refinement runs twice.  See the
   module docstring for the rationale and the 2-way / 3-way split. */
function optimizeLeaderGeometry(placed, stripH, topPad, trackOffsets,
                                boundaries, wireSegments, wallEdges) {
  for (const it of placed) {
    it.bevel0 = 0; it.bevel1 = 0;
    it.jog0   = 0; it.jog1   = 0;
    it.diveMode = 0;
    if (typeof it.detourBias !== "number") it.detourBias = 0;
  }
  if (placed.length < 2) return;

  const bxs   = Array.isArray(boundaries)   ? boundaries   : [];
  const wires = Array.isArray(wireSegments) ? wireSegments : [];
  const wes   = Array.isArray(wallEdges)    ? wallEdges    : [];

  const obstacleContext = {
    boundaries:   bxs,
    wireSegments: wires,
    wallEdges:    wes,
    placed:       placed,
    stripH:       stripH,
  };

  function evaluate() {
    const layout = _buildLayout(placed, stripH, topPad, trackOffsets);
    const pathSegs = layout.segs;
    const boxes    = layout.boxes;

    const allSegs = [];
    for (const segs of pathSegs) {
      for (const s of segs) {
        if (s.len >= SEG_MIN_LEN) allSegs.push(s);
      }
    }

    /* --- tier 1: V-V and H-H parallel collinear pairs --- */
    let vvCount = 0, vvDepth = 0;
    let hhCount = 0, hhDepth = 0;
    for (let i = 0; i < allSegs.length; i++) {
      const A = allSegs[i];
      for (let j = i + 1; j < allSegs.length; j++) {
        const B = allSegs[j];
        if (A.pathIdx === B.pathIdx) continue;

        if (A.kind === "vert" && B.kind === "vert") {
          const dx = Math.abs(A.ax - B.ax);
          if (dx >= SEG_MIN_SEP) continue;
          const oy0 = Math.max(A.minY, B.minY);
          const oy1 = Math.min(A.maxY, B.maxY);
          if (oy1 <= oy0) continue;
          vvCount++;
          vvDepth += (SEG_MIN_SEP - dx);
          continue;
        }
        if (A.kind === "horiz" && B.kind === "horiz") {
          const dy = Math.abs(A.ay - B.ay);
          if (dy >= SEG_MIN_SEP) continue;
          const ox0 = Math.max(A.minX, B.minX);
          const ox1 = Math.min(A.maxX, B.maxX);
          if (ox1 <= ox0) continue;
          hhCount++;
          hhDepth += (SEG_MIN_SEP - dy);
          continue;
        }
      }
    }

    /* --- tier 1b: double crossings --- */
    let extraCrossings = 0;
    for (let i = 0; i < pathSegs.length; i++) {
      for (let j = i + 1; j < pathSegs.length; j++) {
        const n = _countSegmentCrossings(pathSegs[i], pathSegs[j]);
        if (n >= 2) extraCrossings += (n - 1);
      }
    }

    /* --- tier 2b: segment vs foreign pill AABB, expanded by
       PILL_PROX so a segment running alongside a pill counts --- */
    let pillCount = 0, pillDepth = 0;
    for (let i = 0; i < pathSegs.length; i++) {
      for (const s of pathSegs[i]) {
        if (s.len < 1.0) continue;
        for (let j = 0; j < boxes.length; j++) {
          if (j === i) continue;
          const box = boxes[j];
          const expanded = {
            qL: box.qL - PILL_PROX,
            qR: box.qR + PILL_PROX,
            qT: box.qT - PILL_PROX,
            qB: box.qB + PILL_PROX,
          };
          if (s.maxX < expanded.qL || s.minX > expanded.qR) continue;
          if (s.maxY < expanded.qT || s.minY > expanded.qB) continue;
          const t = _segBoxOverlap(s, expanded);
          if (t > 0) { pillCount++; pillDepth += t; }
        }
      }
    }

    /* --- tier 2: every non-parallel-collinear segment pair --- */
    let segCount = 0, segDepth = 0;
    for (let i = 0; i < allSegs.length; i++) {
      const A = allSegs[i];
      for (let j = i + 1; j < allSegs.length; j++) {
        const B = allSegs[j];
        if (A.pathIdx === B.pathIdx) continue;
        if (A.kind === "vert"  && B.kind === "vert")  continue;
        if (A.kind === "horiz" && B.kind === "horiz") continue;
        if (A.maxX + SEG_MIN_SEP < B.minX) continue;
        if (B.maxX + SEG_MIN_SEP < A.minX) continue;
        if (A.maxY + SEG_MIN_SEP < B.minY) continue;
        if (B.maxY + SEG_MIN_SEP < A.minY) continue;
        const d = _segSegDist(A.ax, A.ay, A.bx, A.by,
                              B.ax, B.ay, B.bx, B.by);
        if (d < SEG_MIN_SEP) { segCount++; segDepth += (SEG_MIN_SEP - d); }
      }
    }

    /* --- tier 3: boundary / wire / wall-edge --- */
    const bPen = _boundaryOverlapPenalty(allSegs, bxs, stripH);
    const bCount = bPen.count, bDepth = bPen.depth;

    const wPen = _wireOverlapPenalty(allSegs, placed, wires);
    const wireCount    = wPen.count;
    const wireDepth    = wPen.depth;
    const wireParCount = wPen.parCount;
    const wireParDepth = wPen.parDepth;

    const ePen = _wallEdgeOverlapPenalty(allSegs, wes);
    const weCount = ePen.count, weDepth = ePen.depth;

    /* --- tier 3d: arrowhead regions vs foreign segments --- */
    const HEAD_HALF = 5;
    let headCount = 0, headDepth = 0;
    for (let i = 0; i < placed.length; i++) {
      const it = placed[i];
      const anchorPt = [it.anchorCx, it.anchorCyRel];
      const pillPt   = [it.pillCenterX,
                        stripH + topPad + trackOffsets[it.track]];
      for (const [hx, hy] of [anchorPt, pillPt]) {
        const box = { qL: hx - HEAD_HALF, qR: hx + HEAD_HALF,
                      qT: hy - HEAD_HALF, qB: hy + HEAD_HALF };
        for (let j = 0; j < pathSegs.length; j++) {
          if (j === i) continue;
          for (const s of pathSegs[j]) {
            if (s.maxX < box.qL || s.minX > box.qR) continue;
            if (s.maxY < box.qT || s.minY > box.qB) continue;
            if (_segBoxOverlap(s, box) > 0) {
              headCount++;
              headDepth += HEAD_HALF;
            }
          }
        }
      }
    }

    /* --- tier 4: corner features --- */
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

    for (const cf of corners) {
      const cfIsPt = (Math.abs(cf.x0 - cf.x1) < 0.5 &&
                      Math.abs(cf.y0 - cf.y1) < 0.5);
      for (let bi = 0; bi < boxes.length; bi++) {
        if (bi === cf.pathIdx) continue;
        const box = boxes[bi];
        let hit = false;
        if (cfIsPt) {
          hit = cf.x0 >= box.qL && cf.x0 <= box.qR &&
                cf.y0 >= box.qT && cf.y0 <= box.qB;
        } else {
          const s = { ax: cf.x0, ay: cf.y0, bx: cf.x1, by: cf.y1,
                      minX: Math.min(cf.x0, cf.x1),
                      maxX: Math.max(cf.x0, cf.x1),
                      minY: Math.min(cf.y0, cf.y1),
                      maxY: Math.max(cf.y0, cf.y1) };
          hit = _segBoxOverlap(s, box) > 0;
        }
        if (hit) {
          cornerCount++;
          cornerDepth += CORNER_MIN_DIST;
        }
      }
    }

    const score =
      vvCount      * 1e12 + vvDepth      * 1e11 +
      hhCount      * 1e12 + hhDepth      * 1e11 +
      pillCount    * 5e11 + pillDepth    * 5e10 +
      wireParCount * 1e11 + wireParDepth * 1e10 +
      weCount      * 1e11 + weDepth      * 1e10 +
      bCount       * 1e11 + bDepth       * 1e10 +
      headCount    * 1e10 + headDepth    * 1e9  +
      extraCrossings * DOUBLE_CROSS_EXTRA_W +
      segCount     * 1e9  + segDepth     * 1e8  +
      wireCount    * 1e9  + wireDepth    * 1e8  +
      cornerCount  * 1e6  + cornerDepth  * 1e5;

    return { score };
  }

  let cur = evaluate();
  if (cur.score < 1) return;

  /* ---- Main pass loop ---- */
  for (let pass = 0; pass < OPT_MAX_PASSES; pass++) {
    const layout = _buildLayout(placed, stripH, topPad, trackOffsets);
    const conflicted = _conflictedLeaderIndices(layout, obstacleContext);

    let best = null;

    for (let li = 0; li < placed.length; li++) {
      if (!conflicted.has(li)) continue;
      const it = placed[li];

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

      const curLane = (typeof it.channelYRel === "number")
        ? it.channelYRel : 0;
      for (const dY of CHANNEL_Y_STEPS) {
        const newLane = curLane + dY;
        if (newLane < 2) continue;
        if (newLane >= topPad - 2) continue;

        const saved = it.channelYRel;
        it.channelYRel = newLane;
        const test = evaluate();
        it.channelYRel = saved;

        if (test.score < cur.score - 0.5) {
          if (!best || test.score < best.test.score) {
            best = { it, field: 'channelYRel', newValue: newLane, test };
          }
        }
      }

      const curOff = it.offsetA || 0;
      for (const dOff of OFFSET_A_STEPS) {
        const newOff = curOff + dOff;
        const saved = it.offsetA;
        it.offsetA = newOff;
        const test = evaluate();
        it.offsetA = saved;

        if (test.score < cur.score - 0.5) {
          if (!best || test.score < best.test.score) {
            best = { it, field: 'offsetA', newValue: newOff, test };
          }
        }
      }

      /* --- detourBias --- */
      const curBias = it.detourBias || 0;
      for (const cand of DETOUR_BIAS_STEPS) {
        if (cand === curBias) continue;
        const saved = it.detourBias;
        it.detourBias = cand;
        const test = evaluate();
        it.detourBias = saved;

        if (test.score < cur.score - 0.5) {
          if (!best || test.score < best.test.score) {
            best = { it, field: 'detourBias', newValue: cand, test };
          }
        }
      }
    }

    if (!best) break;
    best.it[best.field] = best.newValue;
    cur = best.test;
    if (cur.score < 1) break;
  }

  /* ==========================================================================
     COORDINATED PAIR REFINEMENT
     ==========================================================================

     Single-variable moves stall on double crossings: eliminating the
     second crossing between two leaders usually requires both of
     them to shift at once, and any single move that shifts one
     leader far enough tends to introduce a new conflict that the
     score rejects.

     Pass 1 — 2-way.  For each bad pair (A, B) with 2 or more proper
     crossings, try every combination of channelYRel / offsetA /
     detourBias on both leaders and every cross-parameter combination
     (A's lane with B's offset, and so on).  Apply the best.

     Pass 2 — 3-way.  A 2-way move sometimes fixes the pair while
     creating a fresh double crossing with a third leader C — the
     V24 × V27 case, where V24's diveMode 2 clears V27 but puts
     V24's anchor leg through V23's horizontal.  For that case the
     pair cannot be fixed without also moving C.  The 3-way pass
     identifies C as the leader whose path is nearest to the pair's
     crossing points, and then searches (A.diveMode, C.diveMode) and
     (A.diveMode, C.channelYRel) and (A.diveMode, C.offsetA) pairs
     — the three most productive combinations in practice — for the
     highest-severity bad pairs.

     The search runs a second iteration on the new state, so a
     two-step fix still lands.

     Naming note: the bystander's index is `bystanderIdx`, the
     bystander's object is `C`, and the winning move on C is
     `bestCMove`.  An earlier revision used `bestC` for both the
     index and the winning move, which was a `let` redeclaration in
     the same block scope and a parse error at load time. */
  {
    const laneChoices   = [2, 6, 10, 14, 18, 22, 26, 30];
    const offsetChoices = [0, -80, -48, -24, -16, -8, 8, 16, 24, 48, 80];
    const biasChoices   = [0, -40, -20, 20, 40];

    for (let iter = 0; iter < 2; iter++) {
      const layout0 = _buildLayout(placed, stripH, topPad, trackOffsets);
      const badPairs = [];
      for (let i = 0; i < placed.length; i++) {
        for (let j = i + 1; j < placed.length; j++) {
          const xc = _countSegmentCrossings(layout0.segs[i], layout0.segs[j]);
          if (xc >= 2) badPairs.push({ i, j, xc });
        }
      }
      if (!badPairs.length) break;
      badPairs.sort((a, b) => b.xc - a.xc);

      let anyImproved = false;

      /* ---- Pass 1: 2-way coordinated ---- */
      for (let k = 0; k < Math.min(badPairs.length, 8); k++) {
        const { i, j } = badPairs[k];
        const A = placed[i], B = placed[j];

        const aOrigLane = A.channelYRel;
        const aOrigOff  = A.offsetA || 0;
        const aOrigBias = A.detourBias || 0;
        const bOrigLane = B.channelYRel;
        const bOrigOff  = B.offsetA || 0;
        const bOrigBias = B.detourBias || 0;

        let bestScore = cur.score;
        let bestALane = aOrigLane, bestAOff = aOrigOff, bestABias = aOrigBias;
        let bestBLane = bOrigLane, bestBOff = bOrigOff, bestBBias = bOrigBias;

        /* (lane, lane) */
        for (const aLane of laneChoices) {
          if (aLane >= topPad - 2) continue;
          A.channelYRel = aLane;
          for (const bLane of laneChoices) {
            if (bLane >= topPad - 2) continue;
            B.channelYRel = bLane;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestALane = aLane; bestBLane = bLane;
              bestAOff = aOrigOff; bestBOff = bOrigOff;
              bestABias = aOrigBias; bestBBias = bOrigBias;
            }
          }
        }
        A.channelYRel = aOrigLane; B.channelYRel = bOrigLane;

        /* (offset, offset) */
        for (const aOff of offsetChoices) {
          A.offsetA = aOff;
          for (const bOff of offsetChoices) {
            B.offsetA = bOff;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestAOff = aOff; bestBOff = bOff;
              bestALane = aOrigLane; bestBLane = bOrigLane;
              bestABias = aOrigBias; bestBBias = bOrigBias;
            }
          }
        }
        A.offsetA = aOrigOff; B.offsetA = bOrigOff;

        /* (bias, bias) */
        for (const aBias of biasChoices) {
          A.detourBias = aBias;
          for (const bBias of biasChoices) {
            B.detourBias = bBias;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestABias = aBias; bestBBias = bBias;
              bestALane = aOrigLane; bestBLane = bOrigLane;
              bestAOff = aOrigOff; bestBOff = bOrigOff;
            }
          }
        }
        A.detourBias = aOrigBias; B.detourBias = bOrigBias;

        /* (lane A, offset B) and (offset A, lane B) */
        for (const aLane of laneChoices) {
          if (aLane >= topPad - 2) continue;
          A.channelYRel = aLane;
          for (const bOff of offsetChoices) {
            B.offsetA = bOff;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestALane = aLane; bestBOff = bOff;
              bestBLane = bOrigLane; bestAOff = aOrigOff;
              bestABias = aOrigBias; bestBBias = bOrigBias;
            }
          }
        }
        A.channelYRel = aOrigLane; B.offsetA = bOrigOff;

        for (const aOff of offsetChoices) {
          A.offsetA = aOff;
          for (const bLane of laneChoices) {
            if (bLane >= topPad - 2) continue;
            B.channelYRel = bLane;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestAOff = aOff; bestBLane = bLane;
              bestALane = aOrigLane; bestBOff = bOrigOff;
              bestABias = aOrigBias; bestBBias = bOrigBias;
            }
          }
        }
        A.offsetA = aOrigOff; B.channelYRel = bOrigLane;

        /* (lane A, bias B) and (bias A, lane B) */
        for (const aLane of laneChoices) {
          if (aLane >= topPad - 2) continue;
          A.channelYRel = aLane;
          for (const bBias of biasChoices) {
            B.detourBias = bBias;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestALane = aLane; bestBBias = bBias;
              bestBLane = bOrigLane; bestABias = aOrigBias;
              bestAOff = aOrigOff; bestBOff = bOrigOff;
            }
          }
        }
        A.channelYRel = aOrigLane; B.detourBias = bOrigBias;

        for (const aBias of biasChoices) {
          A.detourBias = aBias;
          for (const bLane of laneChoices) {
            if (bLane >= topPad - 2) continue;
            B.channelYRel = bLane;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestABias = aBias; bestBLane = bLane;
              bestALane = aOrigLane; bestBBias = bOrigBias;
              bestAOff = aOrigOff; bestBOff = bOrigOff;
            }
          }
        }
        A.detourBias = aOrigBias; B.channelYRel = bOrigLane;

        /* (offset A, bias B) and (bias A, offset B) */
        for (const aOff of offsetChoices) {
          A.offsetA = aOff;
          for (const bBias of biasChoices) {
            B.detourBias = bBias;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestAOff = aOff; bestBBias = bBias;
              bestALane = aOrigLane; bestBLane = bOrigLane;
              bestABias = aOrigBias; bestBOff = bOrigOff;
            }
          }
        }
        A.offsetA = aOrigOff; B.detourBias = bOrigBias;

        for (const aBias of biasChoices) {
          A.detourBias = aBias;
          for (const bOff of offsetChoices) {
            B.offsetA = bOff;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestABias = aBias; bestBOff = bOff;
              bestALane = aOrigLane; bestBLane = bOrigLane;
              bestAOff = aOrigOff; bestBBias = bOrigBias;
            }
          }
        }
        A.detourBias = aOrigBias; B.offsetA = bOrigOff;

        /* Apply the best combination found. */
        A.channelYRel = bestALane;
        A.offsetA     = bestAOff;
        A.detourBias  = bestABias;
        B.channelYRel = bestBLane;
        B.offsetA     = bestBOff;
        B.detourBias  = bestBBias;

        if (bestScore < cur.score - 0.5) {
          cur = { score: bestScore };
          anyImproved = true;
        }
      }

      /* ---- Pass 2: 3-way with the bystander ----

         For each bad pair (A, B), find the leader C whose path comes
         nearest to the pair's crossing points, and try moving A and
         C together.  The diveMode pair is tried first — that is the
         most productive combination — followed by (A.diveMode,
         C.channelYRel) and (A.diveMode, C.offsetA).  The three
         parameter pools give 3 * 3 + 3 * 8 + 3 * 11 = 66 evaluations
         per pair; on the top four pairs that is under 300 total.

         This is what lets V24 × V27 be resolved: V24's diveMode 2
         clears its crossings with V27, and V23's diveMode 2 clears
         the crossings V24's new anchor leg would otherwise make
         through V23's horizontal.  Neither move helps alone, both
         help together. */
      for (let k = 0; k < Math.min(badPairs.length, 4); k++) {
        const { i, j } = badPairs[k];
        const A = placed[i], B = placed[j];

        /* The pair's crossing points. */
        const cps = [];
        for (const sa of layout0.segs[i]) {
          for (const sb of layout0.segs[j]) {
            if (!_segSegProperCross(sa, sb)) continue;
            const d1x = sa.bx - sa.ax, d1y = sa.by - sa.ay;
            const d2x = sb.bx - sb.ax, d2y = sb.by - sb.ay;
            const denom = d1x * d2y - d1y * d2x;
            const rx = sb.ax - sa.ax, ry = sb.ay - sa.ay;
            const t = (rx * d2y - ry * d2x) / denom;
            cps.push([sa.ax + t * d1x, sa.ay + t * d1y]);
          }
        }
        if (cps.length < 2) continue;

        /* Nearest leader to those crossing points. */
        let bystanderIdx = -1, bystanderDist = 50;
        for (let q = 0; q < placed.length; q++) {
          if (q === i || q === j) continue;
          for (const cp of cps) {
            for (const s of layout0.segs[q]) {
              const d = _pointSegDist(cp[0], cp[1],
                                       s.ax, s.ay, s.bx, s.by);
              if (d < bystanderDist) {
                bystanderDist = d;
                bystanderIdx = q;
              }
            }
          }
        }
        if (bystanderIdx < 0) continue;

        const C = placed[bystanderIdx];
        const aSave = { diveMode: A.diveMode, channelYRel: A.channelYRel,
                        offsetA: A.offsetA, detourBias: A.detourBias };
        const cSave = { diveMode: C.diveMode, channelYRel: C.channelYRel,
                        offsetA: C.offsetA, detourBias: C.detourBias };

        let bestScore = cur.score;
        let bestAMove = null, bestCMove = null;

        /* (A.diveMode, C.diveMode) */
        for (const aD of [0, 1, 2]) {
          if (aD === aSave.diveMode) continue;
          A.diveMode = aD;
          for (const cD of [0, 1, 2]) {
            if (cD === cSave.diveMode) continue;
            C.diveMode = cD;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestAMove = { field: "diveMode", value: aD };
              bestCMove = { field: "diveMode", value: cD };
            }
          }
        }
        A.diveMode = aSave.diveMode;
        C.diveMode = cSave.diveMode;

        /* (A.diveMode, C.channelYRel) */
        for (const aD of [0, 1, 2]) {
          if (aD === aSave.diveMode) continue;
          A.diveMode = aD;
          for (const cL of laneChoices) {
            if (cL >= topPad - 2 || cL === cSave.channelYRel) continue;
            C.channelYRel = cL;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestAMove = { field: "diveMode", value: aD };
              bestCMove = { field: "channelYRel", value: cL };
            }
          }
        }
        A.diveMode = aSave.diveMode;
        C.channelYRel = cSave.channelYRel;

        /* (A.diveMode, C.offsetA) */
        for (const aD of [0, 1, 2]) {
          if (aD === aSave.diveMode) continue;
          A.diveMode = aD;
          for (const cO of offsetChoices) {
            if (cO === cSave.offsetA) continue;
            C.offsetA = cO;
            const s = evaluate().score;
            if (s < bestScore - 0.5) {
              bestScore = s;
              bestAMove = { field: "diveMode", value: aD };
              bestCMove = { field: "offsetA", value: cO };
            }
          }
        }
        A.diveMode = aSave.diveMode;
        C.offsetA = cSave.offsetA;

        if (bestAMove && bestCMove) {
          A[bestAMove.field] = bestAMove.value;
          C[bestCMove.field] = bestCMove.value;
          cur = { score: bestScore };
          anyImproved = true;
        }
      }

      if (!anyImproved) break;
    }
  }
}
"""
