"""
pg_export_penalties.py — the four tier-list penalty functions.

Each one is a pure query: it takes the leader segments (or the placed
list, for the corner feature) plus its own obstacle set, and returns
a { count, depth } pair or the parallel/crossing split.

    _boundaryOverlapPenalty   a leader leg running alongside a
                              wall-section boundary (the vertical
                              edge between two wall chunks in the
                              strip).  Angle gate is ±45° from
                              vertical: a leader up to that far off
                              still reads as "running alongside" a
                              vertical boundary for a stretch.
                              Threshold BOUNDARY_MIN_SEP = 10 px,
                              wider than the segment-vs-segment
                              threshold, because a boundary is a
                              semantic partition of the strip.

    _wireOverlapPenalty       a leader leg running alongside or
                              crossing the cable wire.  Parallel
                              contacts are caught with a wider
                              angle window (WIRE_PARALLEL_TOL = 30°)
                              and a wider separation
                              (WIRE_MIN_SEP = 10 px) than crossings.
                              A leader that merely crosses the wire
                              is unambiguously distinguishable; a
                              leader that runs alongside it is not.

    _wallEdgeOverlapPenalty   a near-horizontal leader leg running
                              alongside a wall chunk's top or bottom
                              z-edge.  Max-distance criterion.

    _cornerFeature / _cornerDist
                              the bevel-cut region of each leader.

The tier weights themselves live in evaluate() in
pg_export_optimizer.py, not here.  This module computes the raw
counts and depths.
"""


PENALTIES_JS = r"""
/* ---- Wall-section boundary collision ----

   The boundary is a vertical line — the left or right edge of a wall
   chunk.  A leader running alongside it must keep BOUNDARY_MIN_SEP
   of air, and the angle gate admits segments up to ±45° from
   vertical.  The average-distance criterion (not max) is what makes
   a slanted leader count when it stays close over a stretch: an
   exactly-vertical leader has a single constant distance, and a
   30°-from-vertical leader varies over its length but its average
   is still small when the whole segment hugs the boundary. */
function _boundaryOverlapPenalty(leaderSegs, boundaries, stripH) {
  let count = 0, depth = 0;
  if (!boundaries || !boundaries.length) return { count, depth };

  /* Slope gate: catch any leader segment within 45° of vertical.
     A vertical segment has slope |dx/dy| = 0; a 45° segment has
     slope 1.0.  Beyond that the segment reads as diagonal, not as
     running alongside a vertical line. */
  const SLOPE_MAX = 1.0;

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
      if (s.maxX + BOUNDARY_MIN_SEP < bx) continue;
      if (s.minX - BOUNDARY_MIN_SEP > bx) continue;

      let sumDist = 0;
      const N = 8;
      for (let k = 0; k <= N; k++) {
        const y = yLo + (yHi - yLo) * k / N;
        const t = (y - s.ay) / dy;
        const x = s.ax + dx * t;
        sumDist += Math.abs(x - bx);
      }
      const avgDist = sumDist / (N + 1);
      if (avgDist < BOUNDARY_MIN_SEP) {
        count++;
        depth += (BOUNDARY_MIN_SEP - avgDist);
      }
    }
  }
  return { count, depth };
}

/* ---- Cable-wire collision ----

   A leader segment near a wire segment is a conflict.  The
   distinction between a parallel pass and a crossing is made by
   angle (WIRE_PARALLEL_TOL) and governs which separation threshold
   applies: WIRE_MIN_SEP for parallel, SEG_MIN_SEP for crossing.
   The parallel case is the one the user actually sees as an
   overlap; the crossing case is unambiguous. */
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

      if (s.maxX + WIRE_MIN_SEP < wminX) continue;
      if (wmaxX + WIRE_MIN_SEP < s.minX) continue;
      if (s.maxY + WIRE_MIN_SEP < wminY) continue;
      if (wmaxY + WIRE_MIN_SEP < s.minY) continue;

      const dAnchor = _pointSegDist(ax, ay, w.ax, w.ay, w.bx, w.by);
      if (dAnchor < WIRE_ANCHOR_TRIM) continue;

      const d = _segSegDist(s.ax, s.ay, s.bx, s.by,
                            w.ax, w.ay, w.bx, w.by);
      const wAngle = Math.atan2(w.by - w.ay, w.bx - w.ax);
      const angDiff = _angleDiff(sAngle, wAngle);

      if (angDiff < WIRE_PARALLEL_TOL && d < WIRE_MIN_SEP) {
        parCount++;
        parDepth += (WIRE_MIN_SEP - d);
        count++;
        depth += (WIRE_MIN_SEP - d);
      } else if (d < SEG_MIN_SEP) {
        count++;
        depth += (SEG_MIN_SEP - d);
      }
    }
  }
  return { count, depth, parCount, parDepth };
}

const WIRE_ANCHOR_TRIM = 8.0;

/* ---- Wall-edge collision ---- */

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

/* ---- Corner features ---- */

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
"""
