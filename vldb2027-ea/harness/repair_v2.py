#!/usr/bin/env python3
"""协议 v2:只补跑问数单元里因基础设施失败的题,并把结果合并回原文件(仿 repair_conn.py)。

失败判定(预先登记,不扩大):
- Q=dm 单元(<unit>/<db>.json,bird_datamind.ask 的记录):有 error(请求异常),或 steps 里有 plan_timeout,
  或名字以 llm_plan 开头且 ok 为 False 的步骤(引擎 30 秒超时降级为内置模板)。
- Q=react 单元(<unit>/*/*/all_results.json,取最新一份):error 含 "LLM request failed" 或 "Connection error";
  "Timeout after 180s" 只在该单元此类记录 ≥ 5 条时算基础设施失败(单独一条是智能体在难题上超时,保持原样)。

机制:读 <unit>/meta.json 的 env 原样复用,只换端口(≥ 8220)、只含失败题的临时划分、输出目录 <unit>/repair_<ts>/;
合并时只替换失败题的记录,原文件首次备份为 *.before_repair.json,日志追加到 <unit>/repair_log.json。重跑一次,再失败也保留重跑记录。
只处理 meta.status == done 的单元;逐个单元顺序执行。

用法:
  python3 repair_v2.py --list [--dirs fwd rev ind_fwd ind_rev]
  python3 repair_v2.py --run [--dirs ...] [--only 子串] [--port 8220]
"""
from __future__ import annotations

import argparse
import glob
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
PY_EVO = EXP / ".venv-evo" / "bin" / "python"
V2 = EXP / "runs" / "v2"
SPLITROOT = {"fwd": "minidev", "rev": "minidev_rev", "ind_fwd": "ind_fwd", "ind_rev": "ind_rev"}
REACT_INFRA = ("LLM request failed", "Connection error")
TIMEOUT_TXT = "Timeout after 180s"
TIMEOUT_MIN = 5


def now():
    return time.strftime("%Y%m%d_%H%M%S")


def meta_of(unit: Path):
    f = unit / "meta.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except ValueError:
        return None


# ---------------------------------------------------------------- 失败判定
def dm_failed(rec):
    if rec.get("error"):
        return True
    for s in rec.get("steps") or []:
        if not isinstance(s, (list, tuple)) or not s:
            continue
        name, ok = str(s[0]), (s[1] if len(s) > 1 else None)
        if name == "plan_timeout" or (name.startswith("llm_plan") and ok is False):
            return True
    return False


def dm_failures(unit: Path):
    """{db: [question_id, ...]}"""
    out = {}
    for f in sorted(unit.glob("*.json")):
        if f.name in ("meta.json", "repair_log.json") or f.name.endswith(".before_repair.json"):
            continue
        try:
            recs = json.loads(f.read_text())
        except ValueError:
            continue
        if not isinstance(recs, list):
            continue
        ids = [r["question_id"] for r in recs if isinstance(r, dict) and dm_failed(r)]
        if ids:
            out[f.stem] = ids
    return out


def react_results_file(unit: Path):
    files = sorted(glob.glob(str(unit / "*" / "*" / "all_results.json")))
    files = [f for f in files if "/repair_" not in f]
    return Path(files[-1]) if files else None


def react_failures(unit: Path):
    f = react_results_file(unit)
    if not f:
        return {}, None
    rs = json.loads(f.read_text())["results"]
    n_timeout = sum(1 for r in rs if TIMEOUT_TXT in str(r.get("error") or ""))
    out = {}
    for r in rs:
        e = str(r.get("error") or "")
        infra = any(k in e for k in REACT_INFRA) or (TIMEOUT_TXT in e and n_timeout >= TIMEOUT_MIN)
        if infra:
            out.setdefault(r["db_id"], []).append(r["question_id"])
    return out, f


def unit_kind(unit: Path):
    return "dm" if "__dm__" in unit.name else "react"


