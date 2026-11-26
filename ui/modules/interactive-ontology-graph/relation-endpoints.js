/* Filter object property endpoints on the current projected edit copy — DR-190. */
import {OWL} from './model.js';

function childIndex(model, includeEquivalent) {
  const children = new Map();
  function add(parent, child) {
    if (!children.has(parent)) children.set(parent, []);
    children.get(parent).push(child);
  }
  for (const edge of model.edges) {
    if (edge.source.startsWith('_:') || edge.target.startsWith('_:')) continue;
    if (edge.kind === 'subclass') add(edge.target, edge.source);
    if (includeEquivalent && edge.kind === 'equivalent') {
      add(edge.target, edge.source);
      add(edge.source, edge.target);
    }
  }
  return children;
}

export function subclassTargets(model, child, classes) {
  const children = childIndex(model, false);
  const pending = [child], excluded = new Set();
  while (pending.length) {
    const id = pending.pop();
    if (excluded.has(id)) continue;
    excluded.add(id);
    for (const next of children.get(id) || []) pending.push(next);
  }
  return classes.filter(([id]) => !excluded.has(id));
}

export function individualFilter(model) {
  const children = childIndex(model, true);
  const unions = new Map(model.nodes.filter(n => n.expression?.operator === 'unionOf')
    .map(n => [n.id, n.expression.operands]));
  return (constraints, individuals = model.individuals) => {
    let candidates = individuals;
    for (const constraint of constraints) {
      const pending = [constraint], visited = new Set(), named = new Set();
      while (pending.length) {
        const term = pending.pop();
        if (visited.has(term)) continue;
        visited.add(term);
        if (term.startsWith('_:')) {
          for (const member of unions.get(term) || []) pending.push(member);
        } else {
          named.add(term);
          for (const child of children.get(term) || []) pending.push(child);
        }
      }
      if (!named.has(OWL + 'Thing')) candidates = candidates.filter(n => n.types.some(t => named.has(t)));
      if (!candidates.length) break;
    }
    return candidates;
  };
}

export function eligibleIndividuals(model, constraints) {
  if (!constraints.length) return model.individuals;
  return individualFilter(model)(constraints);
}
