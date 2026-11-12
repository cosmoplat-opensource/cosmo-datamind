"""Question-driven semantic proposals with replayable data checks (DR-093).

Data supports bindings and queries, not arbitrary definitions or formulas.
Proposals remain candidate/not_reviewed. No global rejection state is shared.
"""
from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import time

from data_catalog import qi, schema
from srv_context import sql_code, sql_is_readonly

MAX_Q = 1000
BATCH = 40
MAX_SQL = 8000


def proposal_id(kind, c):
    binding = {k: c.get(k) for k in ('name', 'table', 'column', 'filter', 'name_col', 'formula', 'uses', 'uses_ids', 'statement', 'check_sql')}
    binding['codes'] = [x['code'] for x in c.get('codes') or []]
    return hashlib.sha256((kind + json.dumps(binding, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()[:20]

PROMPT = """为数据库提议业务概念,仅输出JSON:
{{"concepts":[{{"name":"名称","aliases":["别名"],"definition":"定义","table":"表","column":"列",
"filter":"WHERE条件或空串","aggregation":"汇总建议"}}],
"themes":[{{"name":"主题","aliases":[],"definition":"说明","table":"表","name_col":"名称列","codes":["真实代码"]}}],
"derived":[{{"name":"派生指标","aliases":[],"definition":"说明","formula":"计算建议","uses":["概念名"]}}],
"caveats":[{{"statement":"数据使用建议","check_sql":"证据SELECT"}}]}}
所有表、列、代码必须来自结构。定义、汇总和公式只是提议,不要宣称人工确认或认证。
不要为了凑数量重复提议。业务问题仅用于构建,验收问题不在此输入。
结构与字段说明:
{schema}
业务问题:
{questions}
"""


def normalize_questions(questions):
    items = questions if isinstance(questions, list) else []
    valid = [q.strip()[:500] for q in items if isinstance(q, str) and q.strip()]
    return valid[:MAX_Q], {"questions_used": min(len(valid), MAX_Q), "questions_omitted": max(0, len(valid) - MAX_Q),
                          "invalid_questions": len(items) - len(valid)}


def _run(con, sql, timeout=15.0, limit=5, deadline=None, allowed_tables=None, read_tables=None):
    code = sql_code(sql) if isinstance(sql, str) else None
    if not code or not sql_is_readonly(sql) or len(sql) > MAX_SQL or ';' in code.strip().rstrip(';'):
        raise ValueError("只接受单条有界只读查询")
    deadline = min(deadline, time.monotonic() + timeout) if deadline is not None else time.monotonic() + timeout
    if time.monotonic() >= deadline:
        raise ValueError('取证预算已耗尽')

    allowed = {str(t).casefold() for t in allowed_tables} if allowed_tables is not None else None

    def authorize(op, a, b, db, source):
        if op == sqlite3.SQLITE_READ:
            if allowed is not None and (str(a).casefold() not in allowed or db not in (None, 'main')):
                return sqlite3.SQLITE_DENY
            if read_tables is not None:
                read_tables.add(str(a))
        if op == sqlite3.SQLITE_FUNCTION and str(b or a).lower() in ('load_extension', 'writefile', 'readfile'):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK if op in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ,
                                          sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE) else sqlite3.SQLITE_DENY
    con.set_authorizer(authorize)
    con.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    try:
        return con.execute(sql).fetchmany(limit)
    finally:
        con.set_progress_handler(None, 0)
        con.set_authorizer(None)


def _reject(rejected, c, reason):
    if rejected is not None:
        rejected.append({"name": str(c.get("name") or c.get("statement") or '')[:80], "reason": reason})
    return None


def _base(c):
    aliases = c.get("aliases") if isinstance(c.get("aliases"), list) else []
    return {"name": str(c.get("name") or '')[:80], "definition": str(c.get("definition") or '')[:1000],
            "aliases": [str(a)[:100] for a in aliases if isinstance(a, str) and a.strip()][:12],
            "status": "candidate", "semantic_status": "not_reviewed", "origin": "workload_proposal"}


def verify_concept(con, c, tables, entity_col=None, rejected=None, deadline=None):
    t = tables.get(str(c.get("table") or '').casefold())
    if not t:
        return _reject(rejected, c, "table_missing")
    cols = {r[1].casefold(): r[1] for r in con.execute(f"PRAGMA table_info({qi(t)})")}
    col = str(c.get("column") or '')
    if not c.get("name") or not col or col.casefold() not in cols:
        return _reject(rejected, c, "name_or_column_missing")
    flt = str(c.get("filter") or '').strip()
    sql = f"SELECT COUNT(*) FROM {qi(t)}" + (f" WHERE {flt}" if flt else '')
    try:
        n = _run(con, sql, deadline=deadline, allowed_tables=tables)[0][0]
        if not n:
            return _reject(rejected, c, "no_matching_rows")
        ev = {"rows": n, "sql": sql, "query_verified": True, "method": "sql_count"}
        if entity_col and str(entity_col).casefold() in cols:
            esql = f"SELECT COUNT(DISTINCT {qi(cols[entity_col.casefold()])}) FROM {qi(t)}" + (f" WHERE {flt}" if flt else '')
            ev["entities"] = _run(con, esql, deadline=deadline, allowed_tables=tables)[0][0]
            ev["entity_sql"] = esql
    except (sqlite3.Error, ValueError):
        return _reject(rejected, c, "query_failed")
    return {**_base(c), "table": t, "column": cols[col.casefold()], "filter": flt,
            "aggregation": str(c.get("aggregation") or '')[:500], "evidence": ev}


def verify_theme(con, th, tables, entity_of, rejected=None, deadline=None):
    t = tables.get(str(th.get("table") or '').casefold())
    cols = {r[1].casefold(): r[1] for r in con.execute(f"PRAGMA table_info({qi(t)})")} if t else {}
    nc = cols.get(str(th.get("name_col") or '').casefold())
    if not t or not nc or not th.get("name"):
        return _reject(rejected, th, "theme_binding_missing")
    codes = th.get("codes") if isinstance(th.get("codes"), list) else []
    kept = []
    for code in dict.fromkeys(str(x) for x in codes if isinstance(x, str)):
        if deadline is not None and time.monotonic() >= deadline:
            return _reject(rejected, th, 'budget_exhausted')
        sql = f"SELECT COUNT(*) FROM {qi(t)} WHERE {qi(nc)}='{code.replace(chr(39), chr(39)*2)}'"
        try:
            n = _run(con, sql, deadline=deadline, allowed_tables=tables)[0][0]
        except (sqlite3.Error, ValueError):
            return _reject(rejected, th, 'query_failed')
        if n:
            kept.append({"code": code, "rows": n, "sql": sql})
        else:
            _reject(rejected, th, "theme_code_missing")
        if len(kept) >= 40:
            break
    if len(kept) < 2:
        return _reject(rejected, th, "theme_needs_two_codes")
    return {**_base(th), "table": t, "name_col": nc, "codes": kept,
            "evidence": {"query_verified": True, "method": "sql_count_per_code"}}


def verify_caveat(con, c, tables=None, rejected=None, deadline=None):
    sql = str(c.get("check_sql") or '')
    dependencies = set()
    try:
        rows = _run(con, sql, deadline=deadline, allowed_tables=tables, read_tables=dependencies)
    except (sqlite3.Error, ValueError):
        return _reject(rejected, c, "caveat_query_failed")
    try:
        json.dumps(rows, allow_nan=False)
    except (ValueError, TypeError):
        return _reject(rejected, c, 'caveat_nonserializable_evidence')
    if not dependencies or not rows or all(v in (None, 0, '') for r in rows for v in r):
        return _reject(rejected, c, "caveat_no_positive_evidence")
    return {"statement": str(c.get("statement") or '')[:1000], "check_sql": sql, "status": "candidate",
            "semantic_status": "not_reviewed", "origin": "workload_proposal",
            "evidence": {"sql": sql, "query_verified": True, "result": rows[:3], "tables": sorted(dependencies)}}


def schema_brief(con, ir, docs):
    lines = []
    tables = schema(con)
    for o in ir.get("objects") or []:
        t = o.get("table")
        if t not in tables or o.get("kind") == "action":
            continue
        d = docs.get(t.casefold()) or {}
        lines.append(t + ': ' + ', '.join(c + (f" [{d[c.casefold()][:100]}]" if d.get(c.casefold()) else '')
                                         for c, _ in tables[t][:60]))
        cat = o.get("catalog") or {}
        if cat.get("entries"):
            lines.append(str(cat["name_col"]) + ': ' + ', '.join(str(e["value"]) for e in cat["entries"]))
    return '\n'.join(lines)


def build(con, ir, docs, questions, ask, extract, deadline=None):
    qs, meta = normalize_questions(questions)
    result = {**meta, "concepts": [], "themes": [], "derived": [], "caveats": [], "rejected": [],
              "proposed": 0, "accepted": 0, "dropped": 0, "batches_failed": 0, "questions_processed": 0}
    bound = {str(o.get('table') or '').casefold() for o in ir.get('objects') or []}
    tables = {t.casefold(): t for t in schema(con) if t.casefold() in bound}
    entity_of = {o['table'].casefold(): (o.get('catalog') or {}).get('entity_col')
                 for o in ir.get('objects') or [] if o.get('table')}
    brief, seen, pending = schema_brief(con, ir, docs), set(), []
    for i in range(0, len(qs), BATCH):
        if deadline is not None and time.monotonic() >= deadline:
            break
        batch = qs[i:i + BATCH]
        try:
            d = extract(ask(PROMPT.format(schema=brief[:24000], questions='\n'.join(batch))) or '')
            if not isinstance(d, dict):
                raise ValueError("model_response_not_object")
        except Exception:
            result['batches_failed'] += 1
            continue
        result['questions_processed'] += len(batch)
        for kind, verifier in [('concepts', verify_concept), ('themes', verify_theme), ('caveats', verify_caveat)]:
            items = d.get(kind) if isinstance(d.get(kind), list) else []
            for c in items[:100]:
                if deadline is not None and time.monotonic() >= deadline:
                    break
                result['proposed'] += 1
                if not isinstance(c, dict):
                    _reject(result['rejected'], {}, 'proposal_not_object')
                    continue
                args = ((tables, entity_of.get(str(c.get('table') or '').casefold())) if kind == 'concepts'
                        else (tables, entity_of) if kind == 'themes' else (tables,))
                v = verifier(con, c, *args, rejected=result['rejected'], deadline=deadline)
                # Preserve SQL literal case; code='A' and code='a' may differ.
                sig = (kind, v.get('table'), v.get('column'), v.get('filter'), v.get('name_col'),
                       tuple(x['code'] for x in v.get('codes', [])), v.get('name'), v.get('statement')) if v else None
                if v and sig not in seen:
                    seen.add(sig)
                    result[kind].append(v)
                elif v:
                    _reject(result['rejected'], c, 'duplicate')
        pending += [c for c in d.get('derived', [])[:100] if isinstance(c, dict)] if isinstance(d.get('derived'), list) else []
    inputs = _concept_index(result['concepts'])
    for c in pending:
        result['proposed'] += 1
        uses = c.get('uses') if isinstance(c.get('uses'), list) else []
        if c.get('name') and c.get('formula') and uses and all(isinstance(u, str) and len(inputs.get(u.casefold(), [])) == 1 for u in uses):
            sig = ('derived', str(c['name']).casefold())
            if sig not in seen:
                seen.add(sig)
                result['derived'].append({**_base(c), 'formula': str(c['formula'])[:1000], 'uses': uses,
                                          'uses_ids': [proposal_id('concepts', inputs[u.casefold()][0]) for u in uses],
                                          'evidence': {'inputs_bound': True, 'formula_verified': False}})
                continue
        _reject(result['rejected'], c, 'derived_inputs_missing_or_ambiguous')
    result['accepted'] = sum(len(result[k]) for k in ('concepts', 'themes', 'derived', 'caveats'))
    result['dropped'] = result['proposed'] - result['accepted']
    result['budget_exhausted'] = deadline is not None and time.monotonic() >= deadline
    return result


def unavailable(c):
    return c.get('binding_status') in ('stale', 'unresolved') or c.get('status') == 'rejected'


def _concept_index(concepts):
    out = {}
    for c in concepts:
        if c.get('name') and not unavailable(c):
            out.setdefault(c['name'].casefold(), []).append(c)
    return out


def inputs_bound(d, concepts):
    uses = d.get('uses') or []
    if not uses:
        return False
    ids = d.get('uses_ids')
    if ids is not None:
        available = {proposal_id('concepts', c) for c in concepts if not unavailable(c)}
        return len(ids) == len(uses) and all(i in available for i in ids)
    index = _concept_index(concepts)
    return all(isinstance(u, str) and len(index.get(u.casefold(), [])) == 1 for u in uses)


def matches(question, c):
    q = (question or '').casefold()
    words = set(re.findall(r'[a-z]{3,}', q))
    aliases = [c.get('name') or '', *(c.get('aliases') or [])]
    exact = sum(3 for a in aliases if isinstance(a, str) and a and a.casefold() in q)
    toks = set(re.findall(r'[a-z]{3,}', (' '.join(str(a) for a in aliases) + ' '
                                     + str(c.get('definition') or '')).casefold()))
    return exact + len(words & toks)


def scoped(ir, tables):
    """Keep semantic hints inside executable/explicitly selected tables."""
    allowed = {str(t).casefold() for t in tables}
    out = {key: [c for c in ir.get(key) or [] if str(c.get('table') or '').casefold() in allowed
                 and c.get('status') != 'rejected']
           for key in ('concepts', 'themes')}
    out['derived'] = [c for c in ir.get('derived') or []
                      if inputs_bound(c, ir.get('concepts') or []) and inputs_bound(c, out['concepts'])]
    all_tables = {str(o.get('table')).casefold() for o in ir.get('objects') or [] if o.get('table')}
    out['caveats'] = [c for c in ir.get('caveats') or []
                      if {str(t).casefold() for t in (c.get('evidence') or {}).get('tables', all_tables)} <= allowed]
    return out


def context(question, ir, k=8):
    def score(c):
        return matches(question, c)
    lines, count = [], 0
    for key, title in [('concepts', '业务概念'), ('themes', '分析主题'), ('derived', '派生公式建议')]:
        picked = [c for c in sorted(ir.get(key) or [], key=score, reverse=True)
                  if score(c) > 0 and not unavailable(c)
                  and (key != 'derived' or inputs_bound(c, ir.get('concepts') or []))][:k]
        if not picked:
            continue
        lines.append(f'[{title}：仅取数依据经数据检查，语义状态见各项]')
        for c in picked:
            if key == 'concepts':
                desc = f"{c.get('table')}.{c.get('column')}" + (f" WHERE {c['filter']}" if c.get('filter') else '')
                desc += f";汇总建议:{c.get('aggregation') or ''}"
            elif key == 'themes':
                desc = f"{c.get('table')}.{c.get('name_col')}: " + ', '.join(x['code'] for x in c.get('codes', []))
            else:
                desc = str(c.get('formula') or '') + ';公式尚未执行核验'
            state = '业务含义人工确认' if c.get('status') in ('asserted', 'certified') else '业务含义待确认'
            lines.append(f"- {c.get('name')}({state}): {c.get('definition') or ''} → {desc}")
            count += 1
    cavs = [c for c in ir.get('caveats') or [] if not unavailable(c)][:5]
    if cavs:
        lines.append('[数据使用建议：相关查询有数据，陈述本身待确认]')
        lines.extend('- ' + str(c.get('statement') or '') for c in cavs)
        count += len(cavs)
    return '\n'.join(lines)[:16000], count


def merge_layers(base, new):
    out = {}
    for key in ('concepts', 'themes', 'derived', 'caveats'):
        positions, vals = {}, []
        for c in [*(base.get(key) or []), *(new.get(key) or [])]:
            sig = (c.get('name'), c.get('table'), c.get('column'), c.get('filter'), c.get('statement'),
                   c.get('name_col'), tuple(x['code'] for x in c.get('codes', [])), c.get('formula'), tuple(c.get('uses_ids') or c.get('uses') or []), c.get('check_sql'))
            if sig not in positions:
                positions[sig] = len(vals)
                vals.append(c)
            elif vals[positions[sig]].get('status') not in ('asserted', 'certified', 'rejected'):
                vals[positions[sig]] = c
        out[key] = vals
    return out


def refresh(con, ir, deadline=None):
    """Recheck machine bindings on continuation; retain failed entries for review."""
    tables = {t.casefold(): t for t in schema(con)}
    stale = 0
    for key, verifier in [('concepts', verify_concept), ('themes', verify_theme), ('caveats', verify_caveat)]:
        for c in ir.get(key) or []:
            if c.get('origin') != 'workload_proposal' or c.get('status') == 'rejected':
                continue
            if deadline is not None and time.monotonic() >= deadline:
                c['binding_status'] = 'unresolved'
                c.setdefault('evidence', {})['query_verified'] = None
                continue
            source = dict(c)
            if key == 'themes':
                source['codes'] = [x['code'] for x in c.get('codes', [])]
                args = (tables, {})
            else:
                args = (tables,)
            problems = []
            updated = verifier(con, source, *args, rejected=problems, deadline=deadline)
            if key == 'themes' and updated and source['codes'] != [x['code'] for x in updated['codes']]:
                updated = None  # Do not narrow a reviewed or persisted theme silently.
            if updated:
                if c.get('status') in ('asserted', 'certified'):
                    updated['status'], updated['semantic_status'] = c['status'], c.get('semantic_status')
                c.update(updated)
                c['binding_status'] = 'supported'
            else:
                unknown = any(p['reason'] in ('query_failed', 'caveat_query_failed', 'budget_exhausted') for p in problems)
                c['binding_status'] = 'unresolved' if unknown else 'stale'
                c.setdefault('evidence', {})['query_verified'] = None if unknown else False
                stale += int(not unknown)
    for d in ir.get('derived') or []:
        if d.get('origin') == 'workload_proposal':
            d['binding_status'] = 'supported' if inputs_bound(d, ir.get('concepts') or []) else 'stale'
    return stale
