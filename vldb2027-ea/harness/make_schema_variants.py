#!/usr/bin/env python3
"""协议 v2 的模式语义变体库(见 v2_conventions.md)。

- nofk:数据不变,去掉所有 CREATE TABLE 中的表级 FOREIGN KEY 约束与列级 REFERENCES 子句;
  主键、唯一、非空、默认值、CHECK、索引、视图、触发器保留。13 个库(BIRD 11 + ind108 + ind200)。
- bare:nofk 之上再删除字典/元数据表(dim_column_metadata、dim_table_metadata、_dm_tables、_dm_columns),
  并去掉 CREATE 语句里的 SQL 注释(/* */ 与 --)。只对 ind108、ind200。

产物:variants/<S>/dev_databases/<db>/<db>.sqlite 与 variants/<S>/verify.json。原库不改。
校验:每表无外键、每表行数一致、各划分的全部标准 SQL 在变体上结果多重集一致。
用法: python3 make_schema_variants.py [nofk|bare|all] [db ...]
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

EXP = Path(__file__).resolve().parent
BIRD = EXP / "EvoOntology" / "benchmarks" / "bird"
DBDIR = BIRD / "data" / "mini_dev_data" / "dev_databases"
VAR = EXP / "variants"
BIRD_DBS = ["california_schools", "card_games", "codebase_community", "debit_card_specializing", "european_football_2",
            "financial", "formula_1", "student_club", "superhero", "thrombosis_prediction", "toxicology"]
IND_DBS = ["ind108", "ind200"]
SPLITS = {"minidev": BIRD_DBS, "minidev_rev": BIRD_DBS, "ind_fwd": IND_DBS, "ind_rev": IND_DBS}
DOC_TABLES = {"dim_column_metadata", "dim_table_metadata", "_dm_tables", "_dm_columns"}   # 只记录其他表含义的表
GOLD_TIMEOUT = 300


# ---------- CREATE TABLE 文本处理 ----------
def strip_comments(sql: str) -> str:
    """去掉 /* */ 与 -- 注释(不动字符串字面量)。"""
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c in "'\"`[":
            close = "]" if c == "[" else c
            j = i + 1
            while j < n:
                if sql[j] == close:
                    if close in "'\"`" and j + 1 < n and sql[j + 1] == close:   # 转义的成对引号
                        j += 2; continue
                    break
                j += 1
            out.append(sql[i:j + 1]); i = j + 1
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2); i = n if j < 0 else j + 2
            out.append(" ")
        elif sql.startswith("--", i):
            j = sql.find("\n", i); i = n if j < 0 else j
        else:
            out.append(c); i += 1
    return "".join(out)


def split_top_level(body: str) -> list[str]:
    """按括号深度为 0 的逗号拆分列/约束定义(忽略引号内与括号内的逗号)。"""
    parts, depth, cur, i, n = [], 0, [], 0, len(body)
    while i < n:
        c = body[i]
        if c in "'\"`[":
            close = "]" if c == "[" else c
            j = i + 1
            while j < n:
                if body[j] == close:
                    if close in "'\"`" and j + 1 < n and body[j + 1] == close:
                        j += 2; continue
                    break
                j += 1
            cur.append(body[i:j + 1]); i = j + 1; continue
        if body.startswith("/*", i):                      # 注释当作不透明文本,其中的逗号不拆分
            j = body.find("*/", i + 2); j = n if j < 0 else j + 2
            cur.append(body[i:j]); i = j; continue
        if body.startswith("--", i):
            j = body.find("\n", i); j = n if j < 0 else j
            cur.append(body[i:j]); i = j; continue
        if c == "(": depth += 1
        elif c == ")": depth -= 1
        if c == "," and depth == 0:
            parts.append("".join(cur)); cur = []
        else:
            cur.append(c)
        i += 1
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


_FK_TABLE = re.compile(r"^\s*(CONSTRAINT\s+(\"[^\"]*\"|`[^`]*`|\[[^\]]*\]|\w+)\s+)?FOREIGN\s+KEY\b", re.I | re.S)
# 列级:[CONSTRAINT name] REFERENCES tbl [(cols)] [ON DELETE/UPDATE action]* [MATCH name]* [[NOT] DEFERRABLE [INITIALLY ...]]
_FK_COL = re.compile(
    r"\s*(CONSTRAINT\s+(\"[^\"]*\"|`[^`]*`|\[[^\]]*\]|\w+)\s+)?REFERENCES\s+(\"[^\"]*\"|`[^`]*`|\[[^\]]*\]|\w+)"
    r"(\s*\([^)]*\))?"
    r"(\s+ON\s+(DELETE|UPDATE)\s+(SET\s+NULL|SET\s+DEFAULT|CASCADE|RESTRICT|NO\s+ACTION))*"
    r"(\s+MATCH\s+\w+)*"
    r"(\s+(NOT\s+)?DEFERRABLE(\s+INITIALLY\s+(DEFERRED|IMMEDIATE))?)?",
    re.I | re.S)


def strip_foreign_keys(create_sql: str) -> tuple[str, int]:
    """返回(去掉外键后的 CREATE TABLE 语句, 删除的外键约束数)。注释先去掉,避免注释内文字干扰。"""
    sql = strip_comments(create_sql)
    lp = sql.index("(")
    depth, rp = 0, -1
    for i in range(lp, len(sql)):
        if sql[i] == "(": depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0: rp = i; break
    head, body, tail = sql[:lp], sql[lp + 1:rp], sql[rp + 1:]
    removed, kept = 0, []
    for part in split_top_level(body):
        if _FK_TABLE.match(part):
            removed += 1; continue
        new, k = _FK_COL.subn(" ", part)
        removed += k
        kept.append(new.rstrip())
    return head + "(" + ",".join(kept) + ")" + tail, removed


# ---------- 建库 ----------
def build_variant(db: str, variant: str) -> dict:
    src = DBDIR / db / f"{db}.sqlite"
    dst_dir = VAR / variant / "dev_databases" / db
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{db}.sqlite"
    if dst.exists(): dst.unlink()
    orig = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    rows = orig.execute("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY rowid").fetchall()
    orig.close()
    dropped, fk_removed, comments_stripped = [], 0, 0
    con = sqlite3.connect(f"file:{dst}", uri=True)          # uri 模式才能 ATTACH 只读 file: 路径
    con.execute("PRAGMA foreign_keys = OFF")
    order = {"table": 0, "index": 1, "view": 2, "trigger": 3}
    tables = []
    for typ, name, tbl, sql in sorted(rows, key=lambda r: order.get(r[0], 9)):
        if name.startswith("sqlite_"):
            continue
        if variant == "bare" and (tbl in DOC_TABLES or name in DOC_TABLES):
            if typ == "table": dropped.append(name)
            continue
        if typ == "table":
            new_sql, k = strip_foreign_keys(sql)
            fk_removed += k
            if variant == "bare":
                if "/*" in sql or "--" in sql: comments_stripped += 1
            else:
                # nofk 保留注释:只在外键子句不含注释时沿用原文去掉外键,否则用去注释版本
                keep_comments, k2 = _strip_fk_keep_comments(sql)
                if keep_comments is not None and k2 == k:
                    new_sql = keep_comments
            con.execute(new_sql)
            tables.append(name)
        else:
            con.execute(sql)
    con.commit()
    con.execute("ATTACH DATABASE ? AS orig", (f"file:{src}?mode=ro",))
    # 自增序列表
    for t in tables:
        con.execute(f'INSERT INTO main."{t}" SELECT * FROM orig."{t}"')
    has_seq = con.execute("SELECT 1 FROM orig.sqlite_master WHERE name='sqlite_sequence'").fetchone()
    if has_seq and con.execute("SELECT 1 FROM main.sqlite_master WHERE name='sqlite_sequence'").fetchone():
        con.execute("DELETE FROM main.sqlite_sequence")
        con.execute("INSERT INTO main.sqlite_sequence SELECT * FROM orig.sqlite_sequence")
    con.commit()
    con.execute("DETACH DATABASE orig")
    con.close()
    return {"db": db, "variant": variant, "tables": len(tables), "fk_removed": fk_removed,
            "dropped_tables": dropped, "comments_stripped_tables": comments_stripped}


def _strip_fk_keep_comments(sql: str):
    """尽量保留注释地去掉外键:只有当每个外键子句本身不含注释时才用原文处理。"""
    try:
        lp = sql.index("(")
        depth, rp = 0, -1
        for i in range(lp, len(sql)):
            if sql[i] == "(": depth += 1
            elif sql[i] == ")":
                depth -= 1
                if depth == 0: rp = i; break
        head, body, tail = sql[:lp], sql[lp + 1:rp], sql[rp + 1:]
        removed, kept = 0, []
        norm = lambda x: re.sub(r"\s+", " ", strip_comments(x)).strip()
        for part in split_top_level(body):
            if _FK_TABLE.match(strip_comments(part)):
                removed += 1; continue                    # 整个表级外键定义(连同其注释)一起去掉
            if _FK_COL.search(strip_comments(part)):
                new, k = _FK_COL.subn(" ", part)          # 列级 REFERENCES 子句后面的注释保留
                plain, k_plain = _FK_COL.subn(" ", strip_comments(part))
                if k != k_plain or norm(new) != norm(plain):
                    return None, 0                        # 注释夹在子句中间,无法安全保留,退回去注释版本
                removed += k
                kept.append(new.rstrip())
            else:
                kept.append(part)
        return head + "(" + ",".join(kept) + ")" + tail, removed
    except ValueError:
        return None, 0


# ---------- 校验 ----------
def _run(con, sql, timeout=GOLD_TIMEOUT):
    deadline = time.time() + timeout
    con.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 10000)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.set_progress_handler(None, 0)


def verify(db: str, variant: str) -> dict:
    src = DBDIR / db / f"{db}.sqlite"
    dst = VAR / variant / "dev_databases" / db / f"{db}.sqlite"
    o = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    v = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    rep = {"db": db, "variant": variant, "fk_left": {}, "row_mismatch": {}, "column_mismatch": {},
           "tables_orig": 0, "tables_variant": 0, "gold": {}, "gold_mismatch": [], "gold_skipped": []}
    o_tabs = [r[0] for r in o.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    v_tabs = [r[0] for r in v.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    rep["tables_orig"], rep["tables_variant"] = len(o_tabs), len(v_tabs)
    rep["missing_tables"] = sorted(set(o_tabs) - set(v_tabs))
    for t in v_tabs:
        fk = v.execute(f'PRAGMA foreign_key_list("{t}")').fetchall()
        if fk: rep["fk_left"][t] = len(fk)
        n_o = o.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        n_v = v.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        if n_o != n_v: rep["row_mismatch"][t] = [n_o, n_v]
        c_o = [(r[1], r[2], r[3], r[5]) for r in o.execute(f'PRAGMA table_info("{t}")')]
        c_v = [(r[1], r[2], r[3], r[5]) for r in v.execute(f'PRAGMA table_info("{t}")')]
        if c_o != c_v: rep["column_mismatch"][t] = {"orig": c_o, "variant": c_v}
    rep["fk_total_orig"] = sum(len(o.execute(f'PRAGMA foreign_key_list("{t}")').fetchall()) for t in o_tabs)
    for root, dbs in SPLITS.items():
        if db not in dbs: continue
        for part in ("construction", "validation", "test"):
            f = BIRD / "data" / root / part / f"{db}.json"
            if not f.exists(): continue
            qs = json.load(open(f))["questions"]
            ok = 0
            for q in qs:
                qid = q["question_id"]
                try:
                    r_o = _run(o, q["SQL"])
                except sqlite3.OperationalError as e:
                    if "interrupted" in str(e).lower():
                        rep["gold_skipped"].append({"split": f"{root}/{part}", "qid": qid, "why": "orig timeout"}); continue
                    rep["gold_mismatch"].append({"split": f"{root}/{part}", "qid": qid, "why": f"orig error {e}"}); continue
                try:
                    r_v = _run(v, q["SQL"])
                except sqlite3.OperationalError as e:
                    rep["gold_mismatch"].append({"split": f"{root}/{part}", "qid": qid, "why": f"variant error {e}"}); continue
                if Counter(map(tuple, r_o)) == Counter(map(tuple, r_v)): ok += 1
                else: rep["gold_mismatch"].append({"split": f"{root}/{part}", "qid": qid, "why": "result differs"})
            rep["gold"][f"{root}/{part}"] = [ok, len(qs)]
    o.close(); v.close()
    rep["pass"] = not (rep["fk_left"] or rep["row_mismatch"] or rep["column_mismatch"] or rep["gold_mismatch"]
                       or (variant == "nofk" and rep["missing_tables"]))
    return rep


def main():
    args = sys.argv[1:]
    which = args[0] if args else "all"
    dbs = args[1:]
    plan = []
    if which in ("nofk", "all"): plan.append(("nofk", dbs or BIRD_DBS + IND_DBS))
    if which in ("bare", "all"): plan.append(("bare", dbs or IND_DBS))
    for variant, vdbs in plan:
        out = {"variant": variant, "created": [], "verified": [], "all_pass": True, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        for db in vdbs:
            info = build_variant(db, variant)
            rep = verify(db, variant)
            out["created"].append(info); out["verified"].append(rep)
            out["all_pass"] &= rep["pass"]
            print(variant, db, "tables", info["tables"], "fk_removed", info["fk_removed"], "dropped", info["dropped_tables"],
                  "gold", rep["gold"], "PASS" if rep["pass"] else "FAIL", flush=True)
        (VAR / variant / "verify.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
        print(variant, "all_pass", out["all_pass"])


if __name__ == "__main__":
    main()
