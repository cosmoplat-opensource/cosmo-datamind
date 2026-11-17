#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代码契约:从上传的代码与文档里读出「系统实际怎么用数据」(DR-065)。

两种场景最有力的证据都在代码里,形式不同:
- 后端数据访问层的 SQL(MyBatis 映射文件、Java/Python 里的 SQL 字符串):JOIN ON 就是连接契约,
  字典表按 dict_code='X' 取值就是码表契约;
- 前端代码:接口路径、按 dictCode 取的字典、写死的指标编码。

本模块只读、不执行任何代码:
- 读取:zip 包在内存里逐个读取成员(只读代码/文本类扩展名,单成员与总量都有上限,不解压到磁盘);
  docx 读 word/document.xml 的正文段落。
- 抽取:SQL 的 FROM / JOIN 别名与 ON / WHERE 里的等值条件;字面量过滤识别码表;前端的接口与字典编码。
- 对齐到库表:代码里的生产表名(t_line_zone)与湖仓表名(dim_line_zone、ods_x_t_line_zone)不同,
  按业务词根(concept_hints.name_cores 学到的前缀)对齐;列名先精确匹配,再去掉短前缀(wd_zone_sn → zone_sn)。

契约只作为「命名证据」参与裁决:连接是否成立仍要数据证明(重叠率、父键唯一)。
"""
from __future__ import annotations

import io
import re
import zipfile
from collections import defaultdict

import concept_hints

CODE_EXT = ("java", "xml", "sql", "ddl", "js", "ts", "jsx", "tsx", "vue", "py", "kt", "go", "cs", "scala",
            "yml", "yaml", "properties", "json", "md", "txt")
MEMBER_CAP = 1_000_000          # 单个成员最多读 1MB
TOTAL_CAP = 4_000_000           # 一个包最多读 4MB 文本
MAX_MEMBERS = 2000

_KW = {"on", "where", "left", "right", "inner", "outer", "full", "cross", "join", "group", "order", "limit",
       "union", "set", "select", "from", "as", "and", "or", "having", "values", "into", "using", "natural", "lateral"}
_TABLE = r'([`"\[]?[A-Za-z_][\w$]*[`"\]]?(?:\.[`"\[]?[A-Za-z_][\w$]*[`"\]]?)?)'
_FROM_JOIN = re.compile(r"\b(?:FROM|JOIN)\s+" + _TABLE + r"(?:\s+(?:AS\s+)?([A-Za-z_]\w*))?", re.I)
_ON = re.compile(r"\bON\b(.+?)(?=\b(?:LEFT|RIGHT|INNER|FULL|CROSS|JOIN|WHERE|GROUP|ORDER|LIMIT|UNION|HAVING)\b|\Z)",
                 re.I | re.S)
_WHERE = re.compile(r"\bWHERE\b(.+?)(?=\b(?:GROUP|ORDER|LIMIT|UNION|HAVING)\b|\Z)", re.I | re.S)
_EQ_COLS = re.compile(r"([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\.([A-Za-z_]\w*)")
_EQ_LIT = re.compile(r"([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*=\s*'([^']{1,64})'")
_STMT_SPLIT = re.compile(r"<\s*/?\s*(?:select|update|insert|delete|sql)\b[^>]*>|;\s*\n|\"\"\"|'''", re.I)
_MYBATIS_NOISE = re.compile(r"<include\b[^>]*/>|<include\b.*?</include>|<!--.*?-->|<!\[CDATA\[|\]\]>|"
                            r"</?(?:if|where|trim|foreach|choose|when|otherwise|set|bind|property)\b[^>]*>|"
                            r"[#$]\{[^}]*\}", re.I | re.S)
_API = re.compile(r"""["'`](?:\$\{[\w.]+\})?(/(?:(?:[\w\-]+|\$\{[\w.]+\})/)+(?:[\w\-]+|\$\{[\w.]+\})?/?)["'`]""")
_DICT = re.compile(r"""\b(?:dictCode|dict_code|dictType|dict_type|typeCode)\s*[:=]\s*["']([A-Za-z_][\w\-]{1,48})["']""")
_FILE_HDR = re.compile(r"^### 文件 (.+)$", re.M)
# 术语契约:SQL 列后的注释(<!-- 线别ID --> 或 -- 线别ID),以及 Java 字段前的文档注释(/** 线别代码 */ private String weZoneSn;)
_SQL_COL_LABEL = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)(?:\s+AS\s+[A-Za-z_]\w*)?\s*,?\s*"
                            r"(?:<!--\s*([^<>\n]{1,30}?)\s*-->|--\s*([^\n]{1,30}))", re.I)
