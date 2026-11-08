#!/usr/bin/env python3
"""把 EvoOntology 工作区(五类记录)转换成 DataMind IR(协议 v2 的 Q-dm 消费对方本体)。

只做格式映射,不补充知识。读取 <evo_root>/<db>/active.json 指向的版本目录
(terms/mappings/relations/constraints/evidence.json),产出:
  <out_dir>/ir/<db>.json     DataMind IR(objects/relations/concepts/derived/caveats)
  <out_dir>/graphs.json      {db: {graph: "", source: "", seconds: 0, ir: "ir/<db>.json"}};graph 键由 install_ir.py 填
  <out_dir>/loss.json        无法表示的内容 {db: [{kind, count, examples}]}

映射规则(与 v2_conventions.md 一致):
- 任一 Mapping 引用的表 → 一个对象。name 用表名(关系端点按对象名引用,表名唯一且稳定),
  cn 用绑定到该表的表级 Term 名(无表级映射时取该表列级 Term 最多的那个 Term 名,再无则表名);
  attrs 列出库里的全部列(与 DataMind 自建对象一致;EvoOntology 智能体同样能经 describe_table 看到全部列),
  有列级 Mapping 的列附 Term 名作 cn、aggregation_semantics 作 doc;aliases = 绑定到该表的各 Term 名与别名。
- Relation 的 connection_condition 里能解析出两端 table.col 时 → 关系。父子方向按列是否为主键判定
  (主键一侧为父),都不是或都是时按出现顺序(前者为子)。status 记 verified(EvoOntology 只发布经核验的关系,
  这是一个假设,写进 build_manifest.assumptions);解析不出键的关系 → candidate。
- type 为 metric/concept/… 且 Mapping 带 semantic_filter 或 aggregation_semantics 的 Term → ir["concepts"];
  semantic_filter 不像 SQL 条件时不写进 filter(问数会直接拼成 WHERE),改并入 definition 并计入 loss。
- relation_type == derivation 的关系 → ir["derived"](公式字段无对应物,用目标 Term 定义文本)。
- Constraint → ir["caveats"] {statement, check_sql};check_sql 取其 Evidence 中以 SELECT 开头的 query,没有则丢弃并计入 loss。
- 指标契约(metric_layers)无对应物,不生成;Evidence 文本记录只作 provenance。

注意:主仓 cosmo-datamind(D8)只消费 objects/relations/aliases;concepts/caveats/derived 只在
cosmo-datamind-exp 分支(D10)被带入上下文。两者都生成,便于同一份转换供两种消费方式使用。

用法: python3 evo2dm.py <evo_root> <out_dir> [--db-root <dev_databases 目录>] [dbs...]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

EXP = Path(__file__).resolve().parent
sys.path.insert(0, str(EXP / "EvoOntology"))
from evoontology.ontology.models import Constraint, Evidence, Mapping, Relation, Term  # noqa: E402

DEFAULT_DBROOT = EXP / "EvoOntology" / "benchmarks" / "bird" / "data" / "mini_dev_data" / "dev_databases"
TABLE_COL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b")
SQLISH = re.compile(r"(=|<>|!=|<=|>=|<|>|\bIN\s*\(|\bLIKE\b|\bBETWEEN\b|\bIS\s+(NOT\s+)?NULL\b)", re.I)
CONCEPT_TYPES = {"metric", "concept", "measure", "kpi", "derived", "category"}


def _load_list(path: Path, cls):
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    if isinstance(raw, dict):
        raw = raw.get("items") or raw.get("records") or list(raw.values())
    out = []
    for item in raw:
        if isinstance(item, dict) and item.get("id"):
            try:
                out.append(cls.from_dict(item))
            except Exception:  # noqa: BLE001
                continue
    return out


def active_version_dir(ws: Path) -> Path | None:
    act = ws / "active.json"
    ver = None
    if act.exists():
        try:
            ver = json.loads(act.read_text()).get("active_version")
        except ValueError:
            ver = None
    vdir = ws / "versions"
    if ver and (vdir / ver).exists():
        return vdir / ver
    if (vdir / "ontology_v0").exists():
        return vdir / "ontology_v0"
    cands = sorted(p for p in vdir.glob("ontology_v*") if p.is_dir()) if vdir.exists() else []
    return cands[-1] if cands else None


def schema_of(db_path: Path):
    """{table_lower: (table, [(col, type, pk)])}"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    out = {}
    try:
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            cols = [(r[1], r[2] or "", int(r[5] or 0)) for r in con.execute(f'PRAGMA table_info("{t}")')]
            out[t.lower()] = (t, cols)
    finally:
        con.close()
    return out


