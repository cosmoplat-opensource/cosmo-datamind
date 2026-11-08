"""本地对比实验补充:论文中的两个基线,开源仓库未提供实现,按论文描述实现。

- SL(ReAct + Semantic Layer):把构建出的语义层(五类记录全部内容)作为静态文本放进系统提示,不提供语义工具;
- Memory(ReAct + Memory):把构建集上的历史轨迹(问题、最终 SQL、执行是否正确)存为经验条目,
  按与当前问题的词重叠检索同库最相近的 k 条放进系统提示。

由环境变量 EVO_CONTEXT_MODE=sl|memory 打开;SL 读 EVO_SEMANTIC_ROOT/<db_id>,Memory 读 EVO_MEMORY_FILE(jsonl)。

本文方法(VLDB 稿):EVO_CONTEXT_MODE=dmc,按问题从 DataMind 实例(EVO_DM_URL,图谱映射 EVO_DM_GRAPHS)取编译好的
本体上下文(相关表与字段、已验证连接键、指标、概念与注意事项、取值出处),放进系统提示;语义工具照常可用。
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from pathlib import Path

_TOK = re.compile(r"[A-Za-z0-9_]+|[一-鿿]")
_MEM: dict | None = None


def _render_sl(db_id: str) -> str:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from evoontology import SemanticStore
    root = Path(os.environ["EVO_SEMANTIC_ROOT"]) / db_id
    if not root.exists():
        return ""
    _, recs = SemanticStore.load_records(root)
    parts = [f"Semantic layer for database {db_id} (static; no tools needed to read it):"]
    for kind in ("terms", "mappings", "relations", "constraints", "evidence"):
        items = recs.get(kind) if isinstance(recs, dict) else None
        if items:
            parts.append(f"## {kind}")
            parts += [json.dumps(x, ensure_ascii=False) for x in items]
    return "\n".join(parts)


def _tok(s: str) -> list[str]:
    return [t.lower() for t in _TOK.findall(s or "")]


def _render_memory(db_id: str, question: str, k: int = 3) -> str:
    global _MEM
    if _MEM is None:
        _MEM = {}
        for line in open(os.environ["EVO_MEMORY_FILE"], encoding="utf-8"):
            e = json.loads(line)
            _MEM.setdefault(e["db_id"], []).append(e)
    eps = _MEM.get(db_id) or []
    if not eps:
        return ""
    df = Counter(t for e in eps for t in set(_tok(e["question"])))
    n = len(eps)
    q = Counter(_tok(question))

    def score(e):
        d = Counter(_tok(e["question"]))
        return sum(q[t] * d[t] * math.log(1 + n / df[t]) ** 2 for t in q if t in d)

    top = sorted(eps, key=score, reverse=True)[:k]
    lines = ["Past episodes on this database (question, the agent's final SQL, whether it executed to the correct answer):"]
    for e in top:
        lines.append(f"- Q: {e['question']}\n  SQL: {e['sql']}\n  correct: {e['correct']}")
    return "\n".join(lines)


_DM_GRAPHS: dict | None = None


def dm_graph(db_id: str) -> str:
    global _DM_GRAPHS
    if _DM_GRAPHS is None:
        _DM_GRAPHS = json.loads(Path(os.environ["EVO_DM_GRAPHS"]).read_text())
    return (_DM_GRAPHS.get(db_id) or {}).get("graph") or ""


def dm_post(path: str, body: dict, timeout: int = 120) -> dict:
    import urllib.request
    req = urllib.request.Request(os.environ["EVO_DM_URL"] + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _render_dmc(db_id: str, question: str) -> str:
    g = dm_graph(db_id)
    if not g:
        return ""
    try:
        r = dm_post("/api/ont/compile", {"q": question, "graphs": [g]})
    except Exception:  # noqa: BLE001  the agent still has its tools; an unavailable context is recorded as empty
        return ""
    ctx = r.get("context") or ""
    if not ctx:
        return ""
    return ("Ontology context compiled for this question (tables and columns most relevant to it, verified join keys, "
            "metric definitions, concepts and caveats, and which column holds each value mentioned in the question). "
            "Use the listed join keys for joins between these tables.\n" + ctx)


def _render_doc(db_id: str) -> str:
    """Information-matched baseline: the database documentation (BIRD database_description, as registered for
    DataMind in EVO_DOC_DIR/<db_id>.json) given to the agent as static text, without any ontology."""
    f = Path(os.environ["EVO_DOC_DIR"]) / f"{db_id}.json"
    if not f.exists():
        return ""
    tables = json.loads(f.read_text()).get("tables") or {}
    lines = [f"Database documentation for {db_id} (column descriptions and value meanings):"]
    for t, cols in tables.items():
        items = []
        for c, v in cols.items():
            d = (v.get("desc") or "").strip()
            m = (v.get("values") or "").strip()
            if d or m:
                items.append(f"{c}: {d}" + (f" [values: {m}]" if m else ""))
        if items:
            lines.append(f"table {t}: " + "; ".join(items))
    return "\n".join(lines)


def _render_joins(db_id: str) -> str:
    """Verified join keys of the DataMind ontology for this database (relations with status verified/asserted and
    data-adjudicated child/parent keys), read from the installed graph EVO_DM_WORKDIR/<graph>.json."""
    g = dm_graph(db_id)
    f = Path(os.environ["EVO_DM_WORKDIR"]) / f"{g}.json"
    if not g or not f.exists():
        return ""
    ir = json.loads(f.read_text())
    objs = {o.get("name"): o.get("table") for o in ir.get("objects", [])}
    lines = []
    for r in ir.get("relations", []):
        if r.get("status") not in ("verified", "asserted"):
            continue
        ev = r.get("evidence") or {}
        a, b = objs.get(r.get("source_concept") or r.get("source")), objs.get(r.get("target_concept") or r.get("target"))
        if a and b and ev.get("child_key") and ev.get("parent_key"):
            lines.append(f"{a}.{ev['child_key']} = {b}.{ev['parent_key']}")
    if not lines:
        return ""
    return "Verified join keys (from the ontology; checked against the data):\n" + "\n".join(dict.fromkeys(lines))


def extra_context(db_id: str, question: str) -> str:
    mode = os.environ.get("EVO_CONTEXT_MODE", "")
    if mode == "docjoin":
        return "\n\n".join(x for x in (_render_doc(db_id), _render_joins(db_id)) if x)
    if mode == "doc":
        return _render_doc(db_id)
    if mode == "dmc":
        return _render_dmc(db_id, question)
    if mode == "sl":
        return _render_sl(db_id)
    if mode == "memory":
        return _render_memory(db_id, question)
    return ""