def scan(dirs, only=""):
    rows = []
    for d in dirs:
        for unit in sorted((V2 / "qa" / d).glob("*")):
            if not unit.is_dir() or (only and only not in unit.name):
                continue
            m = meta_of(unit)
            status = (m or {}).get("status")
            if unit_kind(unit) == "dm":
                fails = dm_failures(unit)
            else:
                fails, _ = react_failures(unit)
            n = sum(len(v) for v in fails.values())
            rows.append({"dir": d, "unit": unit.name, "kind": unit_kind(unit), "status": status, "n_failed": n, "fails": fails, "path": unit})
    return rows


# ---------------------------------------------------------------- 补跑
def split_questions(d, db, ids):
    src = json.loads((DATA / SPLITROOT[d] / "test" / f"{db}.json").read_text())
    want = {str(i) for i in ids}
    qs = [q for q in src["questions"] if str(q["question_id"]) in want]
    return {"db_id": src["db_id"], "questions": qs}


def repair_dm(row, port):
    unit, d, m = row["path"], row["dir"], meta_of(row["path"])
    ts = now()
    rdir = unit / f"repair_{ts}"
    rdir.mkdir(parents=True, exist_ok=True)
    tmp_root = f"repair_tmp_{d}_{unit.name}_{ts}"
    tmp_split = DATA / tmp_root / "test"
    tmp_split.mkdir(parents=True, exist_ok=True)
    try:
        for db, ids in row["fails"].items():
            (tmp_split / f"{db}.json").write_text(json.dumps(split_questions(d, db, ids), ensure_ascii=False))
        env = dict(os.environ)
        env.update({k: v for k, v in (m.get("env") or {}).items()})
        env.update(DM_SPLITROOT=tmp_root, DM_SPLIT="test", DM_PORT=str(port), DM_QA_OUT=str(rdir))
        env.pop("DM_LIMIT", None)
        cmd = [sys.executable, str(EXP / "bird_datamind.py"), "qa", *sorted(row["fails"])]
        with (rdir / "run.log").open("w") as log:
            code = subprocess.run(cmd, cwd=EXP, env=env, stdout=log, stderr=subprocess.STDOUT).returncode
        merged, still = 0, 0
        for db, ids in row["fails"].items():
            new_f = rdir / f"{db}.json"
            if not new_f.exists():
                still += len(ids); continue
            new = {str(r["question_id"]): r for r in json.loads(new_f.read_text())}
            orig_f = unit / f"{db}.json"
            bak = unit / f"{db}.before_repair.json"
            if not bak.exists():
                shutil.copy2(orig_f, bak)
            recs = json.loads(orig_f.read_text())
            for i, r in enumerate(recs):
                qid = str(r.get("question_id"))
                if qid in new and str(r["question_id"]) in {str(x) for x in ids}:
                    nr = dict(new[qid]); nr["repaired_at"] = ts
                    recs[i] = nr; merged += 1
                    if dm_failed(nr):
                        still += 1
            still += len([i for i in ids if str(i) not in new])
            orig_f.write_text(json.dumps(recs, ensure_ascii=False, indent=1))
        return {"ts": ts, "exit": code, "n_failed": row["n_failed"], "n_rerun": merged, "n_still_failed": still,
                "ids": {db: [str(i) for i in ids] for db, ids in row["fails"].items()}}
    finally:
        shutil.rmtree(DATA / tmp_root, ignore_errors=True)


