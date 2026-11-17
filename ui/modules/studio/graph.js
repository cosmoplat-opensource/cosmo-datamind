/* Studio graph migration — DR-159; provenance: specs/sources/IR-031-studio-sources.json. */
import { label, shortIRI } from './model.js';
import { placeLabels } from './labelLayout.js';
import { routeEdges } from './edgeRouting.js';

const NS = 'http://www.w3.org/2000/svg';
const COLORS = { class: '#dfecff', datatype: '#e0f4ff', expression: '#eee7ff',
  individual: '#ffedd5', literal: '#dff5e7', property: '#e8edf5' };
function element(tag, attrs = {}, text) {
  const result = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) result.setAttribute(key, value);
  if (text !== undefined) result.textContent = text;
  return result;
}
function seed(text) {
  let value = 2166136261;
  for (const char of text) value = Math.imul(value ^ char.codePointAt(0), 16777619);
  return value >>> 0;
}
function widthOf(text) {
  return [...text].reduce((width, char) => width + (char.codePointAt(0) > 255 ? 12 : 6.7), 0);
}
function linesOf(text, width, limit = 4) {
  const lines = []; let line = '';
  for (const char of String(text)) {
    if (char === '\n' || widthOf(line + char) > width) {
      lines.push(line); line = char === '\n' ? '' : char;
      if (lines.length === limit) break;
    } else line += char;
  }
  if (lines.length < limit && line) lines.push(line);
  if (!lines.length) lines.push('');
  if (lines.join('').length < String(text).replaceAll('\n', '').length) lines[lines.length - 1] += '…';
  return lines;
}

