#!/usr/bin/env python3
"""协议 v2:DataMind 原生构建(经 bird_datamind.py build 在隔离实例上运行),产物按 v2_conventions.md 归档。

用法: python3 build_dm_v2.py --track w|d --S full|nofk|bare [--dir fwd|rev|ind_fwd|ind_rev] [--port 8101] [--force] [db ...]

- track w:DM_WORKLOAD = 构建划分的问题文本(不含 evidence、SQL、答案),不截断地传入;服务端 DR-088 入口若仍按 200 条截断,
  在 meta 里记录 cap=200 与实际条数,不改 server.py;
- track d + S=full:缺省复制 runs/datamind/graphs.json 与 workdir 里的 IR(主仓代码构建,记录来源);加 --rebuild 用实验分支代码重建;
- 所有实际构建都在 cosmo-datamind-exp(exp/catalog)上运行:主仓代码没有 DR-088 工作负载入口;
- S=bare:经 DM_DBDIR / DM_SOURCE_TAG 指向变体库(由 bird_datamind.py 的 v2 扩展支持);
- 输出根 runs/v2/dm/<track>_<S>[_<dir>]/:graphs.json、ir/<db>.json、meta.json;日志 runs/v2/build_logs/dm-<track>__<S>[_<dir>]/。
bird_datamind.py 若尚未支持 DM_GRAPHS,则退化为:加锁 → 备份共享 graphs.json → 构建 → 抽取本批键 → 恢复共享文件。
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

EXP = Path(__file__).resolve().parent
BIRD = EXP / "EvoOntology" / "benchmarks" / "bird"
DATA = BIRD / "data"
ORIG_DBDIR = DATA / "mini_dev_data" / "dev_databases"
HARNESS = EXP / "bird_datamind.py"
SHARED_GRAPHS = EXP / "runs" / "datamind" / "graphs.json"
WORKDIR = EXP / "runs" / "datamind" / "workdir"
EXP_ROOT = EXP.parent / "cosmo-datamind-exp"       # DR-088 工作负载概念层只在实验分支(exp/catalog)实现;主仓 D8 代码没有 workload 入口
SPLITROOT = {"fwd": "minidev", "rev": "minidev_rev", "ind_fwd": "ind_fwd", "ind_rev": "ind_rev"}
BIRD_DBS = ["california_schools", "card_games", "codebase_community", "debit_card_specializing", "european_football_2",
            "financial", "formula_1", "student_club", "superhero", "thrombosis_prediction", "toxicology"]
IND_DBS = ["ind108", "ind200"]
SERVER_WORKLOAD_CAP = 200          # cosmo-datamind server.py DR-088 入口 [:200]


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def root_name(track, S, direction):
    return f"{track}_{S}" + (f"_{direction}" if track == "w" else "")


def harness_supports(var):
    return var in HARNESS.read_text()


def ir_counts(ir):
    rels = ir.get("relations") or []
    return {"objects": len(ir.get("objects") or []), "relations": len(rels),
            "verified": sum(1 for r in rels if r.get("status") == "verified"),
            "candidate": sum(1 for r in rels if r.get("status") == "candidate"),
            "concepts": len(ir.get("concepts") or []), "derived": len(ir.get("derived") or []),
            "themes": len(ir.get("themes") or []), "caveats": len(ir.get("caveats") or [])}


def copy_existing_d_full(root, dbs, force):
    """track d、S=full:复用已有的 data-only 本体。"""
    shared = json.loads(SHARED_GRAPHS.read_text())
    out = json.loads((root / "graphs.json").read_text()) if (root / "graphs.json").exists() else {}
    (root / "ir").mkdir(parents=True, exist_ok=True)
    meta = {"provenance": "copied from runs/datamind/graphs.json (data-only builds, DR-079 key-name fix)", "dbs": {}, "at": now()}
    for db in dbs:
        if db in out and not force:
            continue
        if db not in shared:
            meta["dbs"][db] = {"status": "missing in shared graphs.json"}
            continue
        key = shared[db]["graph"]
        src = WORKDIR / f"{key}.json"
        if not src.exists():
            meta["dbs"][db] = {"status": f"IR file missing: {src.name}"}
            continue
        shutil.copy2(src, root / "ir" / f"{db}.json")
        out[db] = dict(shared[db])
        meta["dbs"][db] = {"status": "copied", "graph": key, **ir_counts(json.loads(src.read_text()))}
    (root / "graphs.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    (root / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(json.dumps(meta, ensure_ascii=False))
    return 0 if all(v.get("status") == "copied" for v in meta["dbs"].values()) else 1


def make_workload(direction, dbs, path, S="full"):
    sr = SPLITROOT[direction]
    wl, counts = {}, {}
    for db in dbs:
        f = (EXP / "variants" / "opaque" / "questions" / sr / "construction" / f"{db}.json") if S == "opaque" \
            else (EXP / "variants" / "opaque_merged" / "workload" / sr / f"{db}.json") if S == "opaque_merged" \
            else DATA / sr / "construction" / f"{db}.json"
        qs = json.loads(f.read_text())["questions"]
        # 与 EvoOntology 的 construction_workload 信息对等:问题 + evidence(有则附),不含 SQL、答案
        wl[db] = [q["question"] + (f"\nEvidence: {q['evidence']}" if q.get("evidence") else "") for q in qs]
        counts[db] = len(qs)
    path.write_text(json.dumps(wl, ensure_ascii=False, indent=1))
    return counts


def build(track, S, direction, dbs, port, force, rebuild=False):
    if track == "d" and S == "full" and not rebuild:
        root = EXP / "runs" / "v2" / "dm" / root_name(track, S, direction)
        root.mkdir(parents=True, exist_ok=True)
        return copy_existing_d_full(root, dbs, force)
    if track == "w" and direction not in SPLITROOT:
        raise SystemExit("track w 需要 --dir")
    sfx = os.environ.get("DM_ROOT_SUFFIX", "")          # 另存一套构建(如 DR-093 改进版),不覆盖原构建
    name = root_name(track, S, direction) + sfx
    root = EXP / "runs" / "v2" / "dm" / name
    logs = EXP / "runs" / "v2" / "build_logs" / (f"dm-{track}__{S}" + (f"_{direction}" if track == "w" else "") + sfx)
    root.mkdir(parents=True, exist_ok=True); (root / "ir").mkdir(exist_ok=True); logs.mkdir(parents=True, exist_ok=True)
    graphs = json.loads((root / "graphs.json").read_text()) if (root / "graphs.json").exists() else {}
    todo = [db for db in dbs if force or db not in graphs]
    if not todo:
        print(json.dumps({"root": name, "status": "skip (all built)", "dbs": dbs}, ensure_ascii=False)); return 0
    env = dict(os.environ)
    env.update(DM_PORT=str(port), PYTHONUNBUFFERED="1", DM_ROOT=str(EXP_ROOT))
    if track == "w":
        env["DM_SPLITROOT"] = SPLITROOT[direction]
        wl_path = root / "workload.json"
        counts = make_workload(direction, todo, wl_path, S)
        env["DM_WORKLOAD"] = str(wl_path)
    else:
        counts = {db: 0 for db in todo}
    if S != "full":
        env["DM_DBDIR"] = str(EXP / "variants" / S / "dev_databases")
        env["DM_SOURCE_TAG"] = S
        if not harness_supports("DM_DBDIR"):
            raise SystemExit("bird_datamind.py 尚不支持 DM_DBDIR,S=bare 的 DataMind 构建需等待该扩展")
    use_graphs_env = harness_supports("DM_GRAPHS")
    meta = {"root": name, "track": track, "S": S, "dir": direction, "dbs": todo, "port": port, "started": now(),
            "workload_questions": counts, "server_workload_cap": SERVER_WORKLOAD_CAP,
            "workload_capped": {db: n > SERVER_WORKLOAD_CAP for db, n in counts.items()},
            "workload_content": "question + Evidence (same text as EvoOntology construction_workload; no SQL, no answers)", "graphs_env": use_graphs_env,
            "datamind_code": str(EXP_ROOT), "datamind_commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=EXP_ROOT, capture_output=True, text=True).stdout.strip()}
    log = (logs / "build.log").open("a")
    t0 = time.time()
    if use_graphs_env:
        env["DM_GRAPHS"] = str(root / "graphs.json")
        code = subprocess.call([sys.executable, str(HARNESS), "build", *todo], cwd=EXP, env=env, stdout=log, stderr=subprocess.STDOUT)
        new = json.loads((root / "graphs.json").read_text()) if (root / "graphs.json").exists() else {}
    else:                                   # 退化路径:共享 graphs.json 加锁、备份、恢复
        lock = (EXP / "runs" / "datamind" / ".graphs.lock").open("w")
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            backup = SHARED_GRAPHS.read_text() if SHARED_GRAPHS.exists() else "{}"
            code = subprocess.call([sys.executable, str(HARNESS), "build", *todo], cwd=EXP, env=env, stdout=log, stderr=subprocess.STDOUT)
            after = json.loads(SHARED_GRAPHS.read_text()) if SHARED_GRAPHS.exists() else {}
            before = json.loads(backup)
            new = {db: after[db] for db in todo if db in after and after[db] != before.get(db)}
            SHARED_GRAPHS.write_text(backup)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN); lock.close()
        meta["fallback"] = "shared graphs.json backed up and restored under lock"
    log.close()
    for db in todo:
        info = new.get(db)
        if not info or not info.get("graph"):
            meta.setdefault("failed", []).append(db); continue
        src = WORKDIR / f"{info['graph']}.json"
        if src.exists():
            shutil.copy2(src, root / "ir" / f"{db}.json")
            graphs[db] = info
            meta.setdefault("built", {})[db] = {"graph": info["graph"], "seconds": info.get("seconds"),
                                                 **ir_counts(json.loads(src.read_text()))}
        else:
            meta.setdefault("failed", []).append(f"{db}: IR missing {src.name}")
    (root / "graphs.json").write_text(json.dumps(graphs, ensure_ascii=False, indent=1))
    meta.update(exit=code, seconds=round(time.time() - t0, 1), finished=now())
    (root / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(json.dumps({k: meta.get(k) for k in ("root", "built", "failed", "seconds", "exit")}, ensure_ascii=False))
    return 0 if not meta.get("failed") and code == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", required=True, choices=["w", "d"])
    ap.add_argument("--S", required=True, choices=["full", "nofk", "bare", "opaque", "opaque_merged"])
    ap.add_argument("--dir", default="")
    ap.add_argument("--port", type=int, default=8101)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--rebuild", action="store_true", help="track d + S=full:不复制旧 IR,用实验分支代码重建")
    ap.add_argument("dbs", nargs="*")
    a = ap.parse_args()
    dbs = a.dbs or (IND_DBS if a.dir.startswith("ind") or a.S == "bare" else BIRD_DBS)   # nofk 只在 BIRD 上构建
    if a.track == "d" and a.S == "full" and not a.dbs:
        dbs = BIRD_DBS + IND_DBS
    sys.exit(build(a.track, a.S, a.dir, dbs, a.port, a.force, a.rebuild))


if __name__ == "__main__":
    main()
