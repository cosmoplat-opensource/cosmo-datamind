/* Uniform spatial grid for exact pruning in graph layout — DR-190. */
// Items are found in every cell their boxes overlap, so any pair whose boxes touch is visited.
export class SpatialGrid {
  constructor(enabled = true, cell = 64) {
    this.enabled = enabled; this.cell = cell; this.cells = new Map(); this.all = new Set(); this.stamp = 0;
  }
  static key(x, y) { return x * 1048576 + y; }
  add(item, boxes) {
    const keys = new Set();
    this.all.add(item);
    if (this.enabled) for (const [left, top, right, bottom] of boxes)
      for (let x = Math.floor(left / this.cell), xEnd = Math.floor(right / this.cell); x <= xEnd; x++)
        for (let y = Math.floor(top / this.cell), yEnd = Math.floor(bottom / this.cell); y <= yEnd; y++) keys.add(SpatialGrid.key(x, y));
    for (const key of keys) {
      let cell = this.cells.get(key);
      if (!cell) this.cells.set(key, cell = new Set());
      cell.add(item);
    }
    return keys;
  }
  remove(item, keys) { this.all.delete(item); for (const key of keys) this.cells.get(key)?.delete(item); }
  // Visit each item at most once per call; a disabled grid (non-finite input) visits everything.
  visit(boxes, fn) {
    const stamp = ++this.stamp;
    if (!this.enabled) { for (const item of this.all) fn(item); return; }
    for (const [left, top, right, bottom] of boxes) {
      for (let x = Math.floor(left / this.cell), xEnd = Math.floor(right / this.cell); x <= xEnd; x++)
        for (let y = Math.floor(top / this.cell), yEnd = Math.floor(bottom / this.cell); y <= yEnd; y++) {
          const cell = this.cells.get(SpatialGrid.key(x, y));
          if (cell) for (const item of cell) if (item.stamp !== stamp) { item.stamp = stamp; fn(item); }
        }
    }
  }
}

export function segmentBoxes(points) {
  const boxes = [];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i];
    boxes.push([Math.min(a.x, b.x), Math.min(a.y, b.y), Math.max(a.x, b.x), Math.max(a.y, b.y)]);
  }
  return boxes;
}
