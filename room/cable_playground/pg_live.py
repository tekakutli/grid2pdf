"""
pg_live.py — volatile half of the cable playground HTML/JS bundle.

Everything the playground's UX actually is: rendering decisions, snap
producers, hop transitions, the wormhole route planner, drawing lifecycle,
the Alt-click reuse policy, event handlers, status text.

This module is expected to be fully rewritten whenever we iterate on the
playground.  When you ask for a change, this is the file that gets
reprinted — pg_core.py stays put unless the change touches the model.

The printable cable-run diagram exporter used to live at the bottom of
this file.  It has been extracted to pg_export.py so the volatile UX half
stays focused on screen interaction.
"""


LIVE_JS = r"""
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
   HOP TRANSITION
   ==========================================================================
   Two consecutive wall-edge anchors on different segments that are far
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
   the top of a step face project to the eagle view. */
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

    if (snapEnabled || mouse.alt) {
      const reuseFilter = (a) => isReusableAnchor(a, mouse.alt, view);
      sticky = findAnchorAtScreen(view, drawingPreview.sx, drawingPreview.sy,
                                  reuseFilter);

      if (!sticky) {
        snapSpec = (view === "floor")
          ? snapFloorSpec(pwx, pwy, true)
          : snapWallSpec(pwx, pwy, true);

        if (snapSpec && snapSpec.space === "wall-edge") {
          const scale = (view === "floor")
            ? viewFloor.scale
            : Math.min(viewWall.scaleX, viewWall.scaleY);
          const tolMm = ANCHOR_NEARBY_PX / scale;
          const s = WALL.segments[snapSpec.segIdx];
          if (s) {
            for (const a of anchors.values()) {
              if (a.space !== "wall-edge") continue;
              if (a.segIdx !== snapSpec.segIdx) continue;
              if (!isReusableAnchor(a, mouse.alt, view)) continue;
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
    } else if (!snapEnabled && !mouse.alt && view === "wall") {
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
                            : (snapEnabled || mouse.alt) ? "#0ea5e9" : "#94a3b8";
            ctx.lineWidth = 1.8;
            ctx.stroke(); ctx.setLineDash([]);
          }
        }
      }

      ctx.beginPath();
      ctx.arc(px, py, sticky ? 7 : (snapSpec ? 6 : 4), 0, Math.PI * 2);
      ctx.fillStyle = sticky ? "#10b981"
                    : snapSpec ? "#f59e0b"
                    : (snapEnabled || mouse.alt) ? "#0ea5e9" : "#94a3b8";
      ctx.fill();
      ctx.strokeStyle = sticky ? "#047857"
                      : snapSpec ? "#b45309"
                      : (snapEnabled || mouse.alt) ? "#0ea5e9" : "#94a3b8";
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

  if (mouse.alt) {
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

  /* Alt+click on a nearby endpoint = adopt (start of drawing) or merge. */
  if (altKey) {
    const target = findMergeTarget(view, sx, sy);
    if (target) {
      if (drawing.anchorIds.length === 0) adoptMergeBase(target);
      else                               mergeDrawingInto(target);
      return;
    }
  }

  /* Only reuse an anchor when the reuse predicate allows it. */
  const reuseFilter = (a) => isReusableAnchor(a, altKey, view);

  let anchorId = null;
  const nearby = findAnchorAtScreen(view, sx, sy, reuseFilter);
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
          const rid = resolveSpecToAnchorId(route[i], 5, null);
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

  if (anchorId == null) {
    anchorId = resolveSpecToAnchorId(spec, tolMm, reuseFilter);
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

function findAnchorAtScreen(view, sx, sy, reuseFilter) {
  const project  = (view === "floor") ? anchorPlan : anchorWall;
  const toScreen = (view === "floor") ? w2sFloor   : w2sWall;
  let best = null, bestD = ANCHOR_NEARBY_PX;
  for (const a of anchors.values()) {
    if (view === "wall" && a.space === "wall-edge" && isSegHidden(a.segIdx))
      continue;
    if (reuseFilter && !reuseFilter(a)) continue;
    const p = project(a);
    if (!p) continue;
    const [px, py] = toScreen(p[0], p[1]);
    const d = Math.hypot(px - sx, py - sy);
    if (d < bestD) { bestD = d; best = a; }
  }
  return best;
}

function anchorOwnerCable(anchorId) {
  for (const c of state.floorCables) {
    if (c.anchorIds.includes(anchorId)) return { cable: c, view: "floor" };
  }
  for (const c of state.wallCables) {
    if (c.anchorIds.includes(anchorId)) return { cable: c, view: "wall" };
  }
  return null;
}

function isReusableAnchor(a, altHeld, currentView) {
  if (!a) return false;
  if (altHeld) return true;
  if (drawing && drawing.anchorIds.includes(a.id)) return true;
  if (currentView) {
    const owner = anchorOwnerCable(a.id);
    if (owner && owner.view !== currentView) return true;
  }
  return false;
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

function clearAll() {
  if (!confirm("Clear all grids and cables?")) return;
  exitFocus();
  anchors.clear();
  state.floorGrids = []; state.wallGrids = [];
  state.floorCables = []; state.wallCables = [];
  state.trueCables = [];
  state.selectedCable = null; state.selectedVertex = null;
  drawing = null; drawingPreview = null;
  draw();
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
  mouse.alt = e.altKey;
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
   STATUS / CURSOR
   ========================================================================== */

function updateStatus() {
  if (statusTimer) return;
  if (drawing) {
    const n = drawing.anchorIds.length;
    const snapHint = snapEnabled ? " · snap ON" : " · hold Shift to snap";
    if (drawing.view === null) {
      statusEl.textContent =
        "drawing · click first point in floor plan OR wall strip" +
        " · Alt+click a vertex to connect/adopt" + snapHint;
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
        ` · Alt+click a vertex to connect/adopt${snapHint} · Enter / Esc finishes`;
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

function updateCursor() {
  if (!mouse.inside) { canvas.style.cursor = "default"; return; }
  if (drawing) { canvas.style.cursor = "crosshair"; return; }
  if (state.hoveredRoute) { canvas.style.cursor = "pointer"; return; }
  canvas.style.cursor = "crosshair";
}
"""
