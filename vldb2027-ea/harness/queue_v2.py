#!/usr/bin/env python3
"""协议 v2 的可续跑任务队列:构建 → 转换/评测 → 问数矩阵。

用法:
  python3 queue_v2.py plan                       # 生成 runs/v2/plan.json 与 plan.md(按优先级排列的全部任务)
  python3 queue_v2.py run [--phase build|qa] [--parallel 3] [--retry-failed] [--only 子串]
  python3 queue_v2.py status [--phase build|qa]

状态文件 runs/v2/queue_state.json:每个任务 status(pending/running/done/incomplete/failed/blocked/reused)、pid、起止、退出码、日志。
已完成的任务不重跑;失败任务默认不重跑,加 --retry-failed 才重跑;依赖失败的任务标 blocked。
任务命令见 plan.json,全部环境变量只含路径与开关,不含密钥(密钥由各脚本自行从 .env 读取)。

因子与路径遵守 v2_conventions.md:
  O ∈ {none, evo-d, evo-w, dm-d, dm-w}(另有 dm-d-legacy = 旧转换 C3e 的复用);Q ∈ {react, dm};S ∈ {full, nofk, bare}。
  Q=dm 与 DataMind 构建都在 cosmo-datamind-exp(D10 代码,消费概念层)上运行;旧 D8_r* 不复用;dm-d 本体用实验分支代码重建。
  BIRD 上 dm-* 在 full/nofk 下分别构建(新构建代码读声明外键作契约证据);工业库 nofk 未安排 dm 构建,dm-* 沿用 full;bare 在 bare 库重建。
其他 fork 的接口(若实际 CLI 不同,只需改 CONV_CMDS 一处):
  evo2dm.py <evo_root> <out_root>;install_ir.py <out_root>;DM2EVO_FULL=1 dm2evo.py <graphs.json> <out_root>;
  make_schema_ir.py <out> [--db-root] [dbs] + install_ir.py <out>;eval_construction.py --db-root <原库> [--dm g --ir-dir d | --evo root] --dbs ... --out <json>;
  bird_datamind.py 的 DM_GRAPHS / DM_DBDIR / DM_SOURCE_TAG;run_evaluation.py 的 EVO_DB_ROOT。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

EXP = Path(__file__).resolve().parent
BIRD = EXP / "EvoOntology" / "benchmarks" / "bird"
DATA = BIRD / "data"
PY_EVO = EXP / ".venv-evo" / "bin" / "python"
V2 = EXP / "runs" / "v2"
PLAN_F, STATE_F = V2 / "plan.json", V2 / "queue_state.json"
SPLITROOT = {"fwd": "minidev", "rev": "minidev_rev", "ind_fwd": "ind_fwd", "ind_rev": "ind_rev"}
BIRD_DBS = ["california_schools", "card_games", "codebase_community", "debit_card_specializing", "european_football_2",
            "financial", "formula_1", "student_club", "superhero", "thrombosis_prediction", "toxicology"]
IND_DBS = ["ind108", "ind200"]
BIRD_DIRS, IND_DIRS = ["fwd", "rev"], ["ind_fwd", "ind_rev"]
ALL_O = ["none", "evo-d", "evo-w", "dm-d", "dm-w"]
REPS = (1, 2, 3)
PORT0 = 8101
PARALLEL_REACT = 5

ORIG_DBROOT = DATA / "mini_dev_data" / "dev_databases"
CONV_CMDS = {
    "evo2dm": lambda evo_root, out: f'python3 "{EXP}/evo2dm.py" "{evo_root}" "{out}" && python3 "{EXP}/install_ir.py" "{out}"',
    "dm2evo": lambda graphs, out: f'DM2EVO_FULL=1 python3 "{EXP}/dm2evo.py" "{graphs}" "{out}"',
    "schema_ir": lambda S, out, dbs: (f'python3 "{EXP}/make_schema_ir.py" "{out}"'
                                      + (f' --db-root "{EXP}/variants/{S}/dev_databases"' if S != "full" else "")
                                      + " " + " ".join(dbs) + f' && python3 "{EXP}/install_ir.py" "{out}"'),
    # 金标关系来自原库的声明外键与字典,故 --db-root 固定为原库目录;变体只影响被评的本体
    "construction": lambda root, kind, dbs, out: (f'python3 "{EXP}/eval_construction.py" --db-root "{ORIG_DBROOT}" '
                                                  + (f'--dm "{root}/graphs.json" --ir-dir "{root}/ir"' if kind == "dm" else f'--evo "{root}"')
                                                  + " --dbs " + " ".join(dbs) + f' --out "{out}"'),
}


# ---------------------------------------------------------------- 划分与根目录
def test_questions(direction):
    out = {}
    for f in sorted((DATA / SPLITROOT[direction] / "test").glob("*.json")):
        d = json.loads(f.read_text())
        out[d["db_id"]] = [str(q["question_id"]) for q in d["questions"]]
    return out


def evo_root(O, S, direction):
    t = O.split("-")[1]
    return V2 / "evo" / (f"{t}_{S}" + (f"_{direction}" if t == "w" else ""))


def dm_root(O, Sb, direction):
    t = O.split("-")[1]
    return V2 / "dm" / (f"{t}_{Sb}" + (f"_{direction}" if t == "w" else ""))


def S_for_dm(S, is_ind=False):
    """DataMind 本体所在的变体:BIRD 上 full/nofk/bare 各自构建;工业库未安排 nofk 构建,沿用 full。"""
    return "full" if (S == "nofk" and is_ind) else S


# ---------------------------------------------------------------- 任务构造
class Plan:
    def __init__(self):
        self.jobs, self.ids, self.port = [], set(), PORT0

    def add(self, jid, phase, cmd, *, env=None, deps=(), out=None, check=None, cwd=None, reuse_of=None, note=""):
        if jid in self.ids:
            return jid
        self.ids.add(jid)
        self.jobs.append({"id": jid, "phase": phase, "cmd": cmd, "env": env or {}, "deps": list(deps), "out": str(out) if out else None,
                          "check": check, "cwd": str(cwd or EXP), "reuse_of": str(reuse_of) if reuse_of else None, "note": note})
        return jid

    def next_port(self):
        self.port += 1
        return self.port


def build_plan():
    P = Plan()
    # (0) 变体库存在性检查(由 Fork A 生成,不在本队列生成)
    for S in ("nofk", "bare"):
        dbs = BIRD_DBS + IND_DBS if S == "nofk" else IND_DBS
        files = [str(EXP / "variants" / S / "dev_databases" / db / f"{db}.sqlite") for db in dbs]
        P.add(f"variants_check__{S}", "build", "python3 -c " + json.dumps(
            "import sys,os;m=[f for f in sys.argv[1:] if not os.path.exists(f)];print('missing',m) if m else print('ok');sys.exit(1 if m else 0)")
              + " " + " ".join(f'"{f}"' for f in files), check={"kind": "files", "files": files}, note=f"变体库 {S} 必须已由 make_schema_variants.py 生成")
    # 工业库 D8 需要示例池
    for d in IND_DIRS:
        out = EXP / "runs" / "exemplars" / f"{d}_test"
        P.add(f"exemplars__{d}", "build", f'python3 "{EXP}/make_exemplars.py" {d} construction+validation "{out}"',
              out=out, check={"kind": "files", "files": [str(out / "index.json")]})

    def evo_build_jobs(track, S, direction, dbs):
        root = V2 / "evo" / (f"{track}_{S}" + (f"_{direction}" if track == "w" else ""))
        deps = [f"variants_check__{S}"] if S != "full" else []
        ids = []
        for db in dbs:
            cmd = f'python3 "{EXP}/build_evo_v2.py" {db} --track {track} --S {S}' + (f" --dir {direction}" if track == "w" else "")
            ids.append(P.add(f"evo_build__{root.name}__{db}", "build", cmd, deps=deps, out=root / db,
                             check={"kind": "evo_published", "ws": str(root / db)}))
        return root, ids

    def dm_build_job(track, S, direction, dbs):
        root = V2 / "dm" / (f"{track}_{S}" + (f"_{direction}" if track == "w" else ""))
        deps = [f"variants_check__{S}"] if S != "full" else []
        cmd = (f'python3 "{EXP}/build_dm_v2.py" --track {track} --S {S}' + (f" --dir {direction}" if track == "w" else "")
               + (" --rebuild" if (track, S) == ("d", "full") else "") + f" --port {P.next_port()} " + " ".join(dbs))
        jid = P.add(f"dm_build__{root.name}", "build", cmd, deps=deps, out=root,
                    check={"kind": "graphs", "graphs": str(root / "graphs.json"), "dbs": dbs})
        return root, [jid]

    roots = {}          # (kind, root_name) -> (root, build job ids, dbs, S, dir)
    # (a)(b)(c)(d) BIRD 的 EvoOntology 构建
    for S in ("full", "nofk"):
        for d in BIRD_DIRS:
            r, ids = evo_build_jobs("w", S, d, BIRD_DBS); roots[("evo", r.name)] = (r, ids, BIRD_DBS, S, d)
        r, ids = evo_build_jobs("d", S, "", BIRD_DBS); roots[("evo", r.name)] = (r, ids, BIRD_DBS, S, "")
    # (e) DataMind BIRD:d_full 复用,w_full 两方向
    r, ids = dm_build_job("d", "full", "", BIRD_DBS + IND_DBS); roots[("dm", r.name)] = (r, ids, BIRD_DBS + IND_DBS, "full", "")
    for d in BIRD_DIRS:
        r, ids = dm_build_job("w", "full", d, BIRD_DBS); roots[("dm", r.name)] = (r, ids, BIRD_DBS, "full", d)
    # nofk:新构建代码读取声明外键作为契约证据,DataMind 本体在 full/nofk 下不同,BIRD 上重建
    r, ids = dm_build_job("d", "nofk", "", BIRD_DBS); roots[("dm", r.name)] = (r, ids, BIRD_DBS, "nofk", "")
    for d in BIRD_DIRS:
        r, ids = dm_build_job("w", "nofk", d, BIRD_DBS); roots[("dm", r.name)] = (r, ids, BIRD_DBS, "nofk", d)
    # (f) 工业库
    for S in ("full", "nofk", "bare"):
        for d in IND_DIRS:
            r, ids = evo_build_jobs("w", S, d, IND_DBS); roots[("evo", r.name)] = (r, ids, IND_DBS, S, d)
        r, ids = evo_build_jobs("d", S, "", IND_DBS)
        key = ("evo", r.name)
        if key in roots:                     # d_full / d_nofk 与 BIRD 共用根目录,合并库列表
            r0, ids0, dbs0, S0, d0 = roots[key]; roots[key] = (r0, ids0 + ids, dbs0 + IND_DBS, S0, d0)
        else:
            roots[key] = (r, ids, IND_DBS, S, "")
    for d in IND_DIRS:
        r, ids = dm_build_job("w", "full", d, IND_DBS); roots[("dm", r.name)] = (r, ids, IND_DBS, "full", d)
        r, ids = dm_build_job("w", "bare", d, IND_DBS); roots[("dm", r.name)] = (r, ids, IND_DBS, "bare", d)
    r, ids = dm_build_job("d", "bare", "", IND_DBS); roots[("dm", r.name)] = (r, ids, IND_DBS, "bare", "")
    # 无本体的 DataMind 输入(只有表与列)
    for S in ("full", "bare"):
        out = V2 / "dm" / f"none_{S}"
        deps = [f"variants_check__{S}"] if S != "full" else []
        P.add(f"schema_ir__{S}", "build", CONV_CMDS["schema_ir"](S, out, BIRD_DBS + IND_DBS if S == "full" else IND_DBS), deps=deps, out=out,
              check={"kind": "files", "files": [str(out / "graphs.json")]})
    # (g) 转换与构建质量评测
    conv = {}
    for (kind, name), (root, ids, dbs, S, d) in roots.items():
        if kind == "evo":
            out = V2 / "evo2dm" / name
            conv[("evo2dm", name)] = P.add(f"evo2dm__{name}", "build", CONV_CMDS["evo2dm"](root, out), deps=ids, out=out,
                                           check={"kind": "graphs", "graphs": str(out / "graphs.json"), "dbs": dbs})
            O = "evo-" + name.split("_")[0]
        else:
            out = V2 / "dm2evo" / name
            conv[("dm2evo", name)] = P.add(f"dm2evo__{name}", "build", CONV_CMDS["dm2evo"](root / "graphs.json", out), deps=ids, out=out,
                                           check={"kind": "evo_roots", "root": str(out), "dbs": dbs})
            O = "dm-" + name.split("_")[0]
        cj = V2 / "construction" / (f"{O}__{S}" + (f"_{d}" if d else "") + ".json")
        P.add(f"construction__{kind}__{name}", "build", CONV_CMDS["construction"](root, kind, dbs, cj), deps=ids, out=cj,
              check={"kind": "files", "files": [str(cj)]})

    # ---------------- 问数矩阵
    def unit(direction, O, Q, S, rep):
        sr = SPLITROOT[direction]
        out = V2 / "qa" / direction / f"{O}__{Q}__{S}_r{rep}"
        is_ind = direction.startswith("ind")
        dbs = IND_DBS if is_ind else BIRD_DBS
        nq = test_questions(direction)
        deps, env, reuse = [], {}, None
        legacy = EXP / "runs" / "matrix" / direction
        if Q == "react":
            cfg = "deepseek_baseline" if O == "none" else "deepseek_ontology"
            env["EVO_USE_EVIDENCE"] = "1"
            if S != "full":
                env["EVO_DB_ROOT"] = str(EXP / "variants" / S / "dev_databases"); deps.append(f"variants_check__{S}")
            if O.startswith("evo"):
                root = evo_root(O, S, direction); env["EVO_SEMANTIC_ROOT"] = str(root)
                deps += [j["id"] for j in P.jobs if j["id"].startswith(f"evo_build__{root.name}__") and j["id"].split("__")[-1] in dbs]
            elif O.startswith("dm"):
                root = dm_root(O, S_for_dm(S, is_ind), direction); env["EVO_SEMANTIC_ROOT"] = str(V2 / "dm2evo" / root.name)
                deps.append(f"dm2evo__{root.name}")
            cmd = (f'source "{EXP}/evo_env.sh"; cd "{BIRD}"; exec "{PY_EVO}" run_evaluation.py --config configs/{cfg}.yaml '
                   f'--dataset minidev --split-dir data/{sr}/test --parallel {PARALLEL_REACT} --save-traces --output "{out}"')
            cmd = "bash -c " + json.dumps(cmd, ensure_ascii=False)
            check = {"kind": "react", "out": str(out), "n": sum(len(v) for v in nq.values())}
            # 复用旧单元:无本体 = Ae(工业库无 evidence 字段,A 与 Ae 等价);dm-d 旧转换 = C3e / C3
            if O == "none" and S == "full":
                cand = legacy / (f"A_r{rep}" if is_ind else f"Ae_r{rep}")
                if cand.exists():
                    reuse = cand; check = {"kind": "react", "out": str(cand), "n": sum(len(v) for v in nq.values())}
            if O == "dm-d-legacy" and S == "full":
                cand = legacy / (f"C3_r{rep}" if is_ind else f"C3e_r{rep}")
                if cand.exists(): reuse = cand
                check = {"kind": "react", "out": str(cand), "n": sum(len(v) for v in nq.values())}
        else:
            Sb = S_for_dm(S, is_ind)
            env.update(DATAMIND_EXEMPLARS_DIR=str(EXP / "runs" / "exemplars" / f"{sr}_test"), DM_EVIDENCE="1", DM_SPLITROOT=sr,
                       DM_SPLIT="test", DM_STYLE="exact", DM_CANDIDATES="3", DM_PORT=str(P.next_port()), DM_QA_OUT=str(out),
                       DM_ROOT=str(EXP.parent / "cosmo-datamind-exp"))     # D10 代码消费 concepts/caveats/derived;主仓 D8 忽略它们
            if is_ind:
                deps.append(f"exemplars__{direction}")
            if S == "bare" or (S == "nofk" and O.startswith("dm") and not is_ind):   # 执行库与构建时登记的变体库一致(数据相同)
                env["DM_DBDIR"] = str(EXP / "variants" / S / "dev_databases"); env["DM_SOURCE_TAG"] = S; deps.append(f"variants_check__{S}")
            if O == "none":
                env["DM_GRAPHS"] = str(V2 / "dm" / f"none_{Sb}" / "graphs.json"); deps.append(f"schema_ir__{Sb}")
            elif O.startswith("evo"):
                root = evo_root(O, S, direction); env["DM_GRAPHS"] = str(V2 / "evo2dm" / root.name / "graphs.json"); deps.append(f"evo2dm__{root.name}")
            else:
                root = dm_root(O, Sb, direction); env["DM_GRAPHS"] = str(root / "graphs.json"); deps.append(f"dm_build__{root.name}")
            cmd = f'python3 "{EXP}/bird_datamind.py" qa ' + " ".join(dbs)
            check = {"kind": "dm", "out": str(out), "per_db": {db: len(nq.get(db, [])) for db in dbs}}
            # 旧 D8_r* 在主仓代码上运行,不消费概念层,不复用
        jid = f"qa__{direction}__{O}__{Q}__{S}_r{rep}"
        P.add(jid, "qa", cmd, env=env, deps=sorted(set(deps)), out=out, check=check, reuse_of=reuse)

    def units_for(direction, S):
        is_ind = direction.startswith("ind")
        for rep in REPS:
            for O in ALL_O + (["dm-d-legacy"] if S == "full" else []):
                if S == "full" or (S == "bare" and is_ind) or (S == "nofk" and True):
                    if S == "bare" and not is_ind: continue
                    unit(direction, O, "react", S, rep)
            for O in ALL_O:
                if S == "full" or (S == "bare" and is_ind):
                    unit(direction, O, "dm", S, rep)
                elif S == "nofk" and (O.startswith("evo") or (O.startswith("dm") and not is_ind)):
                    unit(direction, O, "dm", S, rep)

    for S in ("full", "nofk"):
        for d in BIRD_DIRS:
            units_for(d, S)
    for S in ("full", "nofk", "bare"):
        for d in IND_DIRS:
            units_for(d, S)
    return P.jobs


# ---------------------------------------------------------------- 完成性检查
def check_done(job):
    c = job.get("check") or {}
    k = c.get("kind")
    try:
        if k == "files":
            return all(os.path.exists(f) for f in c["files"])
        if k == "evo_published":
            import build_evo_v2
            return bool(build_evo_v2.published(Path(c["ws"])))
        if k == "graphs":
            g = json.loads(Path(c["graphs"]).read_text())
            return all(db in g and g[db].get("graph") for db in c["dbs"])
        if k == "evo_roots":
            return all((Path(c["root"]) / db / "active.json").exists() for db in c["dbs"])
        if k == "react":
            files = sorted(glob.glob(os.path.join(c["out"], "*", "*", "all_results.json")))
            return bool(files) and len(json.loads(Path(files[-1]).read_text())["results"]) >= c["n"]
        if k == "dm":
            for db, n in c["per_db"].items():
                f = Path(c["out"]) / f"{db}.json"
                if not f.exists() or len(json.loads(f.read_text())) < n:
                    return False
            return True
    except (ValueError, OSError, KeyError):
        return False
    return False


# ---------------------------------------------------------------- 状态
_LAST_DISK = {}          # 本进程最近一次与磁盘同步时各任务的记录;save 时只覆盖本进程改动过的任务


def _lock():
    import fcntl
    STATE_F.parent.mkdir(parents=True, exist_ok=True)
    f = open(STATE_F.with_suffix(".lock"), "w")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


def load_state():
    import copy
    with _lock():
        st = json.loads(STATE_F.read_text()) if STATE_F.exists() else {}
    _LAST_DISK.clear(); _LAST_DISK.update(copy.deepcopy(st))
    return st


def save_state(st):
    """多个队列实例可并行(如 --only nofk 与 --phase qa):加锁读磁盘,只把本进程改动过的任务写回,
    其它任务取磁盘上的最新记录并同步回内存,避免互相覆盖。"""
    import copy
    with _lock():
        disk = json.loads(STATE_F.read_text()) if STATE_F.exists() else {}
        for jid, rec in list(st.items()):
            if rec != _LAST_DISK.get(jid):
                disk[jid] = rec                     # 本进程改过 → 写回
            elif jid in disk and disk[jid] != rec:
                st[jid] = disk[jid]                 # 别的实例改过 → 同步到内存
        for jid, rec in disk.items():
            if jid not in st:
                st[jid] = rec
        tmp = STATE_F.with_suffix(".tmp")
        tmp.write_text(json.dumps(disk, ensure_ascii=False, indent=1)); os.replace(tmp, STATE_F)
    _LAST_DISK.clear(); _LAST_DISK.update(copy.deepcopy(disk))


def alive(pid):
    try:
        os.kill(pid, 0); return True
    except OSError:
        return False


def models_from(job):
    out = job.get("reuse_of") or job.get("out")
    if not out: return []
    try:
        files = sorted(glob.glob(os.path.join(out, "*", "*", "all_results.json")))
        if files:
            return [json.loads(Path(files[-1]).read_text()).get("model")]
    except (ValueError, OSError):
        pass
    if "bird_datamind.py" in job["cmd"] or "build_dm_v2" in job["cmd"]:
        for line in (EXP.parent / "cosmo-datamind" / ".env").read_text().splitlines():
            if line.startswith("DATAMIND_LLM_MODEL="):
                return [line.split("=", 1)[1].strip()]
    return []


def write_meta(job, st):
    out = job.get("out")
    if not out or job["phase"] != "qa":
        return
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / "meta.json").write_text(json.dumps({
        "job": job["id"], "command": job["cmd"], "env_names": sorted(job["env"].keys()), "env": job["env"],
        "started": st.get("started"), "finished": st.get("finished"), "exit": st.get("exit"), "status": st.get("status"),
        "models": models_from(job), "reuse_of": job.get("reuse_of")}, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- 运行
def run(args):
    jobs = json.loads(PLAN_F.read_text())
    st = load_state()
    sel = [j for j in jobs if (not args.phase or j["phase"] == args.phase) and (not args.only or args.only in j["id"])]
    # 恢复:上次 running 的任务若进程已不在,按产物判定
    for j in sel:
        s = st.setdefault(j["id"], {"status": "pending"})
        if j.get("reuse_of"):
            s.update(status="reused" if check_done(j) else "reuse_missing"); continue
        if s["status"] == "running" and not alive(int(s.get("pid") or 0)):
            s.update(status="done" if check_done(j) else "incomplete", finished=s.get("finished") or time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        if s["status"] in ("pending", "blocked") and check_done(j):
            s["status"] = "done"
        if args.retry_failed and s["status"] in ("failed", "incomplete", "blocked"):
            s["status"] = "pending"
    save_state(st)
    procs = {}
    def deps_state(j):
        states = [st.get(d, {}).get("status", "pending") for d in j["deps"]]
        if all(x in ("done", "reused") for x in states): return "ready"
        if any(x in ("failed", "incomplete", "blocked", "reuse_missing") for x in states): return "blocked"
        return "wait"
    while True:
        # 回收
        for jid, (proc, j) in list(procs.items()):
            code = proc.poll()
            if code is None: continue
            s = st[jid]
            s.update(exit=code, finished=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
            s["status"] = "done" if check_done(j) else ("failed" if code != 0 else "incomplete")
            write_meta(j, s); save_state(st); procs.pop(jid)
            print(time.strftime("%H:%M:%S"), jid, s["status"], flush=True)
        # 派发
        for j in sel:
            if len(procs) >= args.parallel: break
            s = st[j["id"]]
            if s["status"] != "pending" or j["id"] in procs: continue
            ds = deps_state(j)
            if ds == "blocked":
                s["status"] = "blocked"; save_state(st); continue
            if ds != "ready": continue
            logf = V2 / "logs" / f"{j['id']}.log"; logf.parent.mkdir(parents=True, exist_ok=True)
            env = {**os.environ, **j["env"], "PYTHONUNBUFFERED": "1"}
            proc = subprocess.Popen(j["cmd"], shell=True, cwd=j["cwd"], env=env, stdout=logf.open("a"), stderr=subprocess.STDOUT, start_new_session=True)
            s.update(status="running", pid=proc.pid, started=time.strftime("%Y-%m-%dT%H:%M:%S%z"), log=str(logf))
            procs[j["id"]] = (proc, j); save_state(st)
            print(time.strftime("%H:%M:%S"), "start", j["id"], flush=True)
        if not procs and not any(st[j["id"]]["status"] == "pending" and deps_state(j) == "ready" for j in sel):
            pending_wait = [j["id"] for j in sel if st[j["id"]]["status"] == "pending"]
            print("队列结束;仍待依赖的任务:", len(pending_wait), flush=True)
            break
        time.sleep(20)


def status(args):
    jobs = json.loads(PLAN_F.read_text()) if PLAN_F.exists() else []
    st = load_state()
    rows = {}
    for j in jobs:
        if args.phase and j["phase"] != args.phase: continue
        s = st.get(j["id"], {}).get("status", "pending")
        if j.get("reuse_of") and s == "pending": s = "reused" if check_done(j) else "reuse_missing"
        if s == "running" and not alive(int(st[j["id"]].get("pid") or 0)): s = "done?" if check_done(j) else "dead"
        grp = j["id"].split("__")[0] if j["phase"] == "build" else "qa:" + j["id"].split("__")[1]
        rows.setdefault(grp, {}); rows[grp][s] = rows[grp].get(s, 0) + 1
    keys = ["pending", "running", "done", "reused", "incomplete", "failed", "blocked", "reuse_missing", "dead", "done?"]
    print("| 组 | " + " | ".join(keys) + " |"); print("|---|" + "---|" * len(keys))
    for g, c in rows.items():
        print(f"| {g} | " + " | ".join(str(c.get(k, 0)) or "0" for k in keys) + " |")
    if args.verbose:
        for j in jobs:
            s = st.get(j["id"], {})
            if s.get("status") in ("failed", "incomplete", "blocked", "running"):
                print(j["id"], s.get("status"), s.get("log", ""))


def plan(args):
    jobs = build_plan()
    V2.mkdir(parents=True, exist_ok=True)
    PLAN_F.write_text(json.dumps(jobs, ensure_ascii=False, indent=1))
    by = {}
    for j in jobs:
        g = (j["phase"], j["id"].split("__")[0] if j["phase"] == "build" else "qa:" + j["id"].split("__")[1])
        by.setdefault(g, []).append(j)
    lines = ["# 协议 v2 任务计划", "", f"生成时间 {time.strftime('%Y-%m-%d %H:%M')};任务总数 {len(jobs)}", "",
             "| 阶段 | 组 | 任务数 | 其中复用旧结果 |", "|---|---|---|---|"]
    for (ph, g), js in by.items():
        lines.append(f"| {ph} | {g} | {len(js)} | {sum(1 for j in js if j.get('reuse_of'))} |")
    nq = {d: sum(len(v) for v in test_questions(d).values()) for d in SPLITROOT}
    qa = [j for j in jobs if j["phase"] == "qa" and not j.get("reuse_of")]
    est = sum(nq[j["id"].split("__")[1]] * (3 if "__dm__" in j["id"] else 1) for j in qa)
    lines += ["", f"问数阶段需实际运行 {len(qa)} 个单元,按题数 × 候选数估计约 {est} 次题目规划;构建阶段 Evo 无头构建 "
              f"{sum(1 for j in jobs if j['id'].startswith('evo_build__'))} 个库次。", "",
              "## 任务清单(按优先级)", ""]
    for j in jobs:
        lines.append(f"- `{j['id']}`" + (f" ← 复用 `{Path(j['reuse_of']).relative_to(EXP)}`" if j.get("reuse_of") else "")
                     + (f";依赖 {len(j['deps'])}" if j["deps"] else ""))
    (V2 / "plan.md").write_text("\n".join(lines) + "\n")
    print(f"plan: {len(jobs)} 个任务 → {PLAN_F}")
    for (ph, g), js in by.items():
        print(f"  {ph:5} {g:16} {len(js):4}  reuse={sum(1 for j in js if j.get('reuse_of'))}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["plan", "run", "status"])
    ap.add_argument("--phase", choices=["build", "qa"], default=None)
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    {"plan": plan, "run": run, "status": status}[a.action](a)


if __name__ == "__main__":
    main()