_JAVA_FIELD = re.compile(r"/\*\*\s*\n?\s*\*?\s*([^\n*@]{1,30}?)\s*\n(?:\s*\*[^\n]*\n)*\s*\*/\s*\n"
                         r"(?:\s*@[^\n]*\n)*\s*private\s+[\w<>, ?]+\s+([a-z]\w*)\s*;")


def _snake(name):
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()
# 代码与文档里常有内网地址、测试 / 生产环境主机:读入时就换成占位,不进入提示词与证据
_URL_HOST = re.compile(r"\b(https?|jdbc:[a-z0-9]+|mysql|redis|mongodb)://[^/\s\"'<>]+", re.I)
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")


def scrub(text):
    """去掉 URL 主机部分与 IP 地址,保留路径(接口路径是有用的契约)。"""
    text = _URL_HOST.sub(lambda m: m.group(1) + "://[地址]", text)
    return _IPV4.sub("[地址]", text)


def _bare_ident(x):
    x = x.strip('`"[]')
    return x.split(".")[-1].strip('`"[]')


def read_zip(raw):
    """zip 包 → 带「### 文件 路径」小节标题的文本;只读代码与文本类成员,不落盘。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, OSError, ValueError, EOFError):     # 非 zip 或目录区损坏
        return ""
    with zf:
        return _read_members(zf)


def _read_members(zf):
    out, total = [], 0
    for info in zf.infolist()[:MAX_MEMBERS]:
        name = info.filename
        if not info.flag_bits & 0x800:          # 未声明 UTF-8 的包多为 Windows 中文环境打包(GBK)
            try:
                name = name.encode("cp437").decode("gbk")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if info.is_dir() or ext not in CODE_EXT or info.file_size > MEMBER_CAP or "__MACOSX" in name \
                or "/node_modules/" in f"/{name}":
            continue
        if total + info.file_size > TOTAL_CAP:
            break
        try:
            with zf.open(info) as fh:
                data = fh.read(MEMBER_CAP + 1)[:MEMBER_CAP]
        except Exception:
            continue
        txt = scrub(data.decode("utf-8", errors="replace"))
        total += len(data)
        out.append(f"### 文件 {name}\n{txt}")
    return "\n\n".join(out)


def read_docx(raw):
    """docx → 段落文本(读 word/document.xml,段落换行、表格单元格用「|」分隔)。"""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    except Exception:
        return ""
    xml = re.sub(r"<w:instrText[^>]*>.*?</w:instrText>", "", xml, flags=re.S)   # 域代码(HYPERLINK 等)
    xml = re.sub(r"</w:tr>", "\n", xml)
    # 表格单元格内的段落不换行,单元格之间用「|」分隔,一行一条
    xml = re.sub(r"<w:tc\b.*?</w:tc>", lambda m: re.sub(r"</w:p>", " ", m.group(0)) + " | ", xml, flags=re.S)
    xml = re.sub(r"</w:p>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    for a, b in (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"), ("&quot;", '"'), ("&apos;", "'")):
        text = text.replace(a, b)
    return scrub(re.sub(r"\n{3,}", "\n\n", text).strip())


def _files(text, source):
    """把「### 文件 路径」小节拆开;没有小节标题时整段算一个文件。"""
    heads = list(_FILE_HDR.finditer(text))
    if not heads:
        return [(source, text)]
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out.append((f"{source}:{m.group(1)}", text[m.end():end]))
    return out


