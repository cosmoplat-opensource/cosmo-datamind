# -*- coding: utf-8 -*-
"""SELECT 语句的结构解析(DR-133):供本体约束校验使用。

此前校验用正则在整条语句上找表名、别名和连接条件,分不清作用域:子查询别名与真实表同名、
同一个别名在不同子查询里指向不同的表、FROM 后直接跟括号、JOIN … USING 都会判断错。
本模块把语句切成词元,按括号和子句关键字还原出结构:

- 每个 SELECT 是一个查询块(UNION 的各支、子查询、CTE 各是一块),记下它的 FROM 项及别名;
- 名字按作用域解析:先看本块,再看外层块(关联子查询);FROM 里的名字先看可见的 CTE,再看真实的表;
- 连接条件取 ON 和 WHERE 里「别名.列 = 别名.列」的等式,以及 USING / NATURAL JOIN;
- 聚合调用取 SUM / AVG / COUNT / TOTAL 及其括号里的内容。

只解析结构,不求值,不连接数据库。不认识的写法抛 ParseError,调用方退回原来的做法。只用标准库。
"""
from __future__ import annotations

import re

__all__ = ["ParseError", "analyze"]


class ParseError(ValueError):
    pass


_TOKEN = re.compile(r"""
  (?P<ws>\s+|--[^\n]*|/\*.*?\*/)
 |(?P<str>'(?:[^']|'')*')
 |(?P<qid>"(?:[^"]|"")*"|`[^`]*`|\[[^\]]*\])
 |(?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+)
 |(?P<id>[A-Za-z_一-鿿][\w$一-鿿]*)
 |(?P<op><>|!=|>=|<=|==|\|\||->>|->|<<|>>|[-+*/%(),.;=<>~&|?:@])
""", re.S | re.X)

_SET_OPS = {"union", "intersect", "except"}
_CLAUSES = ("from", "where", "group", "having", "window", "order", "limit")
_JOIN_WORDS = {"join", "left", "right", "full", "inner", "outer", "cross", "natural"}
_NOT_ALIAS = _JOIN_WORDS | set(_CLAUSES) | _SET_OPS | {"on", "using", "indexed", "not", "as", "select", "with", "values"}
_AGGREGATES = {"sum", "avg", "count", "total"}
_QUERY_START = {"select", "with", "values"}


