"""
pg_export_pillpush.py — the pill push and the polish loop.

When the optimiser's five move families have all been exhausted and
a leader is still conflicted, the pill is the movable object.

Two cases are covered:

    1.  The acting leader's OWN pill is nudged, small horizontal
        steps, and the push is accepted only when the acting
        leader's conflict count drops.

    2.  Every foreign pill whose OWNER is in conflict with the
        acting leader is nudged.  "In conflict" means
        segment-segment OR segment-pillbox OR (now) boundary /
        wall-edge / wire proximity — the obstacle context is
        threaded through so a pill whose descent column sits too
        near a wall-section boundary counts as conflicted and the
        push tries to move it.

A same-track pill-collision guard
---------------------------------
Two pills in the same track — the same horizontal row — must not
overlap.  Two pills in DIFFERENT tracks are at different y and can
safely overlap horizontally; that is what the track system is for.

The pill push respects this with a hard gate: before any candidate
position is scored, the pill is checked against every other pill in
its own track.  If the moved pill would come within PILL_PROX of a
same-track neighbour, that candidate is skipped.  A one-time
_separatePillsInTracks pre-pass at the top of the push runs first
and pushes any already-too-close pairs apart, left to right per
track.

buildPathSegsFor is the same helper the conflict counter uses: it
rebuilds one leader's current path segments from its optimisable
fields, without going through the full _buildLayout pipeline.
"""


