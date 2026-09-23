"""
pg_export_labels.py — vertex label placement and draw.

The one pass that decides where every vertex label pill sits and then
draws it.  Placement runs once per export, per cable; draw runs once.

Placement
---------
    Phase 1   Row count — a property of the pill set: the minimum
              number of rows the pills' total width requires.
    Phase 2   Interleaved assignment — pill i goes to row (i % n).
    Phase 3   Compress at COMPRESS_GAP, center in the strip.

Then the pass hands the geometry to pg_export_primitives'
refineLeaderOffsets and pg_export_pillpush's polishLeaderLayout for
the routing optimiser, and finally computes the style assignment
using pg_export_style's conflict graph.

Draw
----
The pill rectangles, the text lines, and the leader polylines (with
their per-pill dash style from the vertex cover).

The vertex cover that turns the style graph into a solid/dashed
assignment lives here, inline, because it produces the last field
the leader placement ever writes (it.leaderStyle) and because the
pills and the leaders are drawn in the same pass.
"""


LABELS_JS = r"""
/* ---- Vertex coordinate labels ---- */

function computeVertexLabelPlacement(c, chunks, stripOffsetX,
                                     stripAreaX0, stripAreaW, stripH,
                                     orderedIds) {
  const fmtCm = (mm) => String(Math.round(mm / 10));
  const FONT  = "700 12px ui-monospace, monospace";
  const PAD_X = 6, PAD_Y = 4, LINE_H = 14;
  const TRACK_GAP_X   = 10;
  const TRACK_V_GAP   = 8;
  const GROUP_TOL     = 8;
  const MAX_TRACKS    = 20;
  const AREA_X0 = stripAreaX0;
  const AREA_X1 = stripAreaX0 + stripAreaW;

  const COMPRESS_GAP = 20;

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

  if (window.filterCollinearVerticesInStrip && Array.isArray(orderedIds)) {
    const oi = new Map();
    orderedIds.forEach((id, i) => oi.set(id, i));

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

    const TOL_MM        = 2.0;
    const LEN_RATIO_MAX = 3.0;
    const kept = [];

    for (const it of items) {
      const idxs = it.members
        .map(id => oi.get(id))
        .filter(v => typeof v === "number")
        .sort((a, b) => a - b);

      if (idxs.length === 0) { kept.push(it); continue; }

      const lo = idxs[0];
      const hi = idxs[idxs.length - 1];

      if (hi - lo + 1 !== idxs.length) { kept.push(it); continue; }
      if (lo === 0 || hi === orderedIds.length - 1) {
        kept.push(it); continue;
      }

      const bP = anchorMm.get(orderedIds[lo - 1]);
      const aP = anchorMm.get(orderedIds[hi + 1]);
      const cP = anchorMm.get(it.members[0]);
      if (!bP || !aP || !cP) { kept.push(it); continue; }

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
      areaX0: AREA_X0,
      areaX1: AREA_X1,
    };
  }

  items.sort((a, b) => a.anchorCx - b.anchorCx);

  const tracks = [];

  const usableWidth = AREA_X1 - AREA_X0;
  const totalPillW  = items.reduce((a, it) => a + it.w, 0);
  let numRows = 1;
  while (numRows < MAX_TRACKS) {
    const needed = totalPillW + (items.length - numRows) * TRACK_GAP_X;
    if (needed <= numRows * usableWidth) break;
    numRows++;
  }

  const rows = [];
  for (let i = 0; i < numRows; i++) rows.push([]);
  for (let i = 0; i < items.length; i++) {
    rows[i % numRows].push(items[i]);
  }

  for (let t = 0; t < rows.length; t++) {
    const row = rows[t];
    if (row.length === 0) continue;

    const k = row.length;
    const sumW = row.reduce((a, it) => a + it.w, 0);
    const rowWidth = sumW + (k - 1) * COMPRESS_GAP;
    const edgePad = Math.max(0, (usableWidth - rowWidth) / 2);

    let cur = AREA_X0 + edgePad + row[0].w / 2;
    row[0].pillCenterX = cur;
    for (let i = 1; i < k; i++) {
      cur += row[i - 1].w / 2 + COMPRESS_GAP + row[i].w / 2;
      row[i].pillCenterX = cur;
    }

    const rowRight = row[k - 1].pillCenterX + row[k - 1].w / 2;
    if (rowRight > AREA_X1) {
      const dx = rowRight - AREA_X1;
      for (const it of row) it.pillCenterX -= dx;
    }

    for (const it of row) {
      it.track     = t;
      it._baseDisp = Math.abs(it.pillCenterX - it.anchorCx);
    }
    tracks.push(row);
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

  refineLeaderOffsets(placed, topPad, trackOffsets, stripH);

  polishLeaderLayout(placed, stripH, topPad, trackOffsets,
                     boundaries, wireSegments, wallEdges);

  const conflictAdj = _buildStyleConflictGraph(placed, stripH, topPad,
                                                trackOffsets);

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

    for (let outer = 0; outer < 30; outer++) {
      let changed = false;

      for (const v of Array.from(dashed)) {
        let hasSolid = false;
        for (const u of conflictAdj.get(v) || []) {
          if (!dashed.has(u)) { hasSolid = true; break; }
        }
        if (!hasSolid) { dashed.delete(v); changed = true; }
      }

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
    areaX0: AREA_X0,
    areaX1: AREA_X1,
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
"""
