/* 固定版本的图谱编辑副本 —— DR-188。 */
import {label, OWL, RDF, RDFS, XSD} from './model.js';

function element(tag, text = '') {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}

function button(parent, title, click) {
  const node = element('button', title);
  node.type = 'button';
  node.onclick = click;
  parent.append(node);
  return node;
}

function field(parent, title, options, initial = '') {
  const wrapper = element('label', title);
  let input;
  if (options) {
    input = element('select');
    for (const [value, name] of options) input.add(new Option(name, value));
  } else {
    input = element('input');
    input.maxLength = 4096;
  }
  if (initial || !options) input.value = initial;
  input.setAttribute('aria-label', title);
  wrapper.append(input);
  parent.append(wrapper);
  return input;
}

const empty = ['', '（无）'];
const kinds = {class: '类别', individual: '实例', object: '对象属性', data: '数据属性'};
const standard = id => [RDF, RDFS, OWL, XSD].some(ns => id.startsWith(ns));
const choice = item => [item.id, label(item)];
const datatypes = ['string', 'integer', 'decimal', 'boolean', 'date', 'dateTime'].map(t => [XSD + t, t]);

export function mountEditor(host, {request, present, select, capture, restore, notice}) {
  const bar = element('div');
  bar.className = 'interactive-ontology-graph-toolbar graph-edit-toolbar';
  host.querySelector('.interactive-ontology-graph-notice').before(bar);
  const dialog = element('dialog');
  dialog.className = 'graph-edit-dialog';
  host.append(dialog);
  let active = false, dead = false, pending = null, model = null, cursor = 0;
  let history = [], views = [], source = null;
  const operations = () => history.slice(0, cursor);
  const classes = () => model.nodes.filter(n => n.type === 'class' && !n.id.startsWith('_:')).map(choice);
  const instances = () => model.individuals.map(choice);
  const properties = type => model.properties.filter(p => p.type === type).map(choice);

  function cancelRequest() {
    pending?.abort();
    pending = null;
  }

  async function run(task, errorTarget = notice) {
    if (pending || dead) return;
    const controller = new AbortController();
    pending = controller;
    controls();
    try {
      await task(async (ops, format = 'view') => {
        const result = await request(ops, format, controller.signal);
        if (controller.signal.aborted || dead) throw new DOMException('已取消', 'AbortError');
        return result;
      });
    } catch (error) {
      if (error.name !== 'AbortError') errorTarget(error.message);
    } finally {
      if (pending === controller) pending = null;
      if (!dead) controls();
    }
  }

  function display(result) {
    const context = result.view?.context;
    if (context?.kind !== 'edit-copy' || context.key !== source.context.key || context.revision !== source.context.revision) {
      throw new Error('编辑响应与来源版本不匹配，请重新打开图谱。');
    }
    if (!present(result.view)) throw new Error('编辑后的图谱超过同屏上限，请缩小显示范围后重试。');
    model = result.view;
  }

  function commit(op, result, focus) {
    const before = capture();
    display(result);
    history = [...operations(), op];
    views = views.slice(0, cursor);
    views.push(before);
    cursor++;
    select(focus, {data: op.op === 'remove_value' || op.op === 'add_value'});
    views[cursor] = capture();
    notice(`编辑副本已更新，移除 ${result.impact.removed} 条、新增 ${result.impact.added} 条断言。`);
    controls();
  }

  function jump(next) {
    run(async load => {
      const result = await load(history.slice(0, next));
      views[cursor] = capture();
      display(result);
      cursor = next;
      restore(views[next]);
      notice(`编辑副本已恢复到第 ${cursor} 次操作。`);
    });
  }

  function modal(title, submitTitle) {
    dialog.replaceChildren();
    const form = element('form');
    const heading = element('h3', title);
    const content = element('div');
    const error = element('p');
    error.setAttribute('role', 'alert');
    const actions = element('footer');
    const submit = element('button', submitTitle);
    submit.type = 'submit';
    actions.append(submit);
    button(actions, '取消', () => dialog.close());
    form.append(heading, content, error, actions);
    dialog.append(form);
    dialog.setAttribute('aria-label', title);
    dialog.showModal();
    return {content, submit, error: message => { error.textContent = message; },
      onSave(fn) { form.onsubmit = event => { event.preventDefault(); fn(); }; }};
  }
  dialog.onclose = () => { cancelRequest(); controls(); };
  dialog.oncancel = () => cancelRequest();

  function editForm(title, build) {
    if (pending || dialog.open) return;
    const form = modal(title, '应用');
    const read = build(form.content);
    form.onSave(() => run(async load => {
      const [op, focus] = read();
      const result = await load([...operations(), op]);
      commit(op, result, focus);
      dialog.close();
    }, form.error));
  }

  function deletion(op, title, focus = null) {
    if (pending || dialog.open) return;
    const form = modal(title, '确认删除');
    form.content.append(element('p', '正在计算完整 RDF 的影响范围…'));
    form.submit.disabled = true;
    run(async load => {
      const result = await load([...operations(), op]);
      const {removed, added, sample} = result.impact;
      form.content.replaceChildren(element('code', op.iri || op.source), element('p', `移除 ${removed} 条断言，新增 ${added} 条身份声明。`));
      if (sample.length) {
        form.content.append(element('pre', sample.join('\n')));
        if (removed > sample.length) form.content.append(element('p', `仅列出前 ${sample.length} 条。`));
      }
      form.submit.disabled = false;
      form.onSave(() => {
        form.submit.disabled = true;
        try { commit(op, result, focus); dialog.close(); }
        catch (error) { form.error(error.message); form.submit.disabled = false; }
      });
    }, form.error);
  }

  function entityForm(kind, selectedClass = '') {
    editForm('新增' + kinds[kind], parent => {
      const name = field(parent, '名称');
      name.required = true;
      const id = field(parent, 'IRI');
      id.required = true;
      const used = new Set([...model.nodes, ...model.individuals, ...model.properties].map(n => n.id));
      const first = selectedClass || classes().find(([id]) => !standard(id))?.[0] || 'urn:ontology:';
      const base = first.slice(0, Math.max(first.lastIndexOf('#'), first.lastIndexOf('/'), first.lastIndexOf(':')) + 1);
      let manual = false;
      id.oninput = () => { manual = !!id.value; };
      name.oninput = () => {
        if (manual) return;
        const local = name.value.trim().normalize('NFKC').replace(/[^\p{L}\p{N}_.-]+/gu, '_');
        let suggestion = base + local, suffix = 2;
        while (used.has(suggestion)) suggestion = base + local + '_' + suffix++;
        id.value = local ? suggestion : '';
      };
      const prop = kind === 'data' || kind === 'object';
      const owner = field(parent, prop ? '定义域' : kind === 'class' ? '父类' : '所属类别', [empty, ...classes()], selectedClass);
      const range = prop ? field(parent, '值域', [empty, ...(kind === 'data' ? datatypes : classes())]) : null;
      return () => [{op: 'create', kind, name: name.value, iri: id.value,
        ...(prop ? {domain: owner.value, range: range.value} : {parent: owner.value})}, id.value];
    });
  }

  function editEntity(entity) {
    editForm('编辑实体', parent => {
      const id = field(parent, 'IRI', null, entity.id);
      const name = field(parent, '中文名称', null, entity.labels.find(l => l.language.toLowerCase() === 'zh')?.value || '');
      const comment = field(parent, '中文说明', null, entity.comment_zh || '');
      return () => [{op: 'edit', iri: entity.id, new_iri: id.value, name: name.value, comment: comment.value}, id.value];
    });
  }

  function relation() {
    editForm('新增关系', parent => {
      const type = field(parent, '关系类型', [
        [RDFS.subClassOf, '继承'], [OWL.equivalentClass, '等价'], [OWL.disjointWith, '互斥'],
        [RDF.type, '实例归属'], ['instance', '实例关系'], [RDFS.domain, '属性定义域'], [RDFS.range, '属性值域']]);
      const fields = element('div');
      parent.append(fields);
      let source, target, property;
      function endpoints() {
        fields.replaceChildren();
        const constraint = [RDFS.domain, RDFS.range].includes(type.value);
        const subjects = constraint ? model.properties.map(choice) :
          [RDF.type, 'instance'].includes(type.value) ? instances() : classes();
        source = field(fields, '起点', subjects);
        property = type.value === 'instance' ? field(fields, '对象属性', properties('object')) : null;
        function targets() {
          target?.parentElement?.remove();
          const dataRange = type.value === RDFS.range && model.properties.some(p => p.id === source.value && p.type === 'data');
          target = field(fields, '终点', type.value === 'instance' ? instances() : dataRange ? datatypes : classes());
        }
        source.onchange = targets;
        targets();
      }
      type.onchange = endpoints;
      endpoints();
      return () => [{op: 'link', source: source.value, predicate: property ? property.value : type.value,
        target: target.value}, source.value];
    });
  }

  function addValue(entity) {
    editForm('新增数据值', parent => {
      const prop = field(parent, '数据属性', properties('data'));
      const value = field(parent, '值');
      const lang = field(parent, '语言标签');
      const type = field(parent, '数据类型', [empty, ...datatypes]);
      const useRange = () => {
        const range = model.properties.find(p => p.id === prop.value)?.ranges[0];
        type.value = datatypes.some(([id]) => id === range) ? range : '';
      };
      prop.onchange = useRange;
      useRange();
      return () => [{op: 'add_value', source: entity.id, predicate: prop.value, value: value.value,
        language: lang.value, datatype: type.value}, entity.id];
    });
  }

  function removeValue(subject, value) {
    deletion({op: 'remove_value', source: subject, predicate: value.predicate, value: value.value,
      language: value.language || '', datatype: value.datatype || ''}, '删除此数据值', subject);
  }

  function unlink(edge) {
    deletion({op: 'unlink', source: edge.source, predicate: edge.predicate, target: edge.target}, '解除关系', edge.source);
  }

  function download() {
    const fmt = bar.querySelector('select').value;
    run(async load => {
      const {content} = await load(operations(), fmt);
      const mime = {owl: 'application/rdf+xml', ttl: 'text/turtle', json: 'application/ld+json'}[fmt];
      const url = URL.createObjectURL(new Blob([content], {type: mime}));
      const link = element('a');
      link.href = url;
      link.download = 'ontology-copy.' + fmt;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }

  let exportFormat = 'owl';
  function controls() {
    bar.replaceChildren();
    if (!active) {
      button(bar, '编辑副本', () => run(async load => {
        const view = capture();
        const result = await load([]);
        active = true;
        try { display(result); restore(view); } catch (error) { active = false; throw error; }
        notice('正在编辑临时副本，离开页面前请导出。');
      }));
    } else {
      for (const [kind, title] of Object.entries(kinds)) button(bar, '新增' + title, () => entityForm(kind));
      button(bar, '新增关系', relation);
      button(bar, '撤销', () => jump(cursor - 1)).disabled = cursor === 0;
      button(bar, '重做', () => jump(cursor + 1)).disabled = cursor === history.length;
      const format = field(bar, '导出格式', [['owl', 'OWL'], ['ttl', 'Turtle'], ['json', 'JSON-LD']], exportFormat);
      format.onchange = () => { exportFormat = format.value; };
      button(bar, '导出副本', download);
      button(bar, '返回原图', () => {
        const leave = () => {
          active = false; history = []; views = []; cursor = 0;
          present(source); model = null; controls();
        };
        if (!cursor) { leave(); return; }
        const form = modal('离开编辑副本', '离开');
        form.content.append(element('p', '离开将丢弃当前副本，请先导出需要保留的修改。'));
        form.onSave(() => { leave(); dialog.close(); });
      });
    }
    if (pending) bar.querySelectorAll('button, select').forEach(node => { node.disabled = true; });
  }
  controls();
  return {
    source(view) { source = view; },
    details(parent, entity) {
      if (!active || !entity) return;
      if (['class', 'object', 'data', 'individual'].includes(entity.type) && !standard(entity.id)) {
        if (entity.type === 'class') button(parent, '新增实例', () => entityForm('individual', entity.id));
        button(parent, '编辑实体', () => editEntity(entity));
        button(parent, '删除实体', () => deletion({op: 'delete', iri: entity.id}, '删除实体'));
        if (entity.type === 'individual') button(parent, '新增数据值', () => addValue(entity));
        for (const [field, predicate, title] of [['domains', RDFS.domain, '定义域'], ['ranges', RDFS.range, '值域'], ['types', RDF.type, '归属']]) {
          for (const target of entity[field] || []) button(parent, '解除' + title + ' · ' + label(target),
            () => unlink({source: entity.id, predicate, target}));
        }
      } else if (['subclass', 'equivalent', 'disjoint', 'membership', 'relation', 'propertyConstraint'].includes(entity.kind)) {
        button(parent, '解除关系', () => unlink(entity));
      } else if (entity.type === 'literal' && model.properties.some(p => p.id === entity.predicate && p.type === 'data')) {
        button(parent, '删除此数据值', () => removeValue(entity.subject, entity));
      } else if (['object', 'data'].includes(entity.kind)) {
        button(parent, '查看属性定义', () => select(entity.predicate));
      }
    },
    value(row, subject, value) {
      if (active && model?.properties.some(p => p.id === value.predicate && p.type === 'data')) button(row, '删除此数据值', () => removeValue(subject, value));
    },
    edge(row, edge) { if (active) button(row, '解除关系', () => unlink(edge)); },
    destroy() { dead = true; cancelRequest(); dialog.remove(); }
  };
}