def _sql_contracts(fname, text):
    joins, books = [], []
    body = _MYBATIS_NOISE.sub(" ", text)
    for stmt in _STMT_SPLIT.split(body):
        if not re.search(r"\b(from|join)\b", stmt, re.I):
            continue
        alias = {}
        for tbl, al in _FROM_JOIN.findall(stmt):
            t = _bare_ident(tbl)
            if t.lower() in _KW:
                continue
            alias[t.lower()] = t
            if al and al.lower() not in _KW:
                alias[al.lower()] = t
        if len(alias) < 2:
            continue
        conds = [m.group(1) for m in _ON.finditer(stmt)] + [m.group(1) for m in _WHERE.finditer(stmt)]
        for cond in conds:
            pairs = []
            for a, ac, b, bc in _EQ_COLS.findall(cond):
                ta, tb = alias.get(a.lower()), alias.get(b.lower())
                if not ta or not tb or ta.lower() == tb.lower():
                    continue
                env = ac.lower() == bc.lower() and re.search(
                    r"tenant|tenement|dept|data_auth|del_flag|company|org", ac, re.I) is not None
                pairs.append((a.lower(), ta, ac, b.lower(), tb, bc, env))
                joins.append({"a": [ta, ac], "b": [tb, bc], "file": fname, "envelope": env})
            # 码表:同一个 ON 子句里,字典表按字面量分组过滤、并与业务表的某一列等值连接
            for a, col, lit in _EQ_LIT.findall(cond):
                dt = alias.get(a.lower())
                if not dt or "dict" not in dt.lower() or not re.search(r"dict|type|code", col, re.I):
                    continue
                for pa, ta, ac, pb, tb, bc, env in pairs:
                    if env:
                        continue
                    if pa == a.lower():
                        books.append({"table": tb, "column": bc, "dictionary": dt, "group_column": col,
                                      "group": lit, "file": fname})
                    elif pb == a.lower():
                        books.append({"table": ta, "column": ac, "dictionary": dt, "group_column": col,
                                      "group": lit, "file": fname})
    return joins, books


def _good_label(label, column):
    """像字段名的中文标签才收:短、无空白、非开发者评注;通用 id 列不打标签。"""
    return (bool(re.search(r"[\u4e00-\u9fa5]", label)) and len(label) <= 12 and not re.search(r"\s|[?？]", label)
            and column.lower() != "id")


def _labels(fname, body, alias_table):
    out = []
    for a, col, lab1, lab2 in _SQL_COL_LABEL.findall(body):
        label = (lab1 or lab2 or "").strip()
        if label and _good_label(label, col):
            out.append({"table": alias_table.get(a.lower()), "column": col, "label": label, "file": fname})
    for label, field in _JAVA_FIELD.findall(body):
        label = label.strip()
        if _good_label(label, _snake(field)):
            out.append({"table": None, "column": _snake(field), "label": label, "file": fname})
    return out


def extract(texts):
    """[(来源名, 文本)] → 连接契约、码表契约、术语契约、接口与字典使用。去重,保留首个出处。"""
    joins, books, apis, dicts, labels = [], [], [], [], []
    seen = set()
    for source, text in texts:
        for fname, body in _files(text, source):
            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
            if re.search(r"\b(select|join)\b", body, re.I) and ext not in ("md", "txt"):
                j, b = _sql_contracts(fname, body)
                for x in j:
                    k = ("j", *sorted([(x["a"][0].lower(), x["a"][1].lower()), (x["b"][0].lower(), x["b"][1].lower())]))
                    if k not in seen:
                        seen.add(k)
                        joins.append(x)
                for x in b:
                    k = ("b", x["table"].lower(), x["column"].lower(), x["group"])
                    if k not in seen:
                        seen.add(k)
                        books.append(x)
            if ext in ("xml", "java", "sql", "kt", "py"):
                alias = {}
                for tbl, al in _FROM_JOIN.findall(_MYBATIS_NOISE.sub(" ", body)):
                    t = _bare_ident(tbl)
                    if t.lower() not in _KW:
                        alias[t.lower()] = t
                        if al and al.lower() not in _KW:
                            alias[al.lower()] = t
                for x in _labels(fname, body, alias):
                    k = ("l", (x["table"] or "").lower(), x["column"].lower())
                    if k not in seen:
                        seen.add(k)
                        labels.append(x)
            if ext in ("js", "ts", "jsx", "tsx", "vue"):
                for p in _API.findall(body):
                    if ("a", p) not in seen:
                        seen.add(("a", p))
                        apis.append({"path": p, "file": fname})
                for d in _DICT.findall(body):
                    if ("d", d) not in seen:
                        seen.add(("d", d))
                        dicts.append({"group": d, "file": fname})
    return {"joins": joins, "codebooks": books, "apis": apis[:200], "dicts": dicts, "labels": labels}


def _column(cols, name):
    """列名对齐:精确(不分大小写);否则去掉一个 ≤3 字母的前缀再试(wd_zone_sn → zone_sn)。"""
    low = {}
    for c in cols:                      # 服务端的 tab_cols 每列是 (列名, 类型)
        c = c if isinstance(c, str) else c[0]
        low[c.lower()] = c
    n = name.lower()
    if n in low:
        return low[n]
    parts = n.split("_", 1)
    if len(parts) == 2 and len(parts[0]) <= 3 and parts[1] in low:
        return low[parts[1]]
    return None


