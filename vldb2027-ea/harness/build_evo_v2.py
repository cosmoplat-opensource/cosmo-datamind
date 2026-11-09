#!/usr/bin/env python3
"""协议 v2:用 Claude Code(DeepSeek 后端)无头运行 EvoOntology 的 build-ontology 技能,为一个库构建并发布 ontology_v0。

用法: python3 build_evo_v2.py <db> --track w|d --S full|nofk|bare [--dir fwd|rev|ind_fwd|ind_rev] [--force] [--max-turns N]

- track w:数据 + 构建集问题(含 evidence,不含答案),只可读 BIRD/data/<splitroot>/construction_workload/<db>.json;
- track d:只读数据,原生 rolling_trajectory cold-start(与 Spider 批相同);
- S:库文件取 v2_conventions.md 的变体根(full 用原库);
- 输出根 runs/v2/evo/<track>_<S>[_<dir>]/<db>/,日志 runs/v2/build_logs/evo-<track>__<S>[_<dir>]/<db>.{jsonl,err,prompt.json,meta.json};
- 已发布(active.json + versions/ontology_v0/五类记录)则跳过;基础设施类失败最多重试 3 次,其余失败如实记录。
密钥只经 evo_env_ds.sh 从 cosmo-datamind/.env 读取,不进入任何产物。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

EXP = Path(__file__).resolve().parent
BIRD = EXP / "EvoOntology" / "benchmarks" / "bird"
ORIG_DBDIR = BIRD / "data" / "mini_dev_data" / "dev_databases"
PLUGIN = EXP / "EvoOntology" / "plugins" / "claude-code"
PYTHON = EXP / ".venv-evo" / "bin" / "python"
SPLITROOT = {"fwd": "minidev", "rev": "minidev_rev", "ind_fwd": "ind_fwd", "ind_rev": "ind_rev"}
RECORDS = ("terms.json", "mappings.json", "relations.json", "constraints.json", "evidence.json")
INFRA = ("apiconnectionerror", "apitimeouterror", "connectionerror", "connection error", "ratelimiterror",
         "nodename", "name resolution", "no internet route", "response stopped arriving", "session limit",
         "rate limit", "econnreset", "etimedout", "socket hang up", "overloaded", "529", "502", "503", "504")
ALLOWED = ["Read", "Glob", "Grep", "Write", "Edit", "Bash(python3:*)", "Bash(sqlite3:*)", "Bash(ls:*)",
           "Bash(cat:*)", "Bash(head:*)", "Bash(wc:*)", "mcp__plugin_evoontology_evo-semantic"]


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def root_name(track, S, direction):
    return f"{track}_{S}" + (f"_{direction}" if track == "w" else "")


def db_dir(S):
    return ORIG_DBDIR if S == "full" else EXP / "variants" / S / "dev_databases"


def _normalize_ws(ws):
    """智能体有时把工作区放在 <ws>/.evoontology/ 下:把内容上提到 <ws>,符合 <root>/<db>/active.json 的布局。"""
    inner = ws / ".evoontology"
    if (inner / "active.json").exists() and not (ws / "active.json").exists():
        import shutil
        for child in list(inner.iterdir()):
            dest = ws / child.name
            if not dest.exists():
                shutil.move(str(child), str(dest))
        try:
            inner.rmdir()
        except OSError:
            pass


def published(ws):
    _normalize_ws(ws)
    vdir = ws / "versions" / "ontology_v0"
    if not (ws / "active.json").exists() or not vdir.is_dir():
        return None
    counts = {}
    for name in RECORDS:
        f = vdir / name
        if not f.exists():
            return None
        try:
            counts[name[:-5]] = len(json.loads(f.read_text()))
        except ValueError:
            return None
    return counts


def audit(jsonl):
    """从 stream-json 日志核对模型名、费用、轮数;返回摘要与错误文本。"""
    models, results, errors = set(), [], []
    if not jsonl.exists():
        return {"models_used": [], "sessions": []}, ""
    for line in jsonl.read_text(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "system" and ev.get("model"):
            models.add(ev["model"])
        if ev.get("type") == "result":
            models.update((ev.get("modelUsage") or {}).keys())
            results.append({k: ev.get(k) for k in ("is_error", "total_cost_usd", "duration_ms", "num_turns", "modelUsage", "subtype")})
            if ev.get("is_error"):
                errors.append(str(ev.get("result") or ev.get("error") or "")[:500])
    return {"models_used": sorted(models), "sessions": results}, "\n".join(errors)


def prompt_for(db, track, S, direction, ws, dbpath):
    if track == "w":
        sr = SPLITROOT[direction]
        wl = (EXP / "variants" / "opaque_workload" / sr / f"{db}.json") if S == "opaque" \
            else (EXP / "variants" / S / "workload" / sr / f"{db}.json") if S in ("opaque_merged", "readable_merged") \
            else BIRD / "data" / sr / "construction_workload" / f"{db}.json"
        return wl, f"""使用 evoontology 插件的 build-ontology skill,为数据库 {db} 构建初始本体层 ontology_v0。