let instanceSequence = 0;
export class GraphView {
  constructor(svg, callbacks = {}) {
    this.svg = svg; this.callbacks = callbacks; this.instanceId = "studio-" + (++instanceSequence);
    this.positions = new Map(); this.nodeElements = new Map(); this.edgeElements = new Map();
    this.edgeLabels = new Map(); this.routeBends = new Map(); this.links = []; this.degrees = new Map();
    this.data = { nodes: [], edges: [] }; this.selectedId = null; this.language = 'zh';
    this.options = { classDistance: 220, datatypeDistance: 150, charge: 650, gravity: 0.025,
      lineStrength: 0.035, labelWidth: 138, dynamicLabelWidth: true, scaleByIndividuals: false,
      rectangular: false, compact: false, colorExternals: false, pinOnDrag: true,
      grid: false, snap: false, showFPS: false, precision: 2, inputModality: 'auto' };
    this.transform = { x: 0, y: 0, k: 1 }; this.paused = false; this.ticks = 0; this.frame = 0;
    this.width = svg.clientWidth || 900; this.height = svg.clientHeight || 650;
    this.defs = element('defs');
    const marker = element('marker', { id: this.instanceId + '-arrow', viewBox: '0 -5 10 10', refX: 9, refY: 0,
      markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse', markerUnits: 'userSpaceOnUse' });
    marker.append(element('path', { d: 'M0,-4 L9,0 L0,4', fill: 'none', stroke: '#68758b', 'stroke-width': 1.5 }));
    this.defs.append(marker); svg.append(this.defs);
    const pattern = element('pattern', { id: this.instanceId + '-grid', width: 20, height: 20, patternUnits: 'userSpaceOnUse' });
    pattern.append(element('path', { d: 'M20 0 H0 V20', fill: 'none', stroke: '#b9c9dc', 'stroke-width': '.6' }));
    this.gridPattern = pattern;
    this.defs.append(pattern);
    this.grid = element('rect', { width: '100%', height: '100%', fill: `url(#${this.instanceId}-grid)`,
      'pointer-events': 'none', 'data-helper': 'grid' });
    this.grid.style.display = 'none'; svg.append(this.grid);
    this.viewport = element('g'); this.edgesLayer = element('g'); this.nodesLayer = element('g');
    this.viewport.append(this.edgesLayer, this.nodesLayer); svg.append(this.viewport);
    this.fpsLabel = element('text', { x: 14, y: this.height - 14, 'font-size': 11, fill: '#60718b',
      'font-family': 'system-ui, sans-serif', 'pointer-events': 'none', 'data-helper': 'fps' }, '0 FPS');
    this.fpsLabel.style.display = 'none'; svg.append(this.fpsLabel);
    svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', 'Ontology graph');
    svg.style.touchAction = 'none';
    this.handlers = {
      pointerdown: event => this.beginPan(event),
      pointermove: event => this.movePointer(event),
      pointerup: event => this.endPointer(event),
      pointercancel: event => this.endPointer(event),
      wheel: event => {
        event.preventDefault(); this.manual();
        this.zoomBy(Math.exp(-event.deltaY * 0.0015), this.screen(event));
      },
      click: event => { if (event.target === svg && !this.moved) this.callbacks.onSelect?.(null, event); },
      dblclick: event => {
        if (event.target === svg) { event.preventDefault(); this.callbacks.onDoubleClick?.(null, { ...this.world(this.screen(event)), originalEvent: event }); }
      }
    };
    for (const [type, handler] of Object.entries(this.handlers)) svg.addEventListener(type, handler, type === 'wheel' ? { passive: false } : undefined);
    this.observer = new ResizeObserver(() => {
      this.width = svg.clientWidth || this.width; this.height = svg.clientHeight || this.height;
      this.fpsLabel.setAttribute('y', this.height - 14);
      this.applyTransform(); if (this.autoFit) this.applyAutoFit();
    });
    this.observer.observe(svg); this.applyTransform();
  }

  setData(view, { focusIds, centerId } = {}) {
    const wasEmpty = !this.data.nodes.length;
    this.data = view;
    const links = new Map();
    for (const edge of view.edges) if (edge.source !== edge.target)
      links.set(JSON.stringify([edge.source, edge.target].sort()), edge);
    this.links = [...links.values()]; this.degrees = new Map();
    for (const edge of this.links) for (const id of [edge.source, edge.target])
      this.degrees.set(id, (this.degrees.get(id) || 0) + 1);
    const ids = new Set(view.nodes.map(node => node.id));
    for (const [id, dom] of this.nodeElements) if (!ids.has(id)) { dom.remove(); this.nodeElements.delete(id); }
    const edgeIds = new Set(view.edges.map(edge => edge.id));
    for (const [id, dom] of this.edgeElements) if (!edgeIds.has(id)) { dom.remove(); this.edgeElements.delete(id); this.edgeLabels.delete(id); }
    view.nodes.forEach((node, index) => {
      if (!this.positions.has(node.id)) {
        const adjacent = view.edges.find(edge => edge.source === node.id && this.positions.has(edge.target)
          || edge.target === node.id && this.positions.has(edge.source));
        const parent = adjacent && this.positions.get(adjacent.source === node.id ? adjacent.target : adjacent.source);
        const angle = (seed(node.id) % 6283) / 1000;
        const radius = parent ? 125 : 100 + Math.sqrt(index) * 55;
        this.positions.set(node.id, { x: (parent?.x || 0) + Math.cos(angle) * radius,
          y: (parent?.y || 0) + Math.sin(angle) * radius, vx: 0, vy: 0, pinned: false });
      }
      this.drawNode(node);
    });
    view.edges.forEach(edge => this.drawEdge(edge));
    if (focusIds?.length || centerId) this.autoFit = { ids: focusIds?.length ? focusIds : [centerId], centerOnly: !focusIds?.length };
    else if (wasEmpty && view.nodes.length) this.autoFit = { ids: [...ids], centerOnly: false };
    this.paint(); this.applyAutoFit(); this.wake();
  }

  drawNode(node) {
    let dom = this.nodeElements.get(node.id);
    if (!dom) {
      dom = element('g', { 'data-node-id': node.id, tabindex: 0, role: 'button' });
      dom.style.cursor = 'pointer';
      dom.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        event.stopPropagation(); this.manual();
        this.pointer = { type: 'node', id: node.id, start: this.screen(event), pointerId: event.pointerId, capture: dom };
        this.moved = false; dom.setPointerCapture(event.pointerId);
      });
      dom.addEventListener('click', event => {
        event.stopPropagation();
        this.handleClick(node.id, event);
      });
      dom.addEventListener('dblclick', event => {
        event.preventDefault(); event.stopPropagation();
        this.handleDoubleClick(node.id, event);
      });
      dom.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); this.callbacks.onSelect?.(node.id, event); }
      });
      this.nodesLayer.append(dom); this.nodeElements.set(node.id, dom);
    }
    dom.replaceChildren();
    const text = label(node, this.language);
    const individualCount = node.type === 'class' ? new Set(node.individuals || []).size : 0;
    const isBox = this.options.rectangular || ['individual', 'literal', 'datatype', 'property'].includes(node.type);
    const wanted = this.options.dynamicLabelWidth ? Math.min(this.options.labelWidth, Math.max(96, widthOf(text) + 24)) : this.options.labelWidth;
    const circleMaximum = Math.max(68, Math.min(104, this.options.labelWidth * .45));
    let width = isBox ? wanted : this.options.dynamicLabelWidth ? Math.max(68, Math.min(circleMaximum, widthOf(text) + 24)) : circleMaximum;
    const lines = linesOf(text, isBox ? wanted - 20 : width - 18, isBox ? 4 : 3);
    if (!isBox && individualCount && lines.length === 3) width = Math.max(width, 88);
    let height = isBox ? Math.max(44, lines.length * 17 + 22 + (individualCount ? 18 : 0)) : width;
    if (this.options.scaleByIndividuals && node.type === 'class') {
      const factor = 1 + Math.min(0.45, Math.sqrt(node.individuals?.length || 0) * 0.035);
      width *= factor; height *= factor;
    }
    const pos = this.positions.get(node.id); pos.width = width; pos.height = height; pos.box = isBox;
    let fill = COLORS[node.type] || COLORS.class;
    if (this.options.colorExternals && this.options.baseIRI && node.iri && !node.iri.startsWith(this.options.baseIRI.split('#')[0])
      && !node.iri.startsWith('_:')) fill = '#f7e0f1';
    const selected = node.id === this.selectedId || node.selected;
    const shape = isBox ? element('rect', { x: -width / 2, y: -height / 2, width, height, rx: node.type === 'literal' ? 7 : 13 })
      : element('circle', { r: width / 2 });
    shape.setAttribute('fill', selected ? '#ffe4a2' : fill);
    shape.setAttribute('stroke', selected ? '#b63e23' : '#5a6e8a');
    shape.setAttribute('stroke-width', selected ? 4 : 1.6);
    shape.setAttribute('vector-effect', 'non-scaling-stroke');
    if (node.type === 'expression') shape.setAttribute('stroke-dasharray', '5 3');
    dom.append(shape, element('title', {}, `${text}${individualCount ? ` (${individualCount})` : ''}\n${node.iri || node.value || ''}`));
    const textElement = element('text', { 'text-anchor': 'middle', 'font-family': 'system-ui, sans-serif', 'font-size': 12,
      fill: '#21304b', 'pointer-events': 'none' });
    lines.forEach((line, index) => textElement.append(element('tspan', { x: 0, y: (index - (lines.length - 1) / 2) * 14 + 4 - (individualCount ? 9 : 0) }, line)));
    dom.append(textElement);
    if (individualCount) {
      const badgeWidth = Math.max(24, widthOf(String(individualCount)) * .84 + 12);
      const badgeY = height / 2 - 14;
      dom.append(element('rect', { x: -badgeWidth / 2, y: badgeY - 7, width: badgeWidth, height: 14, rx: 7,
        fill: selected ? '#f6c66f' : '#c7ddf8', 'pointer-events': 'none' }));
      dom.append(element('text', { x: 0, y: badgeY + 3.5, 'text-anchor': 'middle', 'font-size': 10,
        'font-family': 'system-ui, sans-serif', 'font-weight': 650, fill: '#315176',
        class: 'individual-count', 'pointer-events': 'none' }, String(individualCount)));
    }
    if (pos.pinned) dom.append(element('circle', { cx: width / 2 - 5, cy: -height / 2 + 5, r: 4, fill: '#3155a6' }));
    dom.setAttribute('aria-label', text);
    dom.setAttribute('aria-pressed', String(!!selected));
    dom.setAttribute('data-individual-count', individualCount);
  }

  drawEdge(edge) {
    let dom = this.edgeElements.get(edge.id);
    if (!dom) {
      dom = element('g', { 'data-edge-id': edge.id, tabindex: 0, role: 'button' });
      dom.style.cursor = 'pointer';
      dom.addEventListener('pointerdown', event => { event.stopPropagation(); this.moved = false; this.manual(); });
      dom.addEventListener('click', event => { event.stopPropagation(); this.handleClick(edge.id, event); });
      dom.addEventListener('dblclick', event => { event.preventDefault(); event.stopPropagation(); this.handleDoubleClick(edge.id, event); });
      dom.addEventListener('keydown', event => { if (event.key === 'Enter') this.callbacks.onSelect?.(edge.id, event); });
      this.edgeElements.set(edge.id, dom); this.edgesLayer.append(dom);
    }
    dom.replaceChildren();
    const selected = edge.id === this.selectedId || edge.predicate === this.selectedId || edge.selected;
    const color = selected ? '#b63e23' : edge.kind === 'relation' ? '#9162b6' : edge.kind === 'value' ? '#3c8a6c' : '#71819a';
    const path = element('path', { fill: 'none', stroke: color, 'stroke-width': selected ? 3.5 : 1.5,
      'marker-end': `url(#${this.instanceId}-arrow)`, 'vector-effect': 'non-scaling-stroke' });
    if (['subclass', 'membership', 'disjoint'].includes(edge.kind)) path.setAttribute('stroke-dasharray', edge.kind === 'disjoint' ? '3 4' : '7 4');
    const hit = element('path', { fill: 'none', stroke: 'transparent', 'stroke-width': 14 });
    const text = edge.labels?.length ? label({ ...edge, iri: edge.predicate }, this.language)
      : edge.label || shortIRI(edge.predicate || edge.kind);
    const full = text + (edge.cardinality === undefined ? '' : ` (${edge.cardinality})`);
    const trimmed = linesOf(full, 128, 2);
    const width = Math.min(140, Math.max(34, ...trimmed.map(line => widthOf(line) * 11 / 12 + 10)));
    const height = trimmed.length * 14 + 6;
    const labelGroup = element('g');
    const visible = !this.options.compact || edge.kind !== 'subclass';
    if (visible) {
      labelGroup.append(element('rect', { x: -width / 2, y: -height / 2, width,
        height, rx: 4, fill: '#ffffff', 'fill-opacity': '.96', stroke: selected ? color : '#dfe5ef' }));
      const textElement = element('text', { 'text-anchor': 'middle', 'font-size': 11, 'font-family': 'system-ui, sans-serif', fill: color });
      trimmed.forEach((line, index) => textElement.append(element('tspan', { x: 0, y: (index - (trimmed.length - 1) / 2) * 14 + 4 }, line)));
      labelGroup.append(textElement);
    }
    const leader = element('path', { fill: 'none', stroke: color, 'stroke-width': .8,
      'stroke-opacity': .8, 'pointer-events': 'none' });
    this.edgeLabels.set(edge.id, { width, height, visible, group: labelGroup, leader });
    dom.append(path, hit, labelGroup, element('title', {}, `${full}\n${edge.predicate || ''}`), leader);
    dom.setAttribute('aria-label', full);
    dom.setAttribute('aria-pressed', String(!!selected));
  }

  paint(recalculateRoutes = true) {
    for (const node of this.data.nodes) {
      const pos = this.positions.get(node.id);
      this.nodeElements.get(node.id)?.setAttribute('transform', `translate(${pos.x},${pos.y})`);
    }
    const labels = [];
    const visiblePositions = new Map(this.data.nodes.map(node => [node.id, this.positions.get(node.id)]));
    const routeSignature = JSON.stringify([[...visiblePositions].map(([id, p]) => [id, p.x, p.y, p.width, p.height, p.box]),
      this.data.edges.map(edge => [edge.id, edge.source, edge.target])]);
    if (routeSignature !== this.routeSignature || recalculateRoutes && !this.routesOptimized) {
      this.routes = routeEdges(this.data.edges, visiblePositions, this.routeBends, recalculateRoutes);
      this.routeSignature = routeSignature; this.routesOptimized = recalculateRoutes;
    }
    for (const routed of this.routes) {
      const dom = this.edgeElements.get(routed.id), metrics = this.edgeLabels.get(routed.id);
      dom.children[0].setAttribute('d', routed.path); dom.children[1].setAttribute('d', routed.path);
      metrics.group.setAttribute('transform', `translate(${routed.anchors[0].x},${routed.anchors[0].y})`);
      if (metrics.visible) labels.push({ id: routed.id, width: metrics.width, height: metrics.height,
        anchors: routed.anchors, normal: routed.anchors[0].normal });
    }
    const nodeBoxes = this.data.nodes.map(node => this.positions.get(node.id));
    for (const placed of placeLabels(labels, nodeBoxes, this.routes)) {
      const metrics = this.edgeLabels.get(placed.id);
      Object.assign(metrics, placed);
      metrics.group.setAttribute('transform', `translate(${placed.x},${placed.y})`);
      const dx = placed.anchorX - placed.x, dy = placed.anchorY - placed.y;
      const boundary = Math.min(placed.width / 2 / Math.max(Math.abs(dx), .001), placed.height / 2 / Math.max(Math.abs(dy), .001));
      metrics.leader.setAttribute('d', boundary < 1
        ? `M${placed.anchorX},${placed.anchorY} L${placed.x + dx * boundary},${placed.y + dy * boundary}` : '');
    }
  }

  step() {
    const nodes = this.data.nodes.map(node => this.positions.get(node.id));
    const alpha = Math.max(.12, this.ticks / 180);
    const charge = Number.isFinite(Number(this.options.charge)) ? Math.abs(Number(this.options.charge)) : 650;
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j]; let dx = b.x - a.x, dy = b.y - a.y;
        let distance = Math.hypot(dx, dy);
        if (distance < .01) { dx = .1; dy = .07; distance = Math.hypot(dx, dy); }
        const minimum = Math.max(a.width, a.height) / 2 + Math.max(b.width, b.height) / 2 + 32;
        const strength = (charge * 9 / Math.max(distance * distance, 100)
          + Math.max(0, minimum - distance) * .035) * alpha;
        const fx = dx / distance * strength, fy = dy / distance * strength;
        a.vx -= fx; a.vy -= fy; b.vx += fx; b.vy += fy;
      }
    }
    for (const edge of this.links) {
      const a = this.positions.get(edge.source), b = this.positions.get(edge.target);
      if (!a || !b || a === b) continue;
      const dx = b.x - a.x, dy = b.y - a.y, distance = Math.hypot(dx, dy) || 1;
      const datatype = this.data.nodes.find(node => node.id === edge.target)?.type === 'datatype';
      const preferred = datatype ? Number(this.options.datatypeDistance) : Number(this.options.classDistance);
      const desired = Math.max(preferred || 180, a.width / 2 + b.width / 2 + 65)
        * Math.max(1, Math.sqrt(Math.max(this.degrees.get(edge.source), this.degrees.get(edge.target)) / 12));
      const strength = (distance - desired) / distance * Math.min(.3, Math.max(0, Number(this.options.lineStrength))) * alpha;
      a.vx += dx * strength; a.vy += dy * strength; b.vx -= dx * strength; b.vy -= dy * strength;
    }
    const gravity = Math.max(0, Number(this.options.gravity) || 0) * .04 * alpha;
    for (const pos of nodes) {
      if (pos.pinned || this.pointer?.id && this.positions.get(this.pointer.id) === pos) { pos.vx = pos.vy = 0; continue; }
      pos.vx = (pos.vx - pos.x * gravity) * .78;
      pos.vy = (pos.vy - pos.y * gravity) * .78;
      pos.x += Math.max(-18, Math.min(18, pos.vx)); pos.y += Math.max(-18, Math.min(18, pos.vy));
    }
  }

  wake() {
    this.ticks = 180;
    if (this.frame || this.paused || !this.data.nodes.length) return;
    const animate = timestamp => {
      this.frame = 0;
      if (this.paused) return;
      if (this.options.showFPS && this.lastFrame && timestamp > this.lastFrame)
        this.fpsLabel.textContent = `${Math.round(1000 / (timestamp - this.lastFrame))} FPS`;
      this.lastFrame = timestamp;
      if (this.ticks-- > 0) {
        this.step(); this.paint(this.ticks % 30 === 0); this.applyAutoFit();
        this.frame = requestAnimationFrame(animate);
      } else { this.applyAutoFit(); this.autoFit = null; }
    };
    this.frame = requestAnimationFrame(animate);
  }

  setSelection(id) {
    this.selectedId = id;
    this.data.nodes.forEach(node => this.drawNode(node));
    this.data.edges.forEach(edge => this.drawEdge(edge)); this.paint();
  }
  handleClick(id, event) {
    if (this.moved) return;
    const now = performance.now();
    if (this.lastClick?.id === id && now - this.lastClick.time < 450) {
      this.lastDoubleClick = { id, time: now }; this.lastClick = null;
      this.callbacks.onDoubleClick?.(id, event);
    } else {
      this.lastClick = { id, time: now }; this.callbacks.onSelect?.(id, event);
    }
  }
  handleDoubleClick(id, event) {
    if (this.lastDoubleClick?.id === id && performance.now() - this.lastDoubleClick.time < 500) return;
    this.callbacks.onDoubleClick?.(id, event);
  }
  setLanguage(language) {
    this.language = language;
    this.data.nodes.forEach(node => this.drawNode(node)); this.data.edges.forEach(edge => this.drawEdge(edge)); this.paint();
  }
  setOptions(options) {
    this.options = { ...this.options, ...options };
    this.grid.style.display = this.options.grid ? '' : 'none';
    this.fpsLabel.style.display = this.options.showFPS ? '' : 'none';
    this.data.nodes.forEach(node => this.drawNode(node)); this.data.edges.forEach(edge => this.drawEdge(edge));
    this.paint(); this.wake();
  }
  pause(value) {
    this.paused = !!value;
    if (this.paused) { cancelAnimationFrame(this.frame); this.frame = 0; this.fpsLabel.textContent = '0 FPS'; this.paint(); this.applyAutoFit(); }
    else this.wake();
    return this.paused;
  }
  togglePin(id) {
    const position = this.positions.get(id);
    if (!position) return false;
    position.pinned = !position.pinned;
    const node = this.data.nodes.find(item => item.id === id);
    if (node) this.drawNode(node);
    this.paint(); this.wake(); return position.pinned;
  }
  unpinAll() {
    this.positions.forEach(position => { position.pinned = false; });
    this.data.nodes.forEach(node => this.drawNode(node)); this.paint(); this.wake();
  }
  manual() { this.autoFit = null; this.callbacks.onManualView?.(); }
  screen(event) {
    const rect = this.svg.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }
  world(point) {
    return { x: (point.x - this.width / 2 - this.transform.x) / this.transform.k,
      y: (point.y - this.height / 2 - this.transform.y) / this.transform.k };
  }
  beginPan(event) {
    if (event.button !== 0 || event.target !== this.svg) return;
    this.manual(); this.moved = false;
    this.pointer = { type: 'pan', start: this.screen(event), x: this.transform.x, y: this.transform.y, pointerId: event.pointerId, capture: this.svg };
    this.svg.setPointerCapture(event.pointerId);
  }
  movePointer(event) {
    if (!this.pointer || this.pointer.pointerId !== event.pointerId) return;
    const point = this.screen(event), dx = point.x - this.pointer.start.x, dy = point.y - this.pointer.start.y;
    const touch = this.options.inputModality === 'touch' || this.options.inputModality === 'auto' && event.pointerType === 'touch';
    if (Math.hypot(dx, dy) > (touch ? 10 : 4)) this.moved = true;
    if (!this.moved) return;
    if (this.pointer.type === 'pan') {
      this.transform.x = this.pointer.x + dx; this.transform.y = this.pointer.y + dy; this.applyTransform();
    } else {
      const position = this.positions.get(this.pointer.id), world = this.world(point);
      const factor = 10 ** Math.min(4, Math.max(0, Number(this.options.precision) || 0));
      position.x = this.options.snap ? Math.round(world.x / 20) * 20 : Math.round(world.x * factor) / factor;
      position.y = this.options.snap ? Math.round(world.y / 20) * 20 : Math.round(world.y * factor) / factor;
      position.vx = position.vy = 0;
      this.paint(false); this.wake();
    }
  }
  endPointer(event) {
    if (!this.pointer || this.pointer.pointerId !== event.pointerId) return;
    if (this.pointer.type === 'node' && this.moved) {
      if (this.options.pinOnDrag) {
        const node = this.data.nodes.find(item => item.id === this.pointer.id);
        this.positions.get(this.pointer.id).pinned = true;
        if (node) this.drawNode(node);
      }
      this.paint();
    }
    if (this.pointer.capture.hasPointerCapture(event.pointerId)) this.pointer.capture.releasePointerCapture(event.pointerId);
    this.pointer = null;
  }
  applyTransform() {
    const transform = `translate(${this.width / 2 + this.transform.x},${this.height / 2 + this.transform.y}) scale(${this.transform.k})`;
    this.viewport.setAttribute('transform', transform);
    this.gridPattern.setAttribute('patternTransform', transform);
  }
  zoomBy(factor, point = { x: this.width / 2, y: this.height / 2 }) {
    this.manual(); const before = this.world(point);
    this.transform.k = Math.min(4, Math.max(.025, this.transform.k * factor));
    this.transform.x = point.x - this.width / 2 - before.x * this.transform.k;
    this.transform.y = point.y - this.height / 2 - before.y * this.transform.k;
    this.applyTransform();
  }
  applyAutoFit() {
    if (!this.autoFit) return;
    this.fitPositions(this.autoFit.ids, this.autoFit.centerOnly);
  }
  fitPositions(ids, centerOnly = false) {
    const list = this.data.nodes.filter(node => !ids || ids.includes(node.id)).map(node => this.positions.get(node.id));
    if (!list.length) { this.applyTransform(); return; }
    if (!centerOnly) {
      for (const edge of this.data.edges) {
        const box = this.edgeLabels.get(edge.id);
        if (box?.visible && Number.isFinite(box.x) && (!ids || ids.includes(edge.source) && ids.includes(edge.target))) list.push(box);
      }
    }
    if (!centerOnly) for (const route of this.routes || []) {
      if (ids && (!ids.includes(route.source) || !ids.includes(route.target))) continue;
      const xs = route.points.map(point => point.x), ys = route.points.map(point => point.y);
      const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
      list.push({ x: (minX + maxX) / 2, y: (minY + maxY) / 2, width: maxX - minX, height: maxY - minY });
    }
    const minX = Math.min(...list.map(pos => pos.x - pos.width / 2));
    const maxX = Math.max(...list.map(pos => pos.x + pos.width / 2));
    const minY = Math.min(...list.map(pos => pos.y - pos.height / 2));
    const maxY = Math.max(...list.map(pos => pos.y + pos.height / 2));
    const k = centerOnly ? this.transform.k : Math.min(1.5, Math.max(.025,
      Math.min(Math.max(120, this.width - 120) / Math.max(150, maxX - minX), Math.max(120, this.height - 120) / Math.max(150, maxY - minY))));
    this.transform = { x: -(minX + maxX) / 2 * k, y: -(minY + maxY) / 2 * k, k };
    this.applyTransform();
  }
  fit(ids) { this.autoFit = { ids: ids || this.data.nodes.map(node => node.id), centerOnly: false }; this.applyAutoFit(); }
  center(id) { this.autoFit = { ids: [id], centerOnly: true }; this.applyAutoFit(); }
  reset() { this.autoFit = null; this.unpinAll(); this.transform = { x: 0, y: 0, k: 1 }; this.fit(); }
  getView() {
    return { transform: { ...this.transform }, positions: [...this.positions].map(([id, point]) => [id, { ...point }]), paused: this.paused };
  }
  restoreView(view) {
    if (!view || typeof view !== 'object') return;
    for (const [id, point] of Array.isArray(view.positions) ? view.positions : []) {
      if (!this.positions.has(id) || !Number.isFinite(point.x) || !Number.isFinite(point.y)) continue;
      Object.assign(this.positions.get(id), { x: point.x, y: point.y, vx: 0, vy: 0, pinned: !!point.pinned });
    }
    const t = view.transform;
    if (t && [t.x, t.y, t.k].every(Number.isFinite) && t.k > 0) this.transform = { x: t.x, y: t.y, k: Math.max(.025, Math.min(4, t.k)) };
    this.autoFit = null; this.pause(!!view.paused); this.paint(); this.applyTransform();
  }
  exportSvg() {
    const clone = this.svg.cloneNode(true);
    clone.querySelectorAll('[data-helper]').forEach(node => node.remove());
    clone.setAttribute('xmlns', NS); clone.setAttribute('width', this.width); clone.setAttribute('height', this.height);
    clone.setAttribute('viewBox', `0 0 ${this.width} ${this.height}`);
    const background = element('rect', { width: '100%', height: '100%', fill: '#f5f7fb' });
    clone.insertBefore(background, clone.firstChild);
    return new XMLSerializer().serializeToString(clone);
  }
  async exportPng() {
    const blob = new Blob([this.exportSvg()], { type: 'image/svg+xml' }), url = URL.createObjectURL(blob);
    try {
      const image = new Image();
      await new Promise((resolve, reject) => { image.onload = resolve; image.onerror = () => reject(new Error('Image export failed')); image.src = url; });
      const canvas = document.createElement('canvas'); canvas.width = this.width * 2; canvas.height = this.height * 2;
      canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
      return await new Promise((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('Image export failed')), 'image/png'));
    } finally { URL.revokeObjectURL(url); }
  }
  destroy() {
    cancelAnimationFrame(this.frame); this.frame = 0; this.observer.disconnect();
    this.svg.replaceChildren(); this.nodeElements.clear(); this.edgeElements.clear(); this.positions.clear();
    for (const [type, handler] of Object.entries(this.handlers)) this.svg.removeEventListener(type, handler);
  }
}