def _table_matcher(tab_cols):
    """代码表名 → 库表名。库表名的每个「词根后缀」(至少两个词,或一个 ≥5 字母的词)建索引,
    代码表名按最长后缀匹配;小样本库学不出前缀时,t_line_zone 仍能对上 dim_line_zone。"""
    tables = list(tab_cols)
    exact = {t.lower(): t for t in tables}
    by_suffix = defaultdict(set)
    for t in tables:
        tk = concept_hints.clean_name(t).split("_")
        for i in range(len(tk)):
            suf = tk[i:]
            if len(suf) >= 2 or len(suf[0]) >= 5:
                by_suffix["_".join(suf)].add(t)

    def tables_for(name):
        n = _bare_ident(name).lower()
        if n in exact:
            return [exact[n]]
        tk = concept_hints.clean_name(n).split("_")
        found = None
        for i in range(len(tk)):
            key = "_".join(tk[i:])
            if not (len(tk) - i >= 2 or len(tk[i]) >= 5) or not by_suffix.get(key):
                continue
            hit = by_suffix[key]
            if found is None:
                found = hit
            elif hit >= found:
                # 更短的后缀覆盖了更多表(同一实体的整族):取整族。否则 t_line_zone 只会对上
                # 保留了 t_ 约定的贴源副本 ods_x_t_line_zone,漏掉维度表 dim_line_zone
                found = hit
            else:
                break
        return sorted(found or [])
    return tables_for


def resolve(contracts, tab_cols):
    """连接契约:代码里的表与列 → 库里的表与列(可能对应一个表族的多张表);
    租户、组织、删除标记等范围条件列上的等值连接(envelope)不计入。"""
    tables_for = _table_matcher(tab_cols)
    out = []
    for j in contracts.get("joins", []):
        if j.get("envelope"):
            continue
        for ta in tables_for(j["a"][0]):
            ca = _column(tab_cols[ta], j["a"][1])
            if not ca:
                continue
            for tb in tables_for(j["b"][0]):
                cb = _column(tab_cols[tb], j["b"][1])
                if cb:
                    out.append({"a": [ta, ca], "b": [tb, cb], "file": j["file"],
                                "code": f"{j['a'][0]}.{j['a'][1]} = {j['b'][0]}.{j['b'][1]}"})
    return out


def resolve_codebooks(contracts, tab_cols):
    """码表契约:业务列 → 字典表的哪一组(分组列 = 字面量)。编码按分组复用时,父键只在组内唯一
    ——与业务库的「类别作用域编码」是同一回事。"""
    tables_for = _table_matcher(tab_cols)
    out, seen = [], set()
    for b in contracts.get("codebooks", []):
        for t in tables_for(b["table"]):
            col = _column(tab_cols[t], b["column"])
            if not col:
                continue
            for d in tables_for(b["dictionary"]):
                g = _column(tab_cols[d], b["group_column"])
                key = (t, col, d, b["group"])
                if g and key not in seen:
                    seen.add(key)
                    out.append({"table": t, "column": col, "dictionary": d, "group_column": g,
                                "group": b["group"], "file": b["file"]})
    return out


def resolve_labels(contracts, tab_cols):
    """术语契约对到库表列:{(表, 列): 中文含义}。没写表的(Java 字段)对到所有有该列的表。"""
    tables_for = _table_matcher(tab_cols)
    out = {}
    for x in contracts.get("labels", []):
        targets = tables_for(x["table"]) if x.get("table") else list(tab_cols)
        for t in targets:
            col = _column(tab_cols[t], x["column"])
            if col and col.lower() != "id" and (t, col) not in out:
                out[(t, col)] = x["label"]
    return out


def hit(resolved, child_table, child_col, parent_table, parent_col):
    """一条候选关系是否有代码契约(不分方向)。返回契约或 None。"""
    want = {(child_table.lower(), child_col.lower()), (parent_table.lower(), parent_col.lower())}
    for r in resolved or []:
        if r.get("any_parent_col"):
            if (r["a"][0].lower(), r["a"][1].lower()) == (child_table.lower(), child_col.lower()) \
                    and r["b"][0].lower() == parent_table.lower():
                return r
            continue
        if {(r["a"][0].lower(), r["a"][1].lower()), (r["b"][0].lower(), r["b"][1].lower())} == want:
            return r
    return None


