/* Ontology graph layout — DR-166 / DR-190. */
import { SpatialGrid, segmentBoxes } from './spatialGrid.js';
function onCurve(points, t) {
  const result = points.map(point => ({ ...point }));
  for (let length = result.length - 1; length > 0; length--)
    for (let i = 0; i < length; i++) result[i] = {
      x: result[i].x * (1 - t) + result[i + 1].x * t,
      y: result[i].y * (1 - t) + result[i + 1].y * t };
  return result[0];
}

function boundary(node, dx, dy, gap = 0) {
  const length = Math.hypot(dx, dy) || 1; dx /= length; dy /= length;
  const radius = node.box ? Math.min(node.width / 2 / Math.max(Math.abs(dx), .001),
    node.height / 2 / Math.max(Math.abs(dy), .001)) : node.width / 2;
  return { x: node.x + dx * (radius + gap), y: node.y + dy * (radius + gap) };
}

function curve(edge, a, b, offset) {
  const distance = Math.hypot(b.x - a.x, b.y - a.y) || 1;
  const control = { x: (a.x + b.x) / 2 - (b.y - a.y) / distance * offset,
    y: (a.y + b.y) / 2 + (b.x - a.x) / distance * offset };
  return route(edge, [boundary(a, control.x - a.x, control.y - a.y), control,
    boundary(b, control.x - b.x, control.y - b.y, 4)]);
}

function route(edge, controls) {
  const points = Array.from({ length: 13 }, (_, i) => onCurve(controls, i / 12));
  const xs = points.map(point => point.x), ys = points.map(point => point.y);
  return { ...edge, controls, points, left: Math.min(...xs), right: Math.max(...xs),
    top: Math.min(...ys), bottom: Math.max(...ys) };
}

function crosses(a, b, c, d) {
  const side = (p, q, r) => (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
  if (Math.max(a.x, b.x) < Math.min(c.x, d.x) || Math.min(a.x, b.x) > Math.max(c.x, d.x)
    || Math.max(a.y, b.y) < Math.min(c.y, d.y) || Math.min(a.y, b.y) > Math.max(c.y, d.y)) return false;
  return side(a, b, c) * side(a, b, d) < -.001 && side(c, d, a) * side(c, d, b) < -.001;
}

function hitsNode(points, node) {
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i], dx = b.x - a.x, dy = b.y - a.y;
    if (!node.box) {
      const t = Math.max(0, Math.min(1, ((node.x - a.x) * dx + (node.y - a.y) * dy) / (dx * dx + dy * dy || 1)));
      if (Math.hypot(a.x + dx * t - node.x, a.y + dy * t - node.y) < node.width / 2 + 8) return true;
      continue;
    }
    let start = 0, end = 1;
    for (const [origin, delta, low, high] of [[a.x, dx, node.x - node.width / 2 - 8, node.x + node.width / 2 + 8],
      [a.y, dy, node.y - node.height / 2 - 8, node.y + node.height / 2 + 8]]) {
      if (!delta) { if (origin < low || origin > high) { end = -1; break; } }
      else {
        const first = (low - origin) / delta, second = (high - origin) / delta;
        start = Math.max(start, Math.min(first, second)); end = Math.min(end, Math.max(first, second));
      }
    }
    if (start <= end) return true;
  }
  return false;
}


const finitePoint = point => Number.isFinite(point.x) && Number.isFinite(point.y);