class Loss:
    def __init__(self):
        self.items = {}

    def add(self, kind, example):
        it = self.items.setdefault(kind, {"kind": kind, "count": 0, "examples": []})
        it["count"] += 1
        if len(it["examples"]) < 5:
            it["examples"].append(str(example)[:200])

    def dump(self):
        return sorted(self.items.values(), key=lambda x: -x["count"])



NEGATION = re.compile(r"\b(independent|unrelated|not (?:a |the )?(?:same|related|join|link|reference)|no direct|do(?:es)? not (?:match|correspond|reference|join|link)|different (?:vocabular|namespace|id space|meaning)|cannot be joined|must not be joined)\b", re.I)
JUNCTION = re.compile(r"\b(many[- ]to[- ]many|junction|bridge|association table|link(?:s|ing)? (?:table )?|via|through)\b", re.I)
IDENT_COL = re.compile(r"(^id$|_?id$|_?code$|_?key$|_?no$|_?num$|_?api_id$|^cds?code$)", re.I)
CLAUSE_SPLIT = re.compile(r"[;,\n]|\band\b|\bor\b|\balso\b|\bwhile\b|\bplus\b", re.I)


def unique_columns(db_path: Path):
    """每张表的唯一列(主键 + UNIQUE 索引单列)。"""
    out = {}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            cols = {r[1].lower() for r in con.execute(f'PRAGMA table_info("{t}")') if r[5]}
            try:
                for idx in con.execute(f'PRAGMA index_list("{t}")'):
                    if idx[2]:
                        ic = [r[2] for r in con.execute(f'PRAGMA index_info("{idx[1]}")') if r[2]]
                        if len(ic) == 1:
                            cols.add(ic[0].lower())
            except sqlite3.Error:
                pass
            out[t.lower()] = cols
    finally:
        con.close()
    return out


def _is_key(t, c, pk_of, unique_of):
    return c.lower() in pk_of.get(t.lower(), set()) or c.lower() in unique_of.get(t.lower(), set())