def text_for_prompt(contracts, resolved, scope=None, cap=2000):
    scope = {s.lower() for s in (scope or [])}
    lines = []
    for r in resolved:
        if scope and r["a"][0].lower() not in scope and r["b"][0].lower() not in scope:
            continue
        lines.append(f"- 连接:{r['a'][0]}.{r['a'][1]} = {r['b'][0]}.{r['b'][1]}(代码 {r['code']},{r['file'].split(':')[-1]})")
    for b in contracts.get("codebooks", [])[:20]:
        lines.append(f"- 码表:{b['table']}.{b['column']} 取值见 {b['dictionary']}({b['group_column']}='{b['group']}')")
    if contracts.get("labels"):
        lines.append("- 字段含义(代码注释):" + "、".join(f"{x['column']}={x['label']}" for x in contracts["labels"][:20]))
    if contracts.get("dicts"):
        lines.append("- 前端按字典取值:" + "、".join(d["group"] for d in contracts["dicts"][:12]))
    if contracts.get("apis"):
        lines.append("- 前端接口:" + "、".join(a["path"] for a in contracts["apis"][:12]))
    if not lines:
        return ""
    return ("[代码契约(从上传的代码中抽取;连接须按此提议并写出 child_key / parent_key)]\n" + "\n".join(lines))[:cap]


def summary_text(contracts, resolved):
    return (f"代码契约 · 连接 {len(contracts.get('joins', []))} 条(对上库表 {len(resolved)} 条)· "
            f"码表 {len(contracts.get('codebooks', []))} 条 · 字段含义 {len(contracts.get('labels', []))} 条 · "
            f"前端字典 {len(contracts.get('dicts', []))} 个 · "
            f"接口 {len(contracts.get('apis', []))} 个")


_COMMENT_REF = re.compile(r"(?:外键|关联|引用|参照|对应|references?|refers? to|fk)\s*[,，:：]?\s*(?:到|至|表)?\s*`?([A-Za-z_][\w.]*)`?", re.I)
# 关键词后先写中文名、括号里再写表名:「引用产线(dim_production_line.line_id)」
_COMMENT_REF_CN = re.compile(r"(?:外键|关联|引用|参照|对应)[^A-Za-z_\n,，;；。]{1,12}?[(（]`?([A-Za-z_][\w.]*)`?")


def comment_contracts(column_comments, tab_cols):
    """库表字段注释里写明的引用(「外键,关联 dim_product_line」):注释点名的表存在于库中时,
    当作连接契约——与代码契约同等,可替代键名相容性证据,数据门槛不变(DR-074)。
    父列取同名列,否则取父表唯一的主键式列(id / 表名_id)。"""
    lower = {t.lower(): t for t in tab_cols}
    out, seen = [], set()
    for (t, c), cm in (column_comments or {}).items():
        if t not in tab_cols:
            continue
        text = str(cm or "")
        for m in [*_COMMENT_REF.finditer(text), *_COMMENT_REF_CN.finditer(text)]:
            # 写法可能是 表、库.表 或 表.列:取第一个是库中表名的片段,紧跟其后的片段若是该表的列即为父列
            parts = m.group(1).split(".")
            at = next((i for i, x in enumerate(parts) if x.lower() in lower), None)
            if at is None:
                continue
            pt = lower[parts[at].lower()]
            if pt == t:
                continue
            pcols = [x for x, _ in tab_cols[pt]]
            named_col = parts[at + 1] if at + 1 < len(parts) else ""
            pc = next((x for x in pcols if x.lower() == named_col.lower()), None) if named_col else None
            pc = pc or next((x for x in pcols if x.lower() == c.lower()), None)
            if not pc:
                ids = [x for x in pcols if x.lower() == "id" or x.lower().endswith("_id")]
                pc = ids[0] if ids and (ids[0].lower() == "id" or pcols and pcols[0] == ids[0]) else None
            if not pc or (t, c, pt) in seen:
                continue
            seen.add((t, c, pt))
            out.append({"a": [t, c], "b": [pt, pc], "file": "库表注释", "code": f"{t}.{c} → {pt}(字段注释)",
                        "any_parent_col": True})          # 注释只点名了表,父列由数据裁决选
    return out