- 项目模式:Fixed-Split Mode;划分已冻结,不要重新划分。
- 数据源:只读 SQLite 文件 {dbpath}(可用 sqlite3 命令行只读查询;不得修改数据库)。
- 构建工作负载(只可读这一个题目文件,含问题与 evidence,不含答案):{wl}
- 除上面的库文件与构建工作负载文件外,不得读取 {BIRD / 'data'} 下的任何其他文件或目录(包括各划分的 validation、test、construction 目录与标准答案),也不得读取本项目其他本体、历史结果或评测输出。
- 不得读取 {EXP / "variants"} 下除上述库文件与工作负载文件以外的任何文件(包括 mapping、questions、dictionaries 目录),不得读取 {EXP / "runs"} 下的任何文件。
- workspace 绝对路径:{ws}(通过 evo-semantic MCP 工具发布,workspace 参数用该路径)。
- Evaluator:BIRD 执行准确率比较器 {BIRD / 'run_evaluation.py'}(本次只构建,不运行评测)。
- 这是无人值守的批量实验:不要提问,按 skill 默认值直接完成构建与发布;open_browser:false。"""
    return None, f"""使用 evoontology 插件的 build-ontology skill 构建并发布数据库 {db} 的 ontology_v0。
已授权的无人值守 data-only 原生构建实验,不要提问,不运行评测或演化。
只可读取 {dbpath} 的模式和代表性数据;用 SQLite 只读连接,不得修改源库。
本实验不提供 benchmark 工作负载。使用 rolling_trajectory cold-start 模式;只从模式、取值等证据提出可核验的分析问题,保留 synthetic provenance,不导入任何历史。
workspace:{ws}。数据名用 {db};表名和列名沿用 SQLite 实际拼写。
禁止读取本项目任何题目、标准 SQL、答案、评分、其他本体、历史结果或测试数据。写入仅限 workspace,不能修改共享技能或评测代码。
按原 build-ontology 技能完成 configure_ontology_project、数据探查、save_version、annotate_ontology_version、publish_ontology_build;open_browser:false。"""


def run(db, track, S, direction, force=False, max_turns=180, timeout=3 * 3600):
    if track == "w" and direction not in SPLITROOT:
        raise SystemExit("track w 需要 --dir fwd|rev|ind_fwd|ind_rev")
    name = root_name(track, S, direction)
    ws = EXP / "runs" / "v2" / "evo" / name / db
    logs = EXP / "runs" / "v2" / "build_logs" / (f"evo-{track}__{S}" + (f"_{direction}" if track == "w" else ""))
    logs.mkdir(parents=True, exist_ok=True)
    meta_f = logs / f"{db}.meta.json"
    dbpath = db_dir(S) / db / f"{db}.sqlite"
    if not dbpath.exists():
        raise SystemExit(f"库文件不存在: {dbpath}")
    if not force and published(ws):
        print(json.dumps({"db": db, "root": name, "status": "skip (published)", "counts": published(ws)}, ensure_ascii=False))
        return 0
    ws.mkdir(parents=True, exist_ok=True)
    wl, prompt = prompt_for(db, track, S, direction, ws, dbpath)
    if wl is not None and not wl.exists():
        raise SystemExit(f"构建工作负载不存在: {wl}")
    add_dirs = [str(dbpath.parent)] + ([str(wl.parent)] if wl else [])
    (logs / f"{db}.prompt.json").write_text(json.dumps({"prompt": prompt, "track": track, "S": S, "dir": direction,
                                                        "add_dirs": add_dirs, "max_turns": max_turns}, ensure_ascii=False, indent=1))
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)          # 让 evo_env_ds.sh 的 ANTHROPIC_AUTH_TOKEN 生效
    env.update(BROWSER="true", EVO_ONTOLOGY_PYTHON=str(PYTHON), PYTHONUNBUFFERED="1")
    cmd = ["bash", "-c",
           'source "$1"; shift; exec claude -p "$1" --model deepseek-flash --plugin-dir "$2" --allowedTools ' +
           " ".join(f'"{a}"' for a in ALLOWED) +
           ' --max-turns "$3" --output-format stream-json --verbose ' + " ".join(f'--add-dir "{d}"' for d in add_dirs),
           "evo-build-v2", str(EXP / "evo_env_ds.sh"), prompt, str(PLUGIN), str(max_turns)]
    meta = {"db": db, "track": track, "S": S, "dir": direction, "root": str(ws), "db_path": str(dbpath),
            "db_sha256": sha256(dbpath), "workload": str(wl) if wl else None,
            "workload_questions": (len(json.loads(wl.read_text())["questions"]) if wl else 0), "attempts": [], "started": now()}
    status = "failed"
    for attempt in range(1, 4):
        jsonl, err = logs / f"{db}.attempt{attempt}.jsonl", logs / f"{db}.attempt{attempt}.err"
        t0 = time.time()
        with jsonl.open("w") as out, err.open("w") as e:
            proc = subprocess.Popen(cmd, cwd=ws, env=env, stdout=out, stderr=e, start_new_session=True)
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                code = -1
        summary, errtext = audit(jsonl)
        rec = {"attempt": attempt, "exit": code, "seconds": round(time.time() - t0, 1), **summary,
               "error": (errtext or err.read_text(errors="replace")[-800:])[:800]}
        meta["attempts"].append(rec)
        counts = published(ws)
        if counts:
            status = "published"
            meta["counts"] = counts
            break
        blob = (errtext + " " + err.read_text(errors="replace")).lower()
        if code == -1:
            status = "timeout"
        if not any(w in blob for w in INFRA):
            status = "failed (non-infrastructure, not retried)"
            break
        status = "failed (infrastructure)"
        time.sleep(30)
    meta.update(status=status, finished=now())
    meta_f.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    # 日志别名:最后一次尝试另存为 <db>.jsonl / <db>.err,便于查看
    last = len(meta["attempts"])
    for ext in ("jsonl", "err"):
        src = logs / f"{db}.attempt{last}.{ext}"
        if src.exists():
            (logs / f"{db}.{ext}").write_bytes(src.read_bytes())
    print(json.dumps({"db": db, "root": name, "status": status, "counts": meta.get("counts"),
                      "models": meta["attempts"][-1].get("models_used") if meta["attempts"] else None,
                      "seconds": sum(a["seconds"] for a in meta["attempts"])}, ensure_ascii=False))
    return 0 if status == "published" else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--track", required=True, choices=["w", "d"])
    ap.add_argument("--S", required=True, choices=["full", "nofk", "bare", "opaque", "opaque_merged", "readable_merged"])
    ap.add_argument("--dir", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--max-turns", type=int, default=180)
    ap.add_argument("--timeout", type=int, default=3 * 3600)
    a = ap.parse_args()
    sys.exit(run(a.db, a.track, a.S, a.dir, a.force, a.max_turns, a.timeout))


if __name__ == "__main__":
    main()
