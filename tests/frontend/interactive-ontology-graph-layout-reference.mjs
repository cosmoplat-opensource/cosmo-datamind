/* Frozen layout oracle: the routing and label placement before DR-190, kept verbatim for exact equivalence. */

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

export function referenceRouteEdges(edges, positions, bends = new Map(), optimize = true) {
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
  if (optimize) for (let index = 0; index < valid.length; index++) {
    let best;
    for (const bend of [0, 60, -60, 120, -120, 180, -180, 240, -240]) {
      const candidates = make(valid[index][1], bend);
      let score = Math.abs(bend) * .01;
      for (const candidate of candidates) {
        for (const node of nodes) if (node.id !== candidate.source && node.id !== candidate.target && hitsNode(candidate.points, node)) score += 100;
        for (let otherIndex = 0; otherIndex < routed.length; otherIndex++) {
          if (index === otherIndex) continue;
          for (const other of routed[otherIndex]) {
            if ([candidate.source, candidate.target].some(id => id === other.source || id === other.target)) continue;
            if (candidate.right < other.left || candidate.left > other.right || candidate.bottom < other.top || candidate.top > other.bottom) continue;
            let crossing = false;
            for (let i = 1; i < candidate.points.length && !crossing; i++)
              for (let j = 1; j < other.points.length && !crossing; j++)
                crossing = crosses(candidate.points[i - 1], candidate.points[i], other.points[j - 1], other.points[j]);
            if (crossing) score += 100;
          }
        }
      }
      if (!best || score < best.score) best = { score, candidates, bend };
      if (!score) break;
    }
    routed[index] = best.candidates;
    bends.set(valid[index][0], best.bend);
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

function rectangle(x, y, width, height, padding = 0) {
  return { left: x - width / 2 - padding, right: x + width / 2 + padding,
    top: y - height / 2 - padding, bottom: y + height / 2 + padding };
}

function overlapArea(a, b) {
  const width = Math.min(a.right, b.right) - Math.max(a.left, b.left);
  const height = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
  return width > 0 && height > 0 ? width * height : 0;
}

function crossingLength(segment, bounds) {
  if (segment.right <= bounds.left || segment.left >= bounds.right
    || segment.bottom <= bounds.top || segment.top >= bounds.bottom) return 0;
  let start = 0, end = 1;
  for (const [origin, delta, low, high] of [
    [segment.a.x, segment.dx, bounds.left, bounds.right],
    [segment.a.y, segment.dy, bounds.top, bounds.bottom]
  ]) {
    if (delta === 0) {
      if (origin <= low || origin >= high) return 0;
    } else {
      const first = (low - origin) / delta, second = (high - origin) / delta;
      start = Math.max(start, Math.min(first, second));
      end = Math.min(end, Math.max(first, second));
      if (end <= start) return 0;
    }
  }
  return (end - start) * segment.length;
}

export function referencePlaceLabels(labels, nodes, routes = []) {
  // shortcut: dense labels can still overlap; keep them near their own curve rather than moving them into unrelated regions.
  const occupied = nodes.map(node => rectangle(node.x, node.y, node.width, node.height, 5));
  const segments = [];
  for (const route of routes) {
    for (let i = 1; i < route.points.length; i++) {
      const a = route.points[i - 1], b = route.points[i];
      const dx = b.x - a.x, dy = b.y - a.y;
      segments.push({ id: route.id, a, dx, dy, length: Math.hypot(dx, dy), left: Math.min(a.x, b.x),
        right: Math.max(a.x, b.x), top: Math.min(a.y, b.y), bottom: Math.max(a.y, b.y) });
    }
  }
  const result = [];
  const ordered = labels.map((label, index) => ({ label, index }))
    .sort((a, b) => b.label.width * b.label.height - a.label.width * a.label.height);
  for (const { label, index } of ordered) {
    let best;
    const consider = (anchor, nx, ny, offset) => {
      const x = anchor.x + nx * offset, y = anchor.y + ny * offset;
      const bounds = rectangle(x, y, label.width, label.height);
      let area = 0;
      for (const obstacle of occupied) {
        area += overlapArea(bounds, obstacle);
        if (best && area > best.area) break;
      }
      if (best && area > best.area) return false;
      let crossings = 0;
      for (const segment of segments) {
        if (segment.id !== label.id) crossings += crossingLength(segment, bounds);
      }
      const distance = (x - label.anchors[0].x) ** 2 + (y - label.anchors[0].y) ** 2;
      if (!best || area < best.area || area === best.area && (crossings < best.crossings
        || crossings === best.crossings && distance < best.distance)) {
        best = { x, y, anchorX: anchor.x, anchorY: anchor.y, area, crossings, distance };
      }
      return area === 0 && crossings === 0;
    };
    const anchors = label.anchors.map(anchor => {
      const normal = anchor.normal || label.normal;
      const length = Math.hypot(normal.x, normal.y);
      const nx = length ? normal.x / length : 0, ny = length ? normal.y / length : 1;
      const radius = Math.min(label.width / 2 / Math.max(Math.abs(nx), .000001),
        label.height / 2 / Math.max(Math.abs(ny), .000001));
      return { anchor, nx, ny, radius };
    });
    search: for (const gap of [null, 4, 12]) {
      for (const item of anchors) {
        const offsets = gap === null ? [0] : [item.radius + gap, -(item.radius + gap)];
        for (const offset of offsets) {
          if (consider(item.anchor, item.nx, item.ny, offset)) break search;
        }
      }
    }
    const placed = { id: label.id, x: best.x, y: best.y, width: label.width, height: label.height,
      anchorX: best.anchorX, anchorY: best.anchorY };
    result[index] = placed;
    occupied.push(rectangle(placed.x, placed.y, placed.width, placed.height, 4));
  }
  return result;
}
