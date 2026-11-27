/* Exact-equivalence and work-bound oracles for graph routing and label placement — DR-190. */
import assert from 'node:assert/strict';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {referencePlaceLabels, referenceRouteEdges} from './interactive-ontology-graph-layout-reference.mjs';

const product = file => import(pathToFileURL(path.resolve('ui/modules/interactive-ontology-graph', file)));
const {routeEdges} = await product('edgeRouting.js');
const {placeLabels} = await product('labelLayout.js');

export function scene(nodes, edges, seed, {spread = 3000, loops = true} = {}) {
  let state = seed;
  const random = () => (state = (state * 1103515245 + 12345) % 2147483648) / 2147483648;
  const positions = new Map();
  for (let i = 0; i < nodes; i++) {
    const box = i % 3 === 0;
    positions.set('n' + i, {x: random() * spread, y: random() * spread, width: box ? 96 + (i % 5) * 9 : 68 + (i % 4) * 9,
      height: box ? 44 + (i % 2) * 17 : 68 + (i % 4) * 9, box, vx: 0, vy: 0, pinned: false});
  }
  const list = [];
  for (let i = 0; i < edges; i++) {
    const a = Math.floor(random() * nodes);
    let b = Math.floor(random() * nodes);
    if (!loops && a === b) b = (b + 1) % nodes;
    list.push({id: 'e' + i, source: 'n' + a, target: 'n' + b});
  }
  // Parallel edges share a route group; include them deliberately.
  for (let i = 0; i < Math.min(5, edges); i++) list.push({...list[i], id: 'p' + i});
  return {positions, edges: list};
}

function labelsFor(routes) {
  return routes.map((route, index) => ({id: route.id, width: 34 + (index % 7) * 15, height: 20 + (index % 2) * 14,
    anchors: route.anchors, normal: route.anchors[0].normal}));
}

function compare(positions, edges, optimize, seedBends) {
  const expectedBends = new Map(seedBends), actualBends = new Map(seedBends);
  const expected = referenceRouteEdges(edges, positions, expectedBends, optimize);
  const actual = routeEdges(edges, positions, actualBends, optimize);
  assert.deepEqual(actual, expected, 'routing must match the reference exactly');
  assert.deepEqual([...actualBends], [...expectedBends], 'remembered bends must match the reference');
  const labels = labelsFor(expected), boxes = [...positions.values()];
  assert.deepEqual(placeLabels(labels, boxes, actual), referencePlaceLabels(labels, boxes, expected),
    'label placement must match the reference exactly');
}

const cases = {
  routing_and_labels_match_reference() {
    for (let seed = 1; seed <= 36; seed++) {
      const nodes = 2 + (seed * 7) % 90, edges = 1 + (seed * 13) % 160;
      const dense = seed % 4 === 0;
      const {positions, edges: list} = scene(nodes, edges, seed, {spread: dense ? 500 : 2500, loops: seed % 3 !== 0});
      const bends = new Map(seed % 2 ? [] : [[JSON.stringify(['n0', 'n1'].sort()), 120], ['["gone","stale"]', 60]]);
      compare(positions, list, true, bends);
      compare(positions, list, false, bends);
    }
  },
  limit_scale_matches_reference() {
    for (const seed of [11, 29]) {
      const {positions, edges} = scene(300, 600 - 5, seed);
      compare(positions, edges, true, new Map());
    }
  },
  // Deterministic work counts replace wall-clock assertions: each pass examines a small share of a full scan.
  work_is_pruned_at_limit_scale() {
    for (const seed of [11, 29]) {
      const {positions, edges} = scene(300, 600 - 5, seed);
      const routeStats = {}, labelStats = {};
      const routes = routeEdges(edges, positions, new Map(), true, routeStats);
      placeLabels(labelsFor(routes), [...positions.values()], routes, labelStats);
      const {candidates, nodes, routes: routed} = routeStats;
      assert(candidates > 0 && labelStats.considers > 0, 'work counters must be reported');
      assert(routeStats.nodeTests <= 0.15 * candidates * nodes, `node checks ${routeStats.nodeTests}`);
      assert(routeStats.routeTests <= 0.02 * candidates * 12 * routed * 12, `segment pair checks ${routeStats.routeTests}`);
      assert(labelStats.segmentTests <= 0.01 * labelStats.considers * labelStats.segments, `label segment checks ${labelStats.segmentTests}`);
      assert(labelStats.obstacleTests <= 0.02 * labelStats.considers * labelStats.obstacles, `label obstacle checks ${labelStats.obstacleTests}`);
    }
  },
  non_finite_positions_fall_back_to_full_scan() {
    const {positions, edges} = scene(40, 80, 7);
    positions.get('n3').x = Number.NaN;
    compare(positions, edges.filter(edge => edge.source !== 'n3' && edge.target !== 'n3'), true, new Map());
  },
};

if (process.argv[1] === new URL(import.meta.url).pathname) {
  assert(Object.hasOwn(cases, process.argv[2]), 'unknown layout case');
  cases[process.argv[2]]();
}
