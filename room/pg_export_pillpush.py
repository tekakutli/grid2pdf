"""
pg_export_pillpush.py — the pill push and the polish loop.

When the optimiser's six move families have all been exhausted and a
leader is still conflicted, the pill is the movable object.

Three cases are covered:

    1.  The acting leader's OWN pill is nudged, small horizontal
        steps, accepted only when the acting leader's conflict count
        drops.

    2.  Every foreign pill whose OWNER is in conflict with the acting
        leader is nudged.  "In conflict" means segment-segment OR
        segment-pillbox OR boundary / wall-edge / wire proximity.

    3.  For pairs of leaders whose polylines cross 2 or more times, a
        coordinated move is tried: all combinations of small shifts on
        BOTH pills of the pair, scored by the TOTAL layout conflict
        count.  This is the fix for double crossings that survive the
        single-pill pass — moving one pill alone breaks the pair's
        crossings but creates a new conflict for the same leader
        elsewhere, so the single-move rule rejects it; a joint move
        can avoid both.

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

Threading the obstacle context
------------------------------
The push receives the same obstacleContext polishLeaderLayout
builds: boundaries, wireSegments, wallEdges, placed, and stripH.
It passes that context into every _countConflictsAt call so the
counter counts boundary, wall-edge, and wire conflicts as well as
the segment-segment and segment-pill ones.

Best-state preservation in the polish loop
------------------------------------------
Each round of the polish loop starts with optimizeLeaderGeometry,
whose first action is to reset bevel, jog, and diveMode to 0 on
every leader before it begins trying candidate values.  That reset
is correct inside one optimiser invocation, but it means a polish
round can leave the layout in a worse state than the round before
it if the new round's search fails to find as good a configuration.

polishLeaderLayout therefore snapshots the full optimiser state
after every round and restores the best-scoring snapshot when the
loop ends.  Without this, a round that finds no improvement over
the previous round could still cost the layout a previously-found
fix.
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

  /* ==========================================================================
     COORDINATED PILL PAIR MOVES
     ==========================================================================

     For pairs of leaders whose polylines cross more than once, no
     single pill move can always fix the pair — moving A alone often
     breaks a conflict with a third leader, and moving B alone breaks
     a different conflict.  This pass tries all combinations of small
     shifts on BOTH pills of a double-crossing pair and scores each
     combination by the TOTAL conflict count across the whole layout,
     not just the pair's own.  This is stricter than the earlier
     version, which only measured A + B's conflicts and could
     therefore accept a shift that fixed the pair while introducing
     two conflicts elsewhere. */
  {
    const SHIFTS = [0, -8, 8, -16, 16, -24, 24];
    const layout0 = _buildLayout(placed, stripH, topPad, trackOffsets);

    const baselineTotal = (() => {
      let n = 0;
      for (let i = 0; i < placed.length; i++) {
        n += _countConflictsAt(i, layout0, obstacleContext);
      }
      return Math.floor(n / 2);
    })();

    const pairs = [];
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        const xc = _countSegmentCrossings(layout0.segs[i], layout0.segs[j]);
        if (xc >= 2) pairs.push({ i, j, xc });
      }
    }
    pairs.sort((a, b) => b.xc - a.xc);

    for (const p of pairs.slice(0, 4)) {
      const A = placed[p.i];
      const B = placed[p.j];
      const aOrig = A.pillCenterX;
      const bOrig = B.pillCenterX;

      let bestA = aOrig, bestB = bOrig, bestTotal = baselineTotal;

      for (const da of SHIFTS) {
        A.pillCenterX = aOrig + da;
        if (_pillHasSameTrackCollision(p.i, placed)) continue;
        for (const db of SHIFTS) {
          B.pillCenterX = bOrig + db;
          if (_pillHasSameTrackCollision(p.j, placed)) continue;
          const lay = _buildLayout(placed, stripH, topPad, trackOffsets);
          let tot = 0;
          for (let q = 0; q < placed.length; q++) {
            tot += _countConflictsAt(q, lay, obstacleContext);
          }
          tot = Math.floor(tot / 2);
          if (tot < bestTotal - 0.5) {
            bestTotal = tot;
            bestA = aOrig + da;
            bestB = bOrig + db;
          }
        }
      }
      A.pillCenterX = bestA;
      B.pillCenterX = bestB;
    }
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

  /* Snapshot a full optimiser state.  deepClone covers the fields the
     optimiser mutates: bevel, jog, diveMode, channelYRel, offsetA,
     detourBias, and pillCenterX.  Everything else — anchorCx, w, h,
     track — is immutable during the polish loop. */
  const snap = () => placed.map(p => ({
    bevel0: p.bevel0 || 0, bevel1: p.bevel1 || 0,
    jog0:   p.jog0   || 0, jog1:   p.jog1   || 0,
    diveMode: p.diveMode || 0,
    channelYRel: p.channelYRel,
    offsetA:     p.offsetA || 0,
    detourBias:  p.detourBias || 0,
    pillCenterX: p.pillCenterX,
  }));
  const restore = (s) => {
    for (let i = 0; i < placed.length; i++) {
      const p = placed[i], t = s[i];
      p.bevel0 = t.bevel0; p.bevel1 = t.bevel1;
      p.jog0   = t.jog0;   p.jog1   = t.jog1;
      p.diveMode = t.diveMode;
      p.channelYRel = t.channelYRel;
      p.offsetA = t.offsetA;
      p.detourBias = t.detourBias;
      p.pillCenterX = t.pillCenterX;
    }
  };

  const count = () => {
    const layout = _buildLayout(placed, stripH, topPad, trackOffsets);
    let n = 0;
    for (let i = 0; i < placed.length; i++) {
      n += _countConflictsAt(i, layout, obstacleContext);
    }
    return Math.floor(n / 2);
  };

  let prev = count();
  if (prev === 0) return;
  let bestSnap = snap();
  let bestScore = prev;

  for (let round = 0; round < MAX_ROUNDS; round++) {
    optimizeLeaderGeometry(placed, stripH, topPad, trackOffsets,
                           boundaries, wireSegments, wallEdges);
    pushPillsForLeaderConflicts(placed, stripH, topPad, trackOffsets,
                                obstacleContext);

    const now = count();
    if (now < bestScore) {
      bestScore = now;
      bestSnap = snap();
    }
    if (now >= prev) break;
    prev = now;
    if (now === 0) break;
  }

  /* Restore the best-scoring state across all rounds.  Without this,
     a round that resets diveMode / bevel / jog to 0 at the start of
     optimizeLeaderGeometry would discard the previous round's gains
     whenever the new round failed to improve on them. */
  restore(bestSnap);
}
"""