def _tokens(sql):
    out, pos = [], 0
    text = sql or ""
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise ParseError(f"无法识别的字符:{text[pos:pos + 10]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        raw = m.group(0)
        if kind == "qid":                        # 带引号的标识符:去掉引号,不当关键字
            out.append(("id", raw[1:-1].replace('""', '"') if raw[0] == '"' else raw[1:-1], True))
        elif kind == "id":
            out.append(("id", raw, False))
        else:
            out.append((kind or "op", raw, False))
    while out and out[-1][:2] == ("op", ";"):
        out.pop()
    if any(t[:2] == ("op", ";") for t in out):
        raise ParseError("多条语句")
    return out


class _Parser:
    def __init__(self, sql):
        self.t = _tokens(sql)
        self.match = {}
        stack = []
        for i, tok in enumerate(self.t):
            if tok[:2] == ("op", "("):
                stack.append(i)
            elif tok[:2] == ("op", ")"):
                if not stack:
                    raise ParseError("括号不配对")
                self.match[stack.pop()] = i
        if stack:
            raise ParseError("括号不配对")
        self.cores = []

    # ── 词元辅助 ──
    def kw(self, i, hi=None):
        """位置 i 上的关键字(小写);带引号的标识符、非标识符返回空串。"""
        if i >= (len(self.t) if hi is None else hi):
            return ""
        kind, text, quoted = self.t[i]
        return text.lower() if kind == "id" and not quoted else ""

    def op(self, i, hi=None):
        if i >= (len(self.t) if hi is None else hi):
            return ""
        return self.t[i][1] if self.t[i][0] == "op" else ""

    def is_id(self, i, hi):
        return i < hi and self.t[i][0] == "id"

    def top_level(self, lo, hi):
        """[lo, hi) 里不在括号内的词元位置。"""
        i = lo
        while i < hi:
            yield i
            i = self.match[i] + 1 if self.op(i) == "(" else i + 1

    def text(self, lo, hi, skip=()):
        """[lo, hi) 的词元拼回文本(「别名.列」中间不留空格);skip 里的区间(子查询)不输出。"""
        out, i = "", lo
        while i < hi:
            hole = next((r for r in skip if r[0] == i), None)
            if hole:
                i = hole[1]
                continue
            raw = self.t[i][1]
            out += raw if (raw == "." or out.endswith(".") or not out) else " " + raw
            i += 1
        return out

    # ── 语句 ──
    def query(self, lo, hi, ctes, parent):
        while self.op(lo) == "(" and self.match[lo] == hi - 1:
            lo, hi = lo + 1, hi - 1
        if lo >= hi:
            raise ParseError("空的查询")
        ctes = set(ctes)
        if self.kw(lo) == "with":
            i = lo + 1
            if self.kw(i) == "recursive":
                i += 1
            while True:
                if not self.is_id(i, hi):
                    raise ParseError("WITH 后缺少名称")
                name = self.t[i][1].lower()
                i += 1
                if self.op(i) == "(":
                    i = self.match[i] + 1
                if self.kw(i) != "as":
                    raise ParseError("WITH 缺少 AS")
                i += 1
                while self.kw(i) in ("not", "materialized"):
                    i += 1
                if self.op(i) != "(":
                    raise ParseError("WITH 缺少子查询")
                ctes.add(name)                           # 递归 CTE 可以引用自己
                self.query(i + 1, self.match[i], ctes, parent)
                i = self.match[i] + 1
                if self.op(i) == ",":
                    i += 1
                    continue
                break
            lo = i
        start = lo
        for i in self.top_level(lo, hi):
            if self.kw(i) in _SET_OPS:
                self.branch(start, i, ctes, parent)
                start = i + 2 if self.kw(i + 1) in ("all", "distinct") else i + 1
        self.branch(start, hi, ctes, parent)

    def branch(self, lo, hi, ctes, parent):
        if self.op(lo) == "(" and self.kw(lo + 1) in _QUERY_START:
            end = self.match[lo]
            self.query(lo + 1, end, ctes, parent)
            tail = end + 1                               # 括号后面只能是 ORDER BY / LIMIT
            if tail < hi and self.kw(tail) not in ("order", "limit"):
                raise ParseError("括号后的内容无法解析")
            return
        first = self.kw(lo)
        if first == "values":
            return
        if first != "select":
            raise ParseError("不是 SELECT 语句")
        core = {"id": len(self.cores), "parent": parent, "items": [], "eqs": [], "usings": [], "aggs": []}
        self.cores.append(core)
        cuts = {}
        for i in self.top_level(lo + 1, hi):
            word = self.kw(i)
            if word in _CLAUSES and word not in cuts and all(c not in cuts for c in _CLAUSES[_CLAUSES.index(word) + 1:]):
                cuts[word] = i
        order = sorted(cuts.values()) + [hi]
        self.expression(lo + 1, order[0], core, ctes, on=False)
        if "from" in cuts:
            end = order[order.index(cuts["from"]) + 1]
            self.from_clause(cuts["from"] + 1, end, core, ctes)
            if end < hi:
                self.expression(end, hi, core, ctes, on=False)

    def from_clause(self, lo, hi, core, ctes):
        if lo >= hi:
            raise ParseError("FROM 后缺少表名")
        i, natural, first = lo, False, True
        while i < hi:
            before = [it["alias"] for it in core["items"]]
            if self.op(i) == "(":
                end = self.match[i]
                if self.kw(i + 1) in _QUERY_START:
                    self.query(i + 1, end, ctes, core["parent"])
                    item = {"kind": "derived", "table": None}
                else:                                    # 带括号的一组连接:FROM (a JOIN b ON …)
                    self.from_clause(i + 1, end, core, ctes)
                    item = None
                i = end + 1
            else:
                if not self.is_id(i, hi):
                    raise ParseError("FROM 后缺少表名")
                schema, name = "", self.t[i][1].lower()
                i += 1
                if self.op(i, hi) == "." and self.is_id(i + 1, hi):
                    schema, name = name, self.t[i + 1][1].lower()
                    i += 2
                if self.op(i, hi) == "(":                # 表值函数:json_each(...)
                    i = self.match[i] + 1
                    item = {"kind": "function", "table": None, "name": name}
                elif schema == "up":
                    item = {"kind": "table", "table": "up." + name, "name": name}
                elif not schema and name in ctes:
                    item = {"kind": "cte", "table": None, "name": name}
                else:
                    item = {"kind": "table", "table": name, "name": name}
            if item is not None:
                if self.kw(i, hi) == "as":
                    i += 1
                    if not self.is_id(i, hi):
                        raise ParseError("AS 后缺少别名")
                    item["alias"] = self.t[i][1].lower()
                    i += 1
                elif self.is_id(i, hi) and (self.t[i][2] or self.kw(i) not in _NOT_ALIAS):
                    item["alias"] = self.t[i][1].lower()
                    i += 1
                else:
                    item["alias"] = item.get("name") or f"_derived{len(core['items'])}"
                core["items"].append(item)
                if self.kw(i, hi) == "indexed" and self.kw(i + 1, hi) == "by":
                    i += 3
                elif self.kw(i, hi) == "not" and self.kw(i + 1, hi) == "indexed":
                    i += 2
                if natural and not first:
                    core["usings"].append({"alias": item["alias"], "cols": None, "earlier": before})
            if self.kw(i, hi) == "on":
                end = next((j for j in self.top_level(i + 1, hi) if self.kw(j) in _JOIN_WORDS or self.op(j) == ","), hi)
                self.expression(i + 1, end, core, ctes, on=True)
                i = end
            elif self.kw(i, hi) == "using":
                if self.op(i + 1, hi) != "(":
                    raise ParseError("USING 缺少列清单")
                end = self.match[i + 1]
                cols = [self.t[j][1].lower() for j in range(i + 2, end) if self.t[j][0] == "id"]
                if item is not None:
                    core["usings"].append({"alias": item["alias"], "cols": cols, "earlier": before})
                i = end + 1
            if i >= hi:
                break
            first, natural = False, False
            if self.op(i) == ",":
                i += 1
                continue
            if self.kw(i) not in _JOIN_WORDS:
                raise ParseError(f"FROM 子句里无法解析:{self.t[i][1]}")
            while self.kw(i, hi) in _JOIN_WORDS:
                natural = natural or self.kw(i) == "natural"
                i += 1

    def expression(self, lo, hi, core, ctes, on):
        """扫描一段表达式:子查询各自成块;记下等值条件和聚合调用。"""
        holes, i = [], lo
        while i < hi:
            if self.op(i) == "(" and self.kw(i + 1) in _QUERY_START:
                self.query(i + 1, self.match[i], ctes, core["id"])
                holes.append((i, self.match[i] + 1))
                i = self.match[i] + 1
                continue
            i += 1
        inside = lambda k: any(a <= k < b for a, b in holes)      # noqa: E731
        i = lo
        while i < hi:
            if inside(i):
                i += 1
                continue
            if (self.is_id(i, hi) and self.op(i + 1, hi) == "." and self.is_id(i + 2, hi) and self.op(i + 3, hi) in ("=", "==")
                    and self.is_id(i + 4, hi) and self.op(i + 5, hi) == "." and self.is_id(i + 6, hi)
                    and self.op(i + 7, hi) not in (".", "(") and self.op(i - 1) != "."):
                core["eqs"].append({"a": self.t[i][1].lower(), "ca": self.t[i + 2][1].lower(),
                                    "b": self.t[i + 4][1].lower(), "cb": self.t[i + 6][1].lower(), "on": on})
                i += 7
                continue
            if self.kw(i, hi) in _AGGREGATES and self.op(i + 1, hi) == "(":
                end = self.match[i + 1]
                core["aggs"].append((self.kw(i), self.text(i + 2, end, [h for h in holes if i + 2 <= h[0] < end])))
            i += 1


def _resolve(cores, core, alias):
    """别名按作用域解析:先本块,再外层块。返回 (FROM 项, 是否在本块);找不到返回 (None, False)。"""
    cur, own = core, True
    while cur is not None:
        for item in cur["items"]:
            if item["alias"] == alias:
                return item, own
        cur = cores[cur["parent"]] if cur["parent"] is not None else None
        own = False
    return None, False


def analyze(sql):
    """解析一条 SELECT 语句 → {"tables": [真实表], "cores": [{id, tables: [(别名, 表)]}],
    "pairs": [{a, ca, b, cb, ta, tb, core, on, joined}], "usings": [{core, alias, table, cols, earlier: [(别名, 表)]}],
    "aggs": [(函数, 括号里的内容, 查询块)]}。pairs 只含两侧都解析到真实表的等式;joined 表示两个别名都在本块的 FROM 里。"""
    p = _Parser(sql)
    p.query(0, len(p.t), set(), None)
    if not p.cores:
        raise ParseError("没有 SELECT")
    out: dict = {"tables": [], "cores": [], "pairs": [], "usings": [], "aggs": []}
    for core in p.cores:
        real = [(it["alias"], it["table"]) for it in core["items"] if it["kind"] == "table"]
        out["tables"] += [t for _a, t in real]
        out["cores"].append({"id": core["id"], "tables": real})
        for eq in core["eqs"]:
            ia, own_a = _resolve(p.cores, core, eq["a"])
            ib, own_b = _resolve(p.cores, core, eq["b"])
            if ia is None or ib is None or ia["kind"] != "table" or ib["kind"] != "table":
                continue
            out["pairs"].append({**eq, "ta": ia["table"], "tb": ib["table"], "core": core["id"], "joined": own_a and own_b})
        by_alias = {it["alias"]: it for it in core["items"]}
        for u in core["usings"]:
            it = by_alias.get(u["alias"])
            if it is None or it["kind"] != "table":
                continue
            out["usings"].append({"core": core["id"], "alias": u["alias"], "table": it["table"], "cols": u["cols"],
                                  "earlier": [(a, by_alias[a]["table"]) for a in u["earlier"]
                                              if a in by_alias and by_alias[a]["kind"] == "table"]})
        out["aggs"] += [(fn, args, core["id"]) for fn, args in core["aggs"]]
    return out
