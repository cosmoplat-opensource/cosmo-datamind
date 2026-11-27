/* Wall-clock comparison of graph layout against the frozen reference — DR-190. Prints only; not an assertion. */
import {performance} from 'node:perf_hooks';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {referencePlaceLabels, referenceRouteEdges} from './interactive-ontology-graph-layout-reference.mjs';
import {scene} from './interactive-ontology-graph-layout.mjs';

const product = file => import(pathToFileURL(path.resolve('ui/modules/interactive-ontology-graph', file)));
const {routeEdges} = await product('edgeRouting.js');
const {placeLabels} = await product('labelLayout.js');
const time = fn => { const start = performance.now(); const value = fn(); return [value, performance.now() - start]; };

for (const [nodes, edges] of [[100, 200], [300, 600]]) {
  const {positions, edges: list} = scene(nodes, edges - 5, 11);
  for (const [name, route, place] of [['reference', referenceRouteEdges, referencePlaceLabels], ['current', routeEdges, placeLabels]]) {
    const [routes, routeMs] = time(() => route(list, positions, new Map(), true));
    const labels = routes.map((item, index) => ({id: item.id, width: 34 + (index % 7) * 15, height: 20 + (index % 2) * 14,
      anchors: item.anchors, normal: item.anchors[0].normal}));
    const [, labelMs] = time(() => place(labels, [...positions.values()], routes));
    console.log(`${nodes} nodes / ${edges} edges ${name}: routing ${routeMs.toFixed(0)} ms, labels ${labelMs.toFixed(0)} ms`);
  }
}
