/* Ontology graph layout — DR-166. */
export const RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#';
export const RDFS = 'http://www.w3.org/2000/01/rdf-schema#';
export const OWL = 'http://www.w3.org/2002/07/owl#';
export const XSD = 'http://www.w3.org/2001/XMLSchema#';

export function shortIRI(iri = '') {
  if (iri.startsWith('_:')) return iri;
  const last = iri.replace(/[\/#:]$/, '').split(/[\/#:]/).pop();
  try { return decodeURIComponent(last || iri); } catch { return last || iri; }
}

export function label(entity, lang = 'zh') {
  if (!entity) return '';
  if (typeof entity === 'string') return shortIRI(entity);
  if (entity.type === 'literal') return entity.value === '' ? '""' : String(entity.value);
  if (lang === 'iri') return entity.iri || entity.id || '';
  const labels = entity.labels || [];
  const found = labels.find(item => item.language === lang)
    || labels.find(item => item.language?.split('-')[0] === lang)
    || labels.find(item => !item.language) || labels[0];
  if (found) return found.value;
  if (entity.expression) {
    const symbols = { unionOf: '∪', intersectionOf: '∩', complementOf: '¬', oneOf: '{}',
      someValuesFrom: '∃', allValuesFrom: '∀', hasValue: '∋', cardinality: '=', minCardinality: '≥', maxCardinality: '≤',
      qualifiedCardinality: '=', minQualifiedCardinality: '≥', maxQualifiedCardinality: '≤' };
    return (symbols[entity.expression.operator] || entity.expression.operator || 'OWL expression')
      + (entity.expression.cardinality === undefined ? '' : ` ${entity.expression.cardinality}`);
  }
  return shortIRI(entity.iri || entity.id);
}

function emptyModel() {
  return { schema: 'owl-workbench/1', metadata: {}, prefixes: {}, source: {}, warnings: [],
    triples: [], nodes: [], properties: [], edges: [], individuals: [], relations: [] };
}

export class GraphState {
  constructor(model = null) { this.load(model); }

  load(model) {
    this.model = Object.assign(emptyModel(), model || {});
    this.nodes = new Map(this.model.nodes.map(item => [item.id, item]));
    this.properties = new Map(this.model.properties.map(item => [item.id, item]));
    this.individuals = new Map(this.model.individuals.map(item => [item.id, item]));
    this.selectedId = null;
    this.expanded = new Set();
    this.focusId = null;
    this.classFocusId = null;
    this.showData = false;
    this.showRelations = false;
    this.local = false;
    this.projected = { nodes: [], edges: [] };
  }

  entity(id) {
    return this.nodes.get(id) || this.individuals.get(id) || this.properties.get(id)
      || this.projected.nodes.find(item => item.id === id)
      || this.projected.edges.find(item => item.id === id)
      || this.model.edges.find(item => item.id === id);
  }

  select(id) {
    const entity = this.entity(id);
    this.selectedId = entity ? id : null;
    if (this.individuals.has(id) || entity?.type === 'individual') {
      if (this.focusId !== id) {
        this.showData = this.showRelations = this.local = false;
      }
      this.focusId = id;
    } else if (this.nodes.has(id) || !entity) {
      this.focusId = null;
      this.showData = this.showRelations = this.local = false;
    }
    return entity;
  }

  toggleClass(id) {
    if (!this.nodes.get(id)?.individuals?.length) return false;
    this.select(id);
    if (this.expanded.has(id)) this.expanded.delete(id);
    else this.expanded.add(id);
    return this.expanded.has(id);
  }

  focusClass(id) {
    if (this.nodes.get(id)?.type !== 'class') return false;
    this.select(id);
    this.classFocusId = id;
    return true;
  }

  clearClassFocus() { this.classFocusId = null; }

  toggleData() {
    if (!this.individuals.has(this.focusId)) return false;
    this.showData = !this.showData;
    this.local = this.showData || this.showRelations;
    if (this.local) this.clearClassFocus();
    return this.showData;
  }

  toggleRelations() {
    if (!this.focusId) return false;
    this.showRelations = !this.showRelations;
    this.local = this.showData || this.showRelations;
    if (this.local) this.clearClassFocus();
    return this.showRelations;
  }

  returnToClasses() {
    this.showData = this.showRelations = this.local = false;
    this.clearClassFocus();
    if (this.focusId) this.selectedId = this.focusId;
  }

  captureView() {
    return { selectedId: this.selectedId, expanded: [...this.expanded], focusId: this.focusId, classFocusId: this.classFocusId,
      showData: this.showData, showRelations: this.showRelations, local: this.local };
  }

  restoreView(view = {}) {
    this.expanded = new Set((Array.isArray(view.expanded) ? view.expanded : []).filter(id => this.nodes.has(id)));
    this.focusId = this.individuals.has(view.focusId) ? view.focusId : null;
    this.showData = !!(this.focusId && view.showData);
    this.showRelations = !!(this.focusId && view.showRelations);
    this.local = this.showData || this.showRelations;
    this.classFocusId = !this.local && this.nodes.get(view.classFocusId)?.type === 'class' ? view.classFocusId : null;
    this.view();
    this.selectedId = this.entity(view.selectedId) ? view.selectedId : this.focusId;
  }

  view(filters = {}) {
    if (this.nodes.get(this.classFocusId)?.type !== 'class') this.clearClassFocus();
    const opts = { datatypes: true, objectProperties: true, subclasses: true,
      disjoint: true, expressions: true, minDegree: 0, ...filters };
    const visible = new Map(), edges = new Map();
    const putNode = (node) => {
      if (!node || visible.has(node.id)) return;
      visible.set(node.id, { ...node, selected: node.id === this.selectedId || node.id === this.focusId });
    };
    const putIndividual = id => {
      putNode({ ...(this.individuals.get(id) || { id, iri: id, labels: [], types: [], data: [] }), type: 'individual' });
    };
    const putEdge = edge => {
      if (visible.has(edge.source) && visible.has(edge.target)) {
        const property = this.properties.get(edge.predicate);
        edges.set(edge.id, { ...edge, labels: edge.labels || property?.labels || [],
          selected: edge.id === this.selectedId || edge.predicate === this.selectedId });
      }
    };
    if (this.local && this.focusId) {
      putIndividual(this.focusId);
      if (this.showRelations) {
        this.model.relations.forEach((edge, index) => {
          if (edge.source !== this.focusId && edge.target !== this.focusId) return;
          putIndividual(edge.source);
          putIndividual(edge.target);
          putEdge({ ...edge, id: `relation:${edge.source}|${edge.predicate}|${edge.target}`,
            type: 'relation', kind: 'relation' });
        });
      }
      if (this.showData) {
        (this.individuals.get(this.focusId)?.data || []).forEach((value, index) => {
          const id = `literal:${JSON.stringify([this.focusId, value.predicate, value.value, value.datatype || '', value.language || '', index])}`;
          putNode({ ...value, id, iri: '', type: 'literal', labels: [], subject: this.focusId });
          putEdge({ id: `value:${id}`, source: this.focusId, target: id, predicate: value.predicate, kind: 'value' });
        });
      }
    } else {
      let allowed = this.model.edges.filter(edge => (opts.objectProperties || edge.kind !== 'object')
        && (opts.subclasses || edge.kind !== 'subclass') && (opts.disjoint || edge.kind !== 'disjoint')
        && (opts.expressions || edge.kind !== 'expression') || edge.predicate === this.selectedId || edge.id === this.selectedId);
      const selectedProperty = this.properties.get(this.selectedId);
      const selectedEndpoints = new Set([...(selectedProperty?.domains || []), ...(selectedProperty?.ranges || [])]);
      this.model.edges.forEach(edge => {
        if (edge.id === this.selectedId || edge.predicate === this.selectedId) {
          selectedEndpoints.add(edge.source); selectedEndpoints.add(edge.target);
        }
      });
      const degrees = new Map();
      allowed.forEach(edge => {
        degrees.set(edge.source, (degrees.get(edge.source) || 0) + 1);
        degrees.set(edge.target, (degrees.get(edge.target) || 0) + 1);
      });
      this.model.nodes.forEach(node => {
        if (node.id === this.selectedId || node.id === this.classFocusId || selectedEndpoints.has(node.id) || this.expanded.has(node.id)
          || ((opts.datatypes || node.type !== 'datatype') && (opts.expressions || node.type !== 'expression')
            && (degrees.get(node.id) || 0) >= opts.minDegree)) putNode(node);
      });
      this.model.properties.forEach(property => {
        if ((property.type === 'annotation' || property.type === 'object' && !opts.objectProperties
          || property.type === 'data' && !opts.datatypes) && property.id !== this.selectedId) return;
        if (property.domains?.length && property.ranges?.length && property.type !== 'annotation') return;
        if (opts.minDegree > (property.domains?.length || 0) + (property.ranges?.length || 0)
          && property.id !== this.selectedId) return;
        putNode({ ...property, type: 'property', propertyType: property.type });
        for (const [field, predicate] of [['domains', RDFS + 'domain'], ['ranges', RDFS + 'range']]) {
          (property[field] || []).forEach(target => putEdge({ id: `property:${property.id}|${predicate}|${target}`,
            source: property.id, target, predicate, kind: 'propertyConstraint' }));
        }
      });
      const individuals = new Set();
      this.expanded.forEach(id => (this.nodes.get(id)?.individuals || []).forEach(iri => individuals.add(iri)));
      if (this.focusId) individuals.add(this.focusId);
      individuals.forEach(id => {
        putIndividual(id);
        const record = this.individuals.get(id);
        (record?.types || []).forEach(type => {
          if (id === this.focusId) putNode(this.nodes.get(type));
          if (this.expanded.has(type) || id === this.focusId) {
            putEdge({ id: `membership:${id}|${type}`, source: id, target: type, predicate: RDF + 'type', kind: 'membership' });
          }
        });
      });
      allowed.forEach(putEdge);
    }
    if (this.classFocusId && !this.local) {
      const related = new Set([this.classFocusId]);
      for (const edge of edges.values()) {
        if (edge.source === this.classFocusId || edge.target === this.classFocusId) {
          related.add(edge.source); related.add(edge.target);
        }
      }
      const unions = new Set([...related].filter(id => visible.get(id)?.expression?.operator === 'unionOf'));
      for (const [id, edge] of edges) {
        const throughUnion = unions.has(edge.source) && visible.get(edge.target)?.type === 'class'
          || unions.has(edge.target) && visible.get(edge.source)?.type === 'class';
        if (edge.source === this.classFocusId || edge.target === this.classFocusId || throughUnion) {
          related.add(edge.source); related.add(edge.target);
        } else edges.delete(id);
      }
      for (const id of visible.keys()) if (!related.has(id)) visible.delete(id);
    }
    this.projected = { nodes: [...visible.values()], edges: [...edges.values()] };
    return this.projected;
  }
}