export function routeEdges(edges, positions, bends = new Map(), optimize = true, stats = null) {
  const groups = new Map();
  for (const edge of edges) {
    const key = JSON.stringify([edge.source, edge.target].sort());
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(edge);
  }
  const nodes = [...positions].map(([id, point]) => ({ ...point, id }));
  const make = (group, bend) => group.map((edge, index) => {
    const source = positions.get(edge.source), target = positions.get(edge.target);
    const lane = (index - (group.length - 1) / 2) * 64;
    if (edge.source !== edge.target) return curve(edge, source, target,
      (bend + lane) * (edge.source < edge.target ? 1 : -1));
    const radius = source.width / 2, loop = 3 + index * 1.2;
    const angle = bend / 240 * Math.PI, rotate = (x, y) => ({
      x: source.x + x * Math.cos(angle) - y * Math.sin(angle),
      y: source.y + x * Math.sin(angle) + y * Math.cos(angle) });
    const start = rotate(radius * .7, -radius * .7), end = rotate(-radius * .7, -radius * .7);
    return route(edge, [boundary(source, start.x - source.x, start.y - source.y), rotate(radius * loop, -radius * loop),
      rotate(-radius * loop, -radius * loop), boundary(source, end.x - source.x, end.y - source.y, 4)]);
  });
  const valid = [...groups].filter(([, group]) => positions.has(group[0].source) && positions.has(group[0].target));
  for (const key of bends.keys()) if (!groups.has(key)) bends.delete(key);
  const routed = valid.map(([key, group]) => make(group, optimize ? 0 : bends.get(key) || 0));
  // shortcut: local curve choices reduce crossings without global routing; dense nonplanar graphs can still cross.
  if (optimize) {
    const finite = nodes.every(node => finitePoint(node) && Number.isFinite(node.width) && Number.isFinite(node.height))
      && routed.every(group => group.every(item => item.points.every(finitePoint)));
    const nodeGrid = new SpatialGrid(finite, 64), routeGrid = new SpatialGrid(finite, 64), placed = routed.map(() => []);
    nodes.forEach(node => {
      // The same margins as hitsNode: circles use their radius on both axes.
      const halfWidth = node.width / 2 + 8, halfHeight = (node.box ? node.height : node.width) / 2 + 8;
      nodeGrid.add({ node }, [[node.x - halfWidth, node.y - halfHeight, node.x + halfWidth, node.y + halfHeight]]);
    });
    const place = (index, group) => {
      for (const [item, keys] of placed[index]) routeGrid.remove(item, keys);
      placed[index] = group.flatMap(other => segmentBoxes(other.points).map((box, segment) => {
        const item = { other, index, segment: segment + 1 };
        return [item, routeGrid.add(item, [box])];
      }));
    };
    routed.forEach((group, index) => place(index, group));
    if (stats) Object.assign(stats, { candidates: 0, nodeTests: 0, routeTests: 0, nodes: nodes.length,
      routes: routed.reduce((total, group) => total + group.length, 0) });
    for (let index = 0; index < valid.length; index++) {
      let best;
      for (const bend of [0, 60, -60, 120, -120, 180, -180, 240, -240]) {
        const candidates = make(valid[index][1], bend);
        let score = Math.abs(bend) * .01;
        for (const candidate of candidates) {
          const boxes = finite ? segmentBoxes(candidate.points) : [];
          if (stats) stats.candidates++;
          nodeGrid.visit(boxes, ({ node }) => {
            if (stats) stats.nodeTests++;
            if (node.id !== candidate.source && node.id !== candidate.target && hitsNode(candidate.points, node)) score += 100;
          });
          // An edge counts once when any of its segments properly crosses any candidate segment.
          const crossed = new Set();
          for (let i = 1; i < candidate.points.length; i++) {
            const a = candidate.points[i - 1], b = candidate.points[i];
            routeGrid.visit(finite ? [boxes[i - 1]] : [], ({ other, index: otherIndex, segment }) => {
              if (index === otherIndex || crossed.has(other)) return;
              if (candidate.source === other.source || candidate.source === other.target
                || candidate.target === other.source || candidate.target === other.target) return;
              if (stats) stats.routeTests++;
              if (crosses(a, b, other.points[segment - 1], other.points[segment])) crossed.add(other);
            });
          }
          // Add one penalty at a time, as before, so floating-point ties resolve identically.
          for (let count = 0; count < crossed.size; count++) score += 100;
        }
        if (!best || score < best.score) best = { score, candidates, bend };
        if (!score) break;
      }
      routed[index] = best.candidates;
      place(index, best.candidates);
      bends.set(valid[index][0], best.bend);
    }
  }
  return routed.flat().map(routed => {
    const anchors = [.5, .4, .6, .3, .7, .2, .8, .1, .9].map(t => {
      const point = onCurve(routed.controls, t), before = onCurve(routed.controls, t - .01), after = onCurve(routed.controls, t + .01);
      return { ...point, normal: { x: before.y - after.y, y: after.x - before.x } };
    });
    const path = `M${routed.controls[0].x},${routed.controls[0].y} ${routed.controls.length === 3 ? 'Q' : 'C'}`
      + routed.controls.slice(1).map(point => `${point.x},${point.y}`).join(' ');
    return { ...routed, anchors, path };
  });
}