def _relation_clauses(cond, schema, col_set, pk_of, unique_of):
    """connection_condition → [(ta, ca, tb, cb, verified, why)] 或 "negated"。

    v2 修正(诊断见 runs/v2/diagnosis_evo2dm_under_dm.md):
    - 否定/对照措辞(independent、different vocabularies…)→ 不建关系;
    - 按子句拆分,每个子句恰有两张不同表的 table.col 才成一对;多对(eye/hair/skin → colour)各成一条;
    - 多对多/中间表措辞且提到第三张表:只保留与中间表相连的键对,不把两端表直接连起来;
    - verified 只给「一侧是主键/唯一列、另一侧是标识样列或同名列」的键对;name=name、status=status 这类属性等值记 candidate。"""
    text = cond or ""
    if NEGATION.search(text):
        return "negated"
    all_pairs = [(t, c) for t, c in TABLE_COL.findall(text) if t.lower() in schema and c.lower() in col_set[t.lower()]]
    tables = []
    for t, _ in all_pairs:
        if t.lower() not in tables:
            tables.append(t.lower())
    junction = bool(JUNCTION.search(text)) or len(tables) >= 3
    out, seen = [], set()
    # 中间表只以裸表名出现("hero_power links superhero.id to superpower.id"):按列名把两端各接到中间表,不直接连两端
    if JUNCTION.search(text) and len(tables) == 2 and len(all_pairs) >= 2:
        mids = [t for t in schema if t not in tables and re.search(rf"(?<![A-Za-z0-9_]){re.escape(schema[t][0])}(?![A-Za-z0-9_])", text, re.I)]
        for mid in mids[:1]:
            mcols = list(col_set[mid].values())
            for (t, c) in all_pairs[:2]:
                core = re.sub(r"^(dim_|fact_|dws_|dwd_|ods_)", "", t.lower())
                best = None
                for jc in mcols:
                    stem = re.sub(r"_?(id|code|key|no)$", "", jc.lower())
                    if not stem or jc.lower() == c.lower() and not IDENT_COL.search(jc):
                        continue
                    if stem == core or stem in core or core in stem or (len(stem) >= 4 and core.endswith(stem)):
                        best = jc; break
                if best:
                    verified = _is_key(t, c, pk_of, unique_of) and bool(IDENT_COL.search(best))
                    out.append((schema[mid][0], best, t, c, verified, "junction expansion"))
        if out:
            return out
    for clause in CLAUSE_SPLIT.split(text):
        ps = [(t, c) for t, c in TABLE_COL.findall(clause) if t.lower() in schema and c.lower() in col_set[t.lower()]]
        if len(ps) != 2 or ps[0][0].lower() == ps[1][0].lower():
            continue
        (ta, ca), (tb, cb) = ps
        if junction and len(tables) >= 3:
            # 三张表:中间表 = 在键对里出现最多的表;只接受包含它的键对
            from collections import Counter
            cnt = Counter(t.lower() for t, _ in all_pairs)
            mid = max(cnt, key=lambda x: cnt[x])
            if mid not in (ta.lower(), tb.lower()):
                continue
        k = tuple(sorted([(ta.lower(), ca.lower()), (tb.lower(), cb.lower())]))
        if k in seen:
            continue
        seen.add(k)
        a_key, b_key = _is_key(ta, ca, pk_of, unique_of), _is_key(tb, cb, pk_of, unique_of)
        other = (tb, cb) if a_key else (ta, ca)
        ident_like = bool(IDENT_COL.search(other[1])) or (ca.lower() == cb.lower())
        fk_wording = bool(re.search(r"foreign key|references|reference to|fk\b", text, re.I))
        verified = (a_key or b_key) and (ident_like or fk_wording)
        why = ("key+identifier" if verified else
               "no key column" if not (a_key or b_key) else "non-identifier attribute equality")
        out.append((ta, ca, tb, cb, verified, why))
    if not out and len(all_pairs) >= 2 and all_pairs[0][0].lower() != all_pairs[1][0].lower() and not junction:
        (ta, ca), (tb, cb) = all_pairs[0], all_pairs[1]
        a_key, b_key = _is_key(ta, ca, pk_of, unique_of), _is_key(tb, cb, pk_of, unique_of)
        other = (tb, cb) if a_key else (ta, ca)
        verified = (a_key or b_key) and (bool(IDENT_COL.search(other[1])) or ca.lower() == cb.lower())
        out.append((ta, ca, tb, cb, verified, "fallback first two columns"))
    return out


