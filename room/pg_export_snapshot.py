"""
pg_export_snapshot.py — the per-candidate layout snapshot and the
per-leader conflict counter.

evaluate() and countLeaderConflicts() are the hot loops; they are
called thousands of times per export.  Building the layout once per
candidate and handing the paths, segments, and pill boxes to every
tier check is what makes the optimiser usable at scale.

The three functions:

    _buildLayout              one build, hands back paths, segments,
                              and pill boxes
    _countConflictsAt         every conflict of one leader against
                              the layout — segment-segment and
                              segment-foreign-pill-box
    _leaderHasConflictAt      the same test, early-exit on first hit
    _conflictedLeaderIndices  the set of leaders that participate in
                              any conflict at all, so the optimiser's
                              pass loop can skip the clean ones

Proximity to a pill box
-----------------------
Every foreign-pill test expands the box by PILL_PROX before asking
whether a segment overlaps it.  This is what turns "the descent
column runs 2.4 px to the left of the pill above" into a countable
conflict: with the exact box the segment is outside and scores zero;
with the box expanded by PILL_PROX the segment is inside and scores
one.  The pill push, whose only move is "slide a pill sideways until
the acting leader's conflict count drops", now has a reason to open
a gap.

The expansion is uniform across the two functions here and the
optimiser's tier-2b, so the snapshot counter, the pass-loop filter,
and the scorer all agree on what "a conflict with a pill box" means.
"""


SNAPSHOT_JS = r"""
/* ==========================================================================
   LAYOUT SNAPSHOT — one build per candidate
   ========================================================================== */

function _buildLayout(placed, stripH, topPad, trackOffsets) {
  const n = placed.length;
  const paths = new Array(n);
  const segs  = new Array(n);
  const boxes = new Array(n);
  for (let i = 0; i < n; i++) {
    const it = placed[i];
    const base = _buildLeaderPathRel(it, placed, stripH, topPad, trackOffsets);
    const bev  = _bevelPath(base, it.bevel0 || 0, it.bevel1 || 0);
    const p    = _applyJogsToPath(bev, it);
    paths[i] = p;
    segs[i]  = _pathSegments(p, i, 1.0);
    const qT = stripH + topPad + trackOffsets[it.track];
    boxes[i] = {
      qL: it.pillCenterX - it.w / 2,
      qR: it.pillCenterX + it.w / 2,
      qT,
      qB: qT + it.h,
    };
  }
  return { paths, segs, boxes };
}

/* Small helper: the PILL_PROX-expanded box, or the box itself if
   PILL_PROX is zero.  Called once per (segment, foreign-pill) pair
   in the hot loops, so it does the arithmetic inline. */
function _expandBox(box, prox) {
  return {
    qL: box.qL - prox,
    qR: box.qR + prox,
    qT: box.qT - prox,
    qB: box.qB + prox,
  };
}

/* Count all conflicts of one leader (index idx) against the layout.
   Foreign pill boxes are expanded by PILL_PROX before the overlap
   test, so a segment running within PILL_PROX of a pill edge counts
   as a conflict even when it does not intersect the box. */
function _countConflictsAt(idx, layout) {
  const selfSegs = layout.segs[idx];
  let n = 0;
  for (let j = 0; j < layout.segs.length; j++) {
    if (j === idx) continue;
    const otherSegs = layout.segs[j];
    for (let a = 0; a < selfSegs.length; a++) {
      const sa = selfSegs[a];
      for (let b = 0; b < otherSegs.length; b++) {
        const sb = otherSegs[b];
        if (sa.maxX + SEG_MIN_SEP < sb.minX) continue;
        if (sb.maxX + SEG_MIN_SEP < sa.minX) continue;
        if (sa.maxY + SEG_MIN_SEP < sb.minY) continue;
        if (sb.maxY + SEG_MIN_SEP < sa.minY) continue;
        const d = _segSegDist(sa.ax, sa.ay, sa.bx, sa.by,
                              sb.ax, sb.ay, sb.bx, sb.by);
        if (d < SEG_MIN_SEP) n++;
      }
    }
    const expanded = _expandBox(layout.boxes[j], PILL_PROX);
    for (const s of selfSegs) {
      if (s.maxX < expanded.qL || s.minX > expanded.qR) continue;
      if (s.maxY < expanded.qT || s.minY > expanded.qB) continue;
      if (_segBoxOverlap(s, expanded) > 0) n++;
    }
  }
  return n;
}

function _leaderHasConflictAt(idx, layout) {
  const selfSegs = layout.segs[idx];
  for (let j = 0; j < layout.segs.length; j++) {
    if (j === idx) continue;
    const otherSegs = layout.segs[j];
    for (const sa of selfSegs) {
      for (const sb of otherSegs) {
        if (sa.maxX + SEG_MIN_SEP < sb.minX) continue;
        if (sb.maxX + SEG_MIN_SEP < sa.minX) continue;
        if (sa.maxY + SEG_MIN_SEP < sb.minY) continue;
        if (sb.maxY + SEG_MIN_SEP < sa.minY) continue;
        const d = _segSegDist(sa.ax, sa.ay, sa.bx, sa.by,
                              sb.ax, sb.ay, sb.bx, sb.by);
        if (d < SEG_MIN_SEP) return true;
      }
    }
    const expanded = _expandBox(layout.boxes[j], PILL_PROX);
    for (const s of selfSegs) {
      if (s.maxX < expanded.qL || s.minX > expanded.qR) continue;
      if (s.maxY < expanded.qT || s.minY > expanded.qB) continue;
      if (_segBoxOverlap(s, expanded) > 0) return true;
    }
  }
  return false;
}

function _conflictedLeaderIndices(layout) {
  const s = new Set();
  const n = layout.segs.length;
  for (let i = 0; i < n; i++) {
    if (_leaderHasConflictAt(i, layout)) s.add(i);
  }
  return s;
}
"""
