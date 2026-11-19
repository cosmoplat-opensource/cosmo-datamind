/* Ontology graph layout — DR-161. */
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

export function placeLabels(labels, nodes, routes = []) {
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