def convert_db(db: str, ws: Path, db_path: Path, loss: Loss, fill_schema: bool = True):
    vdir = active_version_dir(ws)
    if not vdir:
        raise FileNotFoundError(f"{ws}: 没有可用版本目录")
    terms = _load_list(vdir / "terms.json", Term)
    mappings = _load_list(vdir / "mappings.json", Mapping)
    relations = _load_list(vdir / "relations.json", Relation)
    constraints = _load_list(vdir / "constraints.json", Constraint)
    evidence = {e.id: e for e in _load_list(vdir / "evidence.json", Evidence)}
    schema = schema_of(db_path)
    term_by_id = {t.id: t for t in terms}

    # ── 表 → 对象 ──
    table_terms = {}      # table_lower → {"table_level": [Term], "cols": {col_lower: [Mapping]}, "terms": set(term ids)}
    term_table = {}       # term id → table_lower(该 Term 的主要绑定表)
    for m in mappings:
        tl = (m.table or "").strip().lower()
        if not tl:
            loss.add("mapping_without_table", m.id); continue
        if tl not in schema:
            loss.add("mapping_table_not_in_db", f"{m.id}: {m.table}"); continue
        ent = table_terms.setdefault(tl, {"table_level": [], "cols": {}, "terms": []})
        if m.term_id and m.term_id not in ent["terms"]:
            ent["terms"].append(m.term_id)
        term_table.setdefault(m.term_id, tl)
        col = (m.column or "").strip()
        if not col:
            if m.term_id in term_by_id:
                ent["table_level"].append(term_by_id[m.term_id])
            continue
        real_cols = {c.lower(): c for c, _t, _pk in schema[tl][1]}
        if col.lower() not in real_cols:
            loss.add("mapping_column_not_in_db", f"{m.id}: {m.table}.{col}"); continue
        ent["cols"].setdefault(col.lower(), []).append(m)

    objects = []
    obj_name_of_table = {}
    for tl, ent in table_terms.items():
        table, cols = schema[tl]
        if ent["table_level"]:
            head = ent["table_level"][0]
        else:
            # 列级 Term 里挑绑定列最多的 Term;没有就用表名
            cnt = {}
            for ms in ent["cols"].values():
                for m in ms:
                    cnt[m.term_id] = cnt.get(m.term_id, 0) + 1
            best = max(cnt, key=cnt.get) if cnt else None
            head = term_by_id.get(best) if best else None
        cn = (head.name if head else table) or table
        aliases, seen = [], {cn.lower(), table.lower()}
        for tid in ent["terms"]:
            t = term_by_id.get(tid)
            if not t: continue
            for a in [t.name, *t.aliases]:
                a = str(a or "").strip()
                if a and a.lower() not in seen:
                    seen.add(a.lower()); aliases.append(a)
        attrs = []
        for col, ctype, _pk in cols:
            ms = ent["cols"].get(col.lower()) or []
            t = term_by_id.get(ms[0].term_id) if ms else None
            a = {"col": col, "cn": (t.name if t and t.name.lower() != col.lower() else ""), "type": ctype}
            doc = "; ".join(x for x in [(ms[0].aggregation_semantics if ms else ""), (ms[0].validation if ms else "")] if x)
            if doc:
                a["doc"] = doc[:200]
            attrs.append(a)
        obj_name_of_table[tl] = table
        objects.append({"name": table, "cn": cn, "kind": "object", "table": table, "attrs": attrs,
                        "aliases": aliases[:12], "candidate": False, "isPrimitive": False,
                        "definition": (head.definition if head else "") or "",
                        "evidence": {"sources": [f"evoontology:{tid}" for tid in ent["terms"][:8]]},
                        "provenance": {"directSource": "evoontology", "terms": ent["terms"][:20]}})

    pk_of = {tl: {c.lower() for c, _t, pk in cols if pk} for tl, (_tn, cols) in schema.items()}
    unique_of = unique_columns(db_path)
    col_set = {tl: {c.lower(): c for c, _t, _pk in cols} for tl, (_tn, cols) in schema.items()}

    # ── 关系 ──
    rels, seen_rel = [], set()
    derived = []
    for r in relations:
        if r.relation_type == "derivation":
            tgt, src = term_by_id.get(r.target), term_by_id.get(r.source)
            if not tgt or not src:
                loss.add("derivation_endpoint_missing", r.id); continue
            tl = term_table.get(src.id)
            inp = []
            for m in mappings:
                if m.term_id == src.id and m.table:
                    inp.append({"name": src.name, "table": m.table, "column": m.column or "",
                                "filter": m.semantic_filter if SQLISH.search(m.semantic_filter or "") else ""})
                    break
            derived.append({"name": tgt.name, "definition": tgt.definition, "aliases": list(tgt.aliases)[:8],
                            "formula": (r.description or tgt.definition or tgt.name)[:300], "uses": [src.name],
                            "inputs": inp, "evidence": {"source": "evoontology", "relation": r.id}})
            continue
        clauses = _relation_clauses(r.connection_condition or "", schema, col_set, pk_of, unique_of)
        if clauses == "negated":
            loss.add("relation_negated_condition_dropped", f"{r.id}: {r.connection_condition[:80]}"); continue
        if clauses:
            n_added = 0
            for (ta, ca, tb, cb, verified, why) in clauses:
                a_pk, b_pk = _is_key(ta, ca, pk_of, unique_of), _is_key(tb, cb, pk_of, unique_of)
                if b_pk and not a_pk:
                    child, ck, parent, pk = ta, ca, tb, cb
                elif a_pk and not b_pk:
                    child, ck, parent, pk = tb, cb, ta, ca
                else:
                    child, ck, parent, pk = ta, ca, tb, cb
                for tl in (child.lower(), parent.lower()):
                    if tl not in obj_name_of_table:          # 关系提到的表没有任何 Mapping:补一个只带列的对象
                        table, cols = schema[tl]
                        obj_name_of_table[tl] = table
                        objects.append({"name": table, "cn": table, "kind": "object", "table": table,
                                        "attrs": [{"col": c, "cn": "", "type": t} for c, t, _ in cols],
                                        "aliases": [], "candidate": False, "isPrimitive": False, "definition": "",
                                        "evidence": {"sources": [f"evoontology:{r.id}"]},
                                        "provenance": {"directSource": "evoontology", "note": "table only named in a relation"}})
                key = (child.lower(), parent.lower(), ck.lower(), pk.lower())
                if key in seen_rel:
                    loss.add("duplicate_relation_keys", r.id); continue
                seen_rel.add(key)
                n_added += 1
                if not verified:
                    loss.add("relation_pair_not_identifier_kept_candidate", f"{r.id}: {ta}.{ca}={tb}.{cb} ({why})")
                rels.append({"source_concept": obj_name_of_table[child.lower()], "target_concept": obj_name_of_table[parent.lower()],
                             "verb": r.relation_type or "关联", "status": "verified" if verified else "candidate",
                             "evidence_status": "evoontology" if verified else "evoontology_nonkey",
                             "note": (r.description or "")[:300],
                             "evidence": {"child_key": col_set[child.lower()][ck.lower()], "parent_key": col_set[parent.lower()][pk.lower()],
                                          "source": "evoontology", "note": (r.description or "")[:300], "pair_reason": why,
                                          "evidence_ids": list(r.evidence_refs)[:6], "relation_id": r.id,
                                          "connection_condition": r.connection_condition[:300]}})
            if n_added:
                continue
        # 解析不出键:两端 Term 各自有绑定表时记 candidate,否则丢弃
        ts, tt = term_table.get(r.source), term_table.get(r.target)
        if ts and tt and ts == tt:
            loss.add("relation_within_one_table_dropped", f"{r.id}: {r.connection_condition[:80]}"); continue
        if ts and tt and ts in obj_name_of_table and tt in obj_name_of_table:
            key = (ts, tt, "", r.id)
            rels.append({"source_concept": obj_name_of_table[ts], "target_concept": obj_name_of_table[tt],
                         "verb": r.relation_type or "关联", "status": "candidate", "evidence_status": "unparsed",
                         "note": (r.connection_condition or r.description or "")[:300],
                         "evidence": {"source": "evoontology", "relation_id": r.id,
                                      "connection_condition": r.connection_condition[:300]}})
            loss.add("relation_keys_unparsed_kept_candidate", f"{r.id}: {r.connection_condition[:80]}")
        else:
            loss.add("relation_dropped_no_table_endpoints", f"{r.id}: {r.connection_condition[:80]}")

    # ── 概念 ──
    concepts, seen_c = [], set()
    for m in mappings:
        t = term_by_id.get(m.term_id)
        if not t or t.type.lower() not in CONCEPT_TYPES:
            continue
        if not (m.semantic_filter or m.aggregation_semantics) or not m.table or m.table.lower() not in schema:
            continue
        if t.name.lower() in seen_c:
            continue
        seen_c.add(t.name.lower())
        filt = (m.semantic_filter or "").strip()
        definition = t.definition or t.name
        if filt and not SQLISH.search(filt):
            definition = f"{definition} (filter: {filt})"
            loss.add("concept_filter_prose_moved_to_definition", f"{t.id}: {filt[:80]}")
            filt = ""
        concepts.append({"name": t.name, "definition": definition, "aliases": list(t.aliases)[:8],
                         "table": schema[m.table.lower()][0], "column": m.column or "",
                         "filter": filt, "aggregation": (m.aggregation_semantics or "")[:200],
                         "evidence": {"source": "evoontology", "mapping": m.id, "validation": (m.validation or "")[:200]}})

    # ── 使用提醒 ──
    caveats = []
    for c in constraints:
        sql = ""
        for eid in c.evidence_refs:
            q = (evidence.get(eid).query if evidence.get(eid) else "") or ""
            if q.strip().lower().startswith("select"):
                sql = q.strip(); break
        statement = (c.description or "").strip()
        if c.scope:
            statement = f"{statement} (scope: {c.scope})"
        if not statement:
            loss.add("constraint_without_description", c.id); continue
        if not sql:
            loss.add("constraint_without_select_evidence", f"{c.id}: {statement[:80]}"); continue
        caveats.append({"statement": statement[:400], "check_sql": sql[:600],
                        "evidence": {"source": "evoontology", "constraint": c.id, "severity": c.severity,
                                     "type": c.constraint_type, "keywords": list(c.trigger_keywords)[:12]}})

    if fill_schema:                                   # 本体没提到的表:补成只带列的对象,让 Q-dm 与 ReAct 的 get_database_info 一样看得到全部表
        for tl, (table, cols) in schema.items():
            if tl in obj_name_of_table:
                continue
            obj_name_of_table[tl] = table
            objects.append({"name": table, "cn": table, "kind": "object", "table": table,
                            "attrs": [{"col": c, "cn": "", "type": t} for c, t, _ in cols],
                            "aliases": [], "candidate": False, "isPrimitive": False, "definition": "",
                            "evidence": {"sources": ["schema_fill"]},
                            "provenance": {"directSource": "schema_fill", "note": "table absent from the EvoOntology workspace"}})
            loss.add("table_filled_from_schema(no ontology knowledge)", table)
    for t in terms:                                   # 没有任何绑定表的 Term:无对应物
        if t.id not in term_table:
            loss.add("term_without_mapping", f"{t.id} ({t.type})")

    ir = {"scenario": {"name": f"EVO-{db}", "style": "evoontology-converted", "object_count": len(objects),
                       "relation_count": len(rels), "evidence": {"terms": len(terms), "mappings": len(mappings),
                                                                 "relations": len(relations), "constraints": len(constraints),
                                                                 "evidence": len(evidence)}},
          "objects": objects, "relations": rels, "concepts": concepts, "derived": derived, "caveats": caveats,
          "metric_layers": {"atomic": [], "derived": [], "composite": []},
          "build_manifest": {"version": 1, "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                             "request": "evo2dm: EvoOntology workspace converted to DataMind IR",
                             "source": {"id": "", "name": f"BIRD-{db}"}, "skills": [], "cqs": [], "method": "evo2dm",
                             "origin": {"evo_root": str(ws.parent), "db": db, "version": vdir.name},
                             "assumptions": ["parsable EvoOntology relations are recorded as status=verified",
                                             "tables absent from the workspace are added as column-only objects (provenance schema_fill)" if fill_schema else "tables absent from the workspace are omitted",
                                             "object attrs list every column of the table; mapped columns carry the Term name",
                                             "metric_layers not representable from EvoOntology records"]}}
    return ir


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("evo_root"); ap.add_argument("out_dir")
    ap.add_argument("--db-root", default=str(DEFAULT_DBROOT))
    ap.add_argument("--no-fill-schema", action="store_true", help="不为本体未提到的表补对象")
    ap.add_argument("dbs", nargs="*")
    a = ap.parse_args()
    root, out, dbroot = Path(a.evo_root), Path(a.out_dir), Path(a.db_root)
    (out / "ir").mkdir(parents=True, exist_ok=True)
    dbs = a.dbs or sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    graphs = json.loads((out / "graphs.json").read_text()) if (out / "graphs.json").exists() else {}
    losses = {}
    for db in dbs:
        ws = root / db
        db_path = dbroot / db / f"{db}.sqlite"
        if not db_path.exists():
            print(db, "skip: 库文件不存在", db_path); continue
        if not active_version_dir(ws):
            print(db, "skip: 没有已发布版本"); continue
        loss = Loss()
        ir = convert_db(db, ws, db_path, loss, fill_schema=not a.no_fill_schema)
        (out / "ir" / f"{db}.json").write_text(json.dumps(ir, ensure_ascii=False, indent=1))
        prev = graphs.get(db) or {}
        graphs[db] = {"graph": prev.get("graph", ""), "source": "", "seconds": 0, "ir": f"ir/{db}.json"}
        losses[db] = loss.dump()
        print(db, "objects", len(ir["objects"]), "relations", len(ir["relations"]),
              "verified", sum(r["status"] == "verified" for r in ir["relations"]),
              "concepts", len(ir["concepts"]), "derived", len(ir["derived"]), "caveats", len(ir["caveats"]),
              "loss", sum(x["count"] for x in losses[db]))
    (out / "graphs.json").write_text(json.dumps(graphs, ensure_ascii=False, indent=1))
    (out / "loss.json").write_text(json.dumps(losses, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