def repair_react(row):
    unit, d, m = row["path"], row["dir"], meta_of(row["path"])
    ts = now()
    rdir = unit / f"repair_{ts}"
    split_dir = rdir / "split" / "test"
    split_dir.mkdir(parents=True, exist_ok=True)
    for db, ids in row["fails"].items():
        (split_dir / f"{db}.json").write_text(json.dumps(split_questions(d, db, ids), ensure_ascii=False))
    cfg = "configs/deepseek_baseline.yaml" if "deepseek_baseline" in m.get("command", "") else "configs/deepseek_ontology.yaml"
    env = dict(os.environ)
    env.update({k: v for k, v in (m.get("env") or {}).items()})
    out_dir = rdir / "out"
    inner = (f'source "{EXP}/evo_env.sh"; cd "{BIRD}"; exec "{PY_EVO}" run_evaluation.py --config {cfg} --dataset minidev '
             f'--split-dir "{split_dir}" --parallel 5 --save-traces --output "{out_dir}"')
    with (rdir / "run.log").open("w") as log:
        code = subprocess.run(["bash", "-c", inner], env=env, stdout=log, stderr=subprocess.STDOUT).returncode
    new_files = sorted(glob.glob(str(out_dir / "*" / "*" / "all_results.json")))
    _, orig_f = react_failures(unit)
    merged, still = 0, 0
    want = {str(i) for ids in row["fails"].values() for i in ids}
    if new_files and orig_f:
        new = {str(r["question_id"]): r for r in json.loads(Path(new_files[-1]).read_text())["results"]}
        bak = orig_f.with_name("all_results.before_repair.json")
        if not bak.exists():
            shutil.copy2(orig_f, bak)
        data = json.loads(orig_f.read_text())
        for i, r in enumerate(data["results"]):
            qid = str(r.get("question_id"))
            if qid in want and qid in new:
                nr = dict(new[qid]); nr["repaired_at"] = ts
                data["results"][i] = nr; merged += 1
                if any(k in str(nr.get("error") or "") for k in REACT_INFRA) or TIMEOUT_TXT in str(nr.get("error") or ""):
                    still += 1
        still += len([q for q in want if q not in new])
        orig_f.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        still = len(want)
    return {"ts": ts, "exit": code, "n_failed": row["n_failed"], "n_rerun": merged, "n_still_failed": still,
            "ids": {db: [str(i) for i in ids] for db, ids in row["fails"].items()}}


def ex_of(row):
    unit = row["path"]
    if row["kind"] == "dm":
        ok = tot = 0
        for f in unit.glob("*.json"):
            if f.name in ("meta.json", "repair_log.json") or f.name.endswith(".before_repair.json"):
                continue
            try:
                recs = json.loads(f.read_text())
            except ValueError:
                continue
            if isinstance(recs, list):
                tot += len(recs); ok += sum(int(r.get("ex_first", 0)) for r in recs)
        return ok, tot
    f = react_results_file(unit)
    if not f:
        return 0, 0
    rs = json.loads(f.read_text())["results"]
    return sum(1 for r in rs if str(r.get("ex")) == "True"), len(rs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--dirs", nargs="*", default=["fwd", "rev", "ind_fwd", "ind_rev"])
    ap.add_argument("--only", default="")
    ap.add_argument("--port", type=int, default=8220)
    a = ap.parse_args()
    rows = scan(a.dirs, a.only)
    if a.list or not a.run:
        print("| dir | unit | kind | status | infra-failed |")
        print("|---|---|---|---|---:|")
        for r in rows:
            if r["n_failed"]:
                print(f"| {r['dir']} | {r['unit']} | {r['kind']} | {r['status']} | {r['n_failed']} |")
        print("total affected units", sum(1 for r in rows if r["n_failed"]), "questions", sum(r["n_failed"] for r in rows))
        if not a.run:
            return
    port = a.port
    order = [r for r in rows if r["n_failed"] and r["status"] == "done" and r["kind"] == "dm"] + \
            [r for r in rows if r["n_failed"] and r["status"] == "done" and r["kind"] == "react"]
    skipped = [r["unit"] for r in rows if r["n_failed"] and r["status"] != "done"]
    if skipped:
        print("skip (not done):", skipped, flush=True)
    for r in order:
        before = ex_of(r)
        print(time.strftime("%H:%M:%S"), "repair", r["dir"], r["unit"], r["n_failed"], flush=True)
        rec = repair_dm(r, port) if r["kind"] == "dm" else repair_react(r)
        port += 1
        after = ex_of(r)
        rec.update(ex_before=before, ex_after=after, dir=r["dir"], unit=r["unit"])
        logf = r["path"] / "repair_log.json"
        log = json.loads(logf.read_text()) if logf.exists() else []
        log.append(rec); logf.write_text(json.dumps(log, ensure_ascii=False, indent=1))
        print(time.strftime("%H:%M:%S"), "done", r["unit"], json.dumps({k: rec[k] for k in ("n_failed", "n_rerun", "n_still_failed", "ex_before", "ex_after", "exit")}), flush=True)


if __name__ == "__main__":
    main()
