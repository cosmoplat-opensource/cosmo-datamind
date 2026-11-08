#!/usr/bin/env python3
"""DataMind(cosmo-datamind)在 BIRD mini-dev 冻结划分上的对比实验。

- 起一个隔离的 DataMind 实例:同一份代码,workdir 为已提交的种子拷贝,模型配置读 cosmo-datamind/.env;
- 每个 BIRD 库经 /api/build/connect 登记为 SQLite 数据源;构建智能体按构建体检的分轮计划只从数据建本体,不读任何题目;
- 测试集(fold B)逐题调用 /api/chat(限定为该库的本体),取第一个分析的 SQL,
  在只读库上执行,与标准 SQL 的结果按 BIRD 执行准确率规则(结果行集合相等)比较。

用法:
  python3 bird_datamind.py build  [db ...]      # 构建本体(缺省全部 11 个库)
  python3 bird_datamind.py qa     [db ...]      # 在测试集上问数并打分
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

EXP = Path(__file__).resolve().parent
MAIN = EXP.parent / "cosmo-datamind"
ROOT = Path(os.environ["DM_ROOT"]) if os.environ.get("DM_ROOT") else MAIN   # 可指向实验分支的工作树
BIRD = EXP / "EvoOntology" / "benchmarks" / "bird"
DBDIR = Path(os.environ["DM_DBDIR"]) if os.environ.get("DM_DBDIR") else BIRD / "data" / "mini_dev_data" / "dev_databases"   # 协议 v2:模式变体库目录
SOURCE_TAG = os.environ.get("DM_SOURCE_TAG", "")   # 协议 v2:登记数据源名后缀,变体库与原库不同名
SPLIT = BIRD / "data" / os.environ.get("DM_SPLITROOT", "minidev")   # minidev=A→B;minidev_rev=B→A
OUT = EXP / "runs" / "datamind"
QA_OUT = Path(os.environ["DM_QA_OUT"]) if os.environ.get("DM_QA_OUT") else None   # 指定时问数结果写到这里
WORK = OUT / "workdir"
PORT = int(os.environ.get("DM_PORT", "8099"))
BASE = f"http://127.0.0.1:{PORT}"
ALL_DBS = sorted(p.name for p in DBDIR.iterdir() if p.is_dir() and (SPLIT / os.environ.get("DM_SPLIT", "test") / f"{p.name}.json").exists())
SKILLS = ["ontology-semi-auto", "ontology-build-method", "ontology-cross-check"]
STYLE = os.environ.get("DM_STYLE", "exact")
SPLIT_NAME = os.environ.get("DM_SPLIT", "test")
EVIDENCE = os.environ.get("DM_EVIDENCE") == "1"
# DR-088 构建时随附的业务问题样本:DM_WORKLOAD 指向 {库名: [问题...]} 的 JSON(只含问题,不含答案)
WORKLOAD = json.loads(Path(os.environ["DM_WORKLOAD"]).read_text()) if os.environ.get("DM_WORKLOAD") else {}   # 按 BIRD oracle knowledge 设置在问题后附上 evidence
CANDS = int(os.environ.get("DM_CANDIDATES", "1"))       # >1:多路候选与执行结果投票(DR-081)
TAG = os.environ.get("DM_TAG", "")       # test=留出测试集;validation=开发诊断只用验证集          # exact=精确作答;report=分析报告(产品默认)


def env() -> dict:
    e = dict(os.environ)
    for line in (MAIN / ".env").read_text().splitlines():
        m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line.strip())
        if m and m.group(2).strip():
            e[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    if not WORK.exists():
        WORK.mkdir(parents=True)
        names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "HEAD", "workdir"], cwd=MAIN, text=True).split()
        for n in names:
            dest = OUT / n
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(subprocess.check_output(["git", "show", "HEAD:" + n], cwd=MAIN))
    e.update(DATAMIND_PIN_DRIVER="openai",          # DR-091 全程只用配置的 DeepSeek,接口失败时重试而不改用其他模型
             DATAMIND_WORKDIR=str(WORK), DATAMIND_DB=str(MAIN / "examples/demo_metrics.db"),
             DATAMIND_HOST="127.0.0.1", DATAMIND_PORT=str(PORT), PYTHONUNBUFFERED="1")
    return e


def http(path, body=None, timeout=900):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def sse(path, body, log):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
    done = {}
    with urllib.request.urlopen(req, timeout=3600) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            if ev.get("type") == "step":
                log.write(f"  {ev.get('step')} {'✓' if ev.get('ok') else '✗'} {ev.get('info', '')}\n")
            elif ev.get("type") in ("done", "error"):
                done = ev
                log.write(f"== {ev.get('type')} {json.dumps({k: ev.get(k) for k in ('graph_key', 'stats', 'error', 'method')}, ensure_ascii=False)}\n")
            log.flush()
    return done


def start_server():
    proc = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT, env=env(), start_new_session=True,
                            stdout=(OUT / "server.log").open("a"), stderr=subprocess.STDOUT)
    for _ in range(120):
        try:
            http("/api/build/defaults", timeout=5)
            return proc
        except Exception:
            time.sleep(1)
    raise SystemExit("DataMind 实例未能启动,见 runs/datamind/server.log")


def source_id(db):
    name = f"BIRD-{db}{SOURCE_TAG}"
    for s in http("/api/build/sources")["sources"]:
        if s.get("name") == name and s.get("ready"):
            return s["id"]
    r = http("/api/build/connect", {"kind": "sqlite", "name": name, "path": str(DBDIR / db / f"{db}.sqlite")})
    if r.get("error"):
        raise RuntimeError(r["error"])
    return r["conn"]["id"]


def graphs_file():
    return Path(os.environ["DM_GRAPHS"]) if os.environ.get("DM_GRAPHS") else OUT / "graphs.json"   # 协议 v2:外部本体映射


def bind_graph_source(db, graph):
    """协议 v2:让图谱在本库上执行。/api/chat 按 IR 的 build_manifest.source.id 找已登记的 SQLite 源
    (server._qa_source_for);转换来的 IR 没有来源、变体库(DM_DBDIR)的来源也与原登记不同,
    这里登记本库并把来源 id 回填进 WORK 里的 built_*.json。文件内容变了但键不变(键只在安装时算)。"""
    if not graph or not graph.startswith("built_"):
        return None
    path = WORK / f"{graph}.json"
    if not path.exists():
        raise SystemExit(f"图谱文件不存在: {path}(先用 install_ir.py 安装)")
    src = source_id(db)
    ir = json.loads(path.read_text())
    bm = ir.setdefault("build_manifest", {})
    cur = (bm.get("source") or {}).get("id")
    if cur != src:
        bm["source"] = {"id": src, "name": f"BIRD-{db}{SOURCE_TAG}", "rebound_by": "bird_datamind.bind_graph_source"}
        path.write_text(json.dumps(ir, ensure_ascii=False, indent=1))
    return src


def build(dbs):
    keys = json.loads(graphs_file().read_text()) if graphs_file().exists() else {}
    for db in dbs:
        log = (OUT / f"build_{db}.log").open("w", encoding="utf-8")
        t0 = time.time()
        src = source_id(db)
        prep = http("/api/build/prep", {"source": src})
        base = ""
        for rd in prep.get("plan") or []:
            body = {"q": f"按构建计划第 {rd['round']} 轮:构建「{rd['label']}」相关表的业务对象与关系", "source": src,
                    "name": f"BIRD-{db}", "skills": SKILLS, "cqs": [], "tables": rd["tables"], "base_graph": base,
                    "workload": WORKLOAD.get(db, []),
                    "references": {"version": 1, "industry": {"id": "", "mode": "reference"},
                                   "ontology_standard": {"id": "", "mode": "reference"}}}
            log.write(f"### 第 {rd['round']} 轮 · {rd['count']} 张表\n")
            done = sse("/api/build/inquire", body, log)
            if done.get("graph_key"):
                base = done["graph_key"]
        keys[db] = {"graph": base, "source": src, "seconds": round(time.time() - t0, 1)}
        graphs_file().write_text(json.dumps(keys, ensure_ascii=False, indent=1))
        print(db, keys[db], flush=True)
        log.close()


def run_sql(db, sql, timeout=60):
    con = sqlite3.connect(f"file:{DBDIR / db / (db + '.sqlite')}?mode=ro", uri=True)
    deadline = time.time() + timeout
    con.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 10000)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _compact_anchor(anchor):
    """把 /api/chat 的锚定视图压成几行:对象数、关系数、JOIN 键(最多 12 条)、键来源计数、最终 SQL 实际用到的表。"""
    if not isinstance(anchor, dict):
        return None
    objs = [o for o in anchor.get("objects") or [] if isinstance(o, dict)]
    rels = [x for x in anchor.get("relations") or [] if isinstance(x, dict)]
    src = {}
    for x in rels:
        k = str(x.get("key_src") or "")
        src[k] = src.get(k, 0) + 1
    return {"objects": len(objs), "relations": len(rels),
            "keys": [str(x.get("key"))[:120] for x in rels if x.get("has_key")][:12],
            "key_src": src, "metrics": len(anchor.get("metrics") or []),
            "used": [str(t) for t in (anchor.get("used") or [])][:12]}


def ask(db, q, graph):
    t0 = time.time()
    try:
        qtext = q["question"] + (f"\nEvidence: {q['evidence']}" if EVIDENCE and q.get("evidence") else "")
        r = http("/api/chat", {"q": qtext, "graphs": [graph], "nocache": 1, "cache_policy": "bypass",
                              "answer_style": STYLE, "candidates": CANDS}, timeout=900)
    except Exception as exc:                                      # noqa: BLE001
        return {"question_id": q["question_id"], "error": f"request: {exc}", "seconds": round(time.time() - t0, 1)}
    results = r.get("results") or []
    sqls = [x.get("sql") for x in results]
    rec = {"question_id": q["question_id"], "difficulty": q.get("difficulty"), "sqls": sqls,
           "seconds": round(time.time() - t0, 1),
           "blocked": [s.get("info") for s in r.get("steps", []) if s.get("step") == "ontology_gate" and not s.get("ok")],
           "steps": [[s.get("step"), s.get("ok"), str(s.get("info"))[:120]] for s in r.get("steps", [])],
           "narrative": (r.get("narrative") or "")[:200],
           "candidates": [c.get("sql") for c in r.get("candidates") or []],
           "anchor": _compact_anchor(r.get("anchor"))}          # 协议 v2:本体使用率的依据(锚定对象/关系与 JOIN 键)
    try:
        gold = set(run_sql(db, q["SQL"], timeout=300))
    except Exception as exc:                                      # noqa: BLE001
        rec["error"] = f"gold: {exc}"
        return rec
    rec["ex_first"] = rec["ex_any"] = 0
    for i, sql in enumerate(sqls):
        try:
            ok = set(run_sql(db, sql)) == gold
        except Exception as exc:                                  # noqa: BLE001
            rec.setdefault("pred_errors", []).append(str(exc)[:120])
            ok = False
        if ok:
            rec["ex_any"] = 1
            if i == 0:
                rec["ex_first"] = 1
            break
    return rec


def qa(dbs):
    keys = json.loads(graphs_file().read_text())
    out = {}
    for db in dbs:
        qs = json.loads((SPLIT / SPLIT_NAME / f"{db}.json").read_text())["questions"]
        if os.environ.get("DM_LIMIT"):                    # 冒烟/诊断:每库只取前 N 题(不用于正式成绩)
            qs = qs[:int(os.environ["DM_LIMIT"])]
        qdir = QA_OUT or (OUT / f"qa_{STYLE}{TAG}_{SPLIT_NAME}")
        done_f = qdir / f"{db}.json"
        if done_f.exists():                               # 续跑:该库已有完整结果则跳过(进程被中断后重启用)
            try:
                if len(json.loads(done_f.read_text())) == len(qs):
                    out[db] = json.loads(done_f.read_text()); print(db, "skip (done)", flush=True); continue
            except ValueError:
                pass
        if os.environ.get("DM_GRAPHS") or os.environ.get("DM_DBDIR") or not keys[db].get("source"):
            keys[db]["source"] = bind_graph_source(db, keys[db]["graph"])
        with ThreadPoolExecutor(max_workers=4) as pool:
            recs = list(pool.map(lambda q: ask(db, q, keys[db]["graph"]), qs))
        out[db] = recs
        n = len(recs)
        print(db, "EX(first)", sum(r.get("ex_first", 0) for r in recs), "/", n,
              "EX(any)", sum(r.get("ex_any", 0) for r in recs), flush=True)
        qdir = QA_OUT or (OUT / f"qa_{STYLE}{TAG}_{SPLIT_NAME}")
        qdir.mkdir(parents=True, exist_ok=True)
        (qdir / f"{db}.json").write_text(json.dumps(recs, ensure_ascii=False, indent=1))
    return out


def serve_for(cmd):
    """Mode "with": start a DataMind instance, bind every graph of DM_GRAPHS to its database, then run an external
    command (e.g. the ReAct evaluation) with EVO_DM_URL pointing at this instance; stop the instance afterwards."""
    keys = json.loads(graphs_file().read_text())
    for db, row in keys.items():
        bind_graph_source(db, row.get("graph"))
    env = dict(os.environ, EVO_DM_URL=BASE, EVO_DM_GRAPHS=str(graphs_file()))
    return subprocess.call(cmd, shell=True, env=env)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    mode, dbs = sys.argv[1], (sys.argv[2:] or ALL_DBS)
    proc = start_server()
    try:
        if mode == "with":
            sys.exit(serve_for(" ".join(sys.argv[2:])))
        build(dbs) if mode == "build" else qa(dbs)
    finally:
        os.killpg(proc.pid, signal.SIGTERM)


if __name__ == "__main__":
    main()