PILLPUSH_JS = r"""
/* ==========================================================================
   PILL PUSH PASS
   ========================================================================== */

function buildPathSegsFor(it, placed, stripH, topPad, trackOffsets) {
  const chanY    = stripH + (it.channelYRel || 0);
  const pillTopY = stripH + topPad + trackOffsets[it.track];
  const base = computeLeaderPath(
    it.anchorCx, it.anchorCyRel, it.offsetA || 0,
    chanY, it.pillCenterX, pillTopY,
    it, placed, stripH, topPad, trackOffsets, it.diveMode || 0);
  const path = _applyJogsToPath(
    _bevelPath(base, it.bevel0 || 0, it.bevel1 || 0), it);
  return _pathSegments(path, -1, SEG_MIN_LEN);
}

function _pillHasSameTrackCollision(idx, placed) {
  const me = placed[idx];
  const myTrack = me.track;
  const myL = me.pillCenterX - me.w / 2;
  const myR = me.pillCenterX + me.w / 2;

  for (let j = 0; j < placed.length; j++) {
    if (j === idx) continue;
    const other = placed[j];
    if (other.track !== myTrack) continue;
    const oL = other.pillCenterX - other.w / 2;
    const oR = other.pillCenterX + other.w / 2;
    if (myR + PILL_PROX > oL && oR + PILL_PROX > myL) return true;
  }
  return false;
}

function _separatePillsInTracks(placed) {
  const byTrack = new Map();
  for (let i = 0; i < placed.length; i++) {
    const t = placed[i].track;
    if (!byTrack.has(t)) byTrack.set(t, []);
    byTrack.get(t).push(i);
  }
  for (const [, indices] of byTrack) {
    indices.sort((a, b) =>
      placed[a].pillCenterX - placed[b].pillCenterX);
    for (let k = 1; k < indices.length; k++) {
      const prev = placed[indices[k - 1]];
      const cur  = placed[indices[k]];
      const prevR = prev.pillCenterX + prev.w / 2;
      const curL  = cur.pillCenterX - cur.w / 2;
      const gap = curL - prevR;
      if (gap < PILL_PROX) {
        cur.pillCenterX += (PILL_PROX - gap);
      }
    }
  }
}

function pushPillsForLeaderConflicts(placed, stripH, topPad, trackOffsets,
                                     obstacleContext) {
  const PUSH = [];
  for (let d = 4; d <= 32; d += 4) { PUSH.push(d); PUSH.push(-d); }

  const MAX_ROUNDS = 3;

  _separatePillsInTracks(placed);

  for (let round = 0; round < MAX_ROUNDS; round++) {
    let anyMoved = false;

    for (let li = 0; li < placed.length; li++) {
      const it = placed[li];
      let layout = _buildLayout(placed, stripH, topPad, trackOffsets);
      const before = _countConflictsAt(li, layout, obstacleContext);
      if (before === 0) continue;

      const origX = it.pillCenterX;
      let bestX = origX, bestN = before;
      for (const dX of PUSH) {
        it.pillCenterX = origX + dX;
        if (_pillHasSameTrackCollision(li, placed)) continue;
        layout = _buildLayout(placed, stripH, topPad, trackOffsets);
        const n = _countConflictsAt(li, layout, obstacleContext);
        if (n < bestN) { bestN = n; bestX = origX + dX; }
      }
      it.pillCenterX = bestX;
      if (bestX !== origX) anyMoved = true;

      if (bestN > 0) {
        const involved = new Set();
        const selfSegs = layout.segs[li];

        for (let j = 0; j < placed.length; j++) {
          if (j === li) continue;
          const otherSegs = layout.segs[j];
          let cflt = false;

          for (const sa of selfSegs) {
            if (cflt) break;
            for (const sb of otherSegs) {
              if (sa.maxX + SEG_MIN_SEP < sb.minX) continue;
              if (sb.maxX + SEG_MIN_SEP < sa.minX) continue;
              if (sa.maxY + SEG_MIN_SEP < sb.minY) continue;
              if (sb.maxY + SEG_MIN_SEP < sa.minY) continue;
              const d = _segSegDist(sa.ax, sa.ay, sa.bx, sa.by,
                                    sb.ax, sb.ay, sb.bx, sb.by);
              if (d < SEG_MIN_SEP) { cflt = true; break; }
            }
          }

          if (!cflt) {
            const box = layout.boxes[j];
            const expanded = _expandBox(box, PILL_PROX);
            for (const s of selfSegs) {
              if (s.maxX < expanded.qL || s.minX > expanded.qR) continue;
              if (s.maxY < expanded.qT || s.minY > expanded.qB) continue;
              if (_segBoxOverlap(s, expanded) > 0) { cflt = true; break; }
            }
          }

          if (cflt) involved.add(j);
        }

        for (const j of involved) {
          const foreign = placed[j];
          const fOrigX = foreign.pillCenterX;
          let fBestX = fOrigX, fBestN = bestN;
          for (const dX of PUSH) {
            foreign.pillCenterX = fOrigX + dX;
            if (_pillHasSameTrackCollision(j, placed)) continue;
            layout = _buildLayout(placed, stripH, topPad, trackOffsets);
            const n = _countConflictsAt(li, layout, obstacleContext);
            if (n < fBestN) { fBestN = n; fBestX = fOrigX + dX; }
          }
          foreign.pillCenterX = fBestX;
          if (fBestX !== fOrigX) { anyMoved = true; bestN = fBestN; }
        }
      }
    }
    if (!anyMoved) break;
  }
}

/* ==========================================================================
   POLISH LOOP
   ========================================================================== */

function polishLeaderLayout(placed, stripH, topPad, trackOffsets,
                            boundaries, wireSegments, wallEdges) {
  const MAX_ROUNDS = 6;

  const obstacleContext = {
    boundaries:   boundaries   || [],
    wireSegments: wireSegments || [],
    wallEdges:    wallEdges    || [],
    placed:       placed,
    stripH:       stripH,
  };

  const initialLayout = _buildLayout(placed, stripH, topPad, trackOffsets);
  let prev = 0;
  for (let i = 0; i < placed.length; i++) {
    prev += _countConflictsAt(i, initialLayout, obstacleContext);
  }
  prev = Math.floor(prev / 2);
  if (prev === 0) return;

  for (let round = 0; round < MAX_ROUNDS; round++) {
    optimizeLeaderGeometry(placed, stripH, topPad, trackOffsets,
                           boundaries, wireSegments, wallEdges);
    pushPillsForLeaderConflicts(placed, stripH, topPad, trackOffsets,
                                obstacleContext);

    const layout = _buildLayout(placed, stripH, topPad, trackOffsets);
    let now = 0;
    for (let i = 0; i < placed.length; i++) {
      now += _countConflictsAt(i, layout, obstacleContext);
    }
    now = Math.floor(now / 2);

    if (now >= prev) break;
    prev = now;
    if (now === 0) break;
  }
}
"""
