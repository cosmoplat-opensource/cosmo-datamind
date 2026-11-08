#!/usr/bin/env python3
"""协议 v2 汇总:按 (方向, O, Q, S) 读取 3 次重复,给出均值与范围、两方向合并、逐次配对 McNemar(预登记比较)、本体使用率。

用法: python3 aggregate_v2.py [--legacy] [--out runs/v2]
  --legacy:在 plan.json 之外,再把 runs/matrix 的 Ae/C3e/D8 读成 (none,react,full)/(dm-d-legacy,react,full)/(dm-d-main,dm,full),用于自检。
输出 runs/v2/summary.md 与 summary.json。

读取方式:
  react 单元:<unit>/*/*/all_results.json 的 results[].ex(字符串 "True")与 semantic_tool_calls;
  dm 单元:<unit>/<db>.json 每题 ex_first;本体使用取每题 anchor(对象数 + 关系数 > 0);旧结果无 anchor 时回退到 intent_crosscheck 文本。
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import re
from pathlib import Path

EXP = Path(__file__).resolve().parent
DATA = EXP / "EvoOntology" / "benchmarks" / "bird" / "data"
V2 = EXP / "runs" / "v2"
SPLITROOT = {"fwd": "minidev", "rev": "minidev_rev", "ind_fwd": "ind_fwd", "ind_rev": "ind_rev"}
POOLS = {"BIRD": ("fwd", "rev"), "工业库": ("ind_fwd", "ind_rev")}
REPS = (1, 2, 3)
ANCHOR_RE = re.compile(r"锚定到的\s*(\d+)\s*个")


def test_ids(direction):
    return {str(q["question_id"]) for f in (DATA / SPLITROOT[direction] / "test").glob("*.json")
            for q in json.loads(f.read_text())["questions"]}


def read_react(path):
    files = sorted(glob.glob(str(Path(path) / "*" / "*" / "all_results.json")))
    if not files:
        return {}, {}
    rs = json.loads(Path(files[-1]).read_text())["results"]
    if not isinstance(rs, list) or any(not isinstance(r, dict) or 'question_id' not in r for r in rs):
        raise ValueError(f'Invalid ReAct prediction records in {files[-1]}')
    ids = [str(r['question_id']) for r in rs]
    if len(set(ids)) != len(ids):
        raise ValueError(f'Duplicate formal ReAct question in {files[-1]}')
    if any(str(r.get('ex')) not in ('True', 'False') for r in rs):
        raise ValueError(f'Invalid ReAct execution score in {files[-1]}')
    ex = {str(r["question_id"]): int(str(r.get("ex")) == "True") for r in rs}
    use = {str(r["question_id"]): int(float(r.get("semantic_tool_calls") or 0)) for r in rs}
    return ex, use


def read_dm(path):
    ex, use = {}, {}
    for f in sorted(Path(path).glob("*.json")):
        if f.name in {"meta.json", "repair_log.json"} or f.stem.count(".") or any(x in f.stem for x in ("before_repair", "backup", "aborted")):
            continue
        try:
            rows = json.loads(Path(f).read_text())
        except ValueError:
            continue
        if not isinstance(rows, list):
            continue
        for r in rows:
            if not isinstance(r, dict) or "question_id" not in r:
                raise ValueError(f"Invalid prediction record in {f}")
            qid = str(r["question_id"])
            if qid in ex:
                raise ValueError(f"Duplicate formal question {qid} in {path}")
            score = r.get('ex_first', 0)
            if score not in (0, 1) or not isinstance(score, (bool, int)):
                raise ValueError(f'Nonbinary execution score for question {qid} in {f}')
            ex[qid] = int(score)
            an = r.get("anchor")
            if isinstance(an, dict):                 # 协议 v2:bird_datamind.ask 保存的紧凑锚定视图
                n = int(an.get("objects") or 0) + int(an.get("relations") or 0)
            else:                                    # 旧结果:从 intent_crosscheck 文本回退
                n = 0
                for st in r.get("steps") or []:
                    if st and st[0] == "intent_crosscheck":
                        m = ANCHOR_RE.search(str(st[2]))
                        if m:
                            n = max(n, int(m.group(1)))
            use[qid] = n
    return ex, use


def mcnemar(x, y):
    if set(x) != set(y):
        raise ValueError("Paired test requires identical question sets")
    b = sum(1 for k in x if x[k] and not y.get(k))
    c = sum(1 for k in x if not x[k] and y.get(k))
    n = b + c
    if n == 0:
        return 1.0, b, c
    p = 2 * sum(math.comb(n, i) for i in range(0, min(b, c) + 1)) / 2 ** n
    return min(1.0, p), b, c


def collect(legacy=False):
    """units[(dir, O, Q, S)][rep] = (ex dict, use dict)。"""
    plan = json.loads((V2 / "plan.json").read_text()) if (V2 / "plan.json").exists() else []
    units = {}
    for j in plan:
        if j["phase"] != "qa":
            continue
        _, d, O, Q, tail = j["id"].split("__")
        S, rep = tail.rsplit("_r", 1)
        path = j.get("reuse_of") or j["out"]
        ex, use = (read_react if Q == "react" else read_dm)(path)
        ids = test_ids(d)
        ex = {k: v for k, v in ex.items() if k in ids}
        if len(ex) < len(ids):
            continue
        units.setdefault((d, O, Q, S), {})[int(rep)] = (ex, {k: v for k, v in use.items() if k in ids})
    if legacy:
        for d in ("fwd", "rev"):
            for cond, key in (("Ae", ("none", "react", "full")), ("C3e", ("dm-d-legacy", "react", "full")), ("D8", ("dm-d-main", "dm", "full"))):
                for rep in REPS:
                    p = EXP / "runs" / "matrix" / d / f"{cond}_r{rep}"
                    if not p.exists():
                        continue
                    ex, use = (read_dm if cond == "D8" else read_react)(p)
                    ids = test_ids(d)
                    ex = {k: v for k, v in ex.items() if k in ids}
                    if len(ex) == len(ids):
                        units.setdefault((d,) + key, {})[rep] = (ex, use)
    return units


def pooled(units, dirs, O, Q, S):
    """两方向合并:每次重复把两个方向的题并起来;任一方向缺该次则该次缺。"""
    out = {}
    for rep in REPS:
        parts = [units.get((d, O, Q, S), {}).get(rep) for d in dirs]
        if all(parts):
            ex, use = {}, {}
            for e, u in parts:
                ex.update(e); use.update(u)
            out[rep] = (ex, use)
    return out


def fmt_row(label, reps, n):
    if not reps:
        return f"| {label} | — | | | |"
    vals = [sum(reps[r][0].values()) for r in sorted(reps)]
    mean = sum(vals) / len(vals)
    used = [sum(1 for v in reps[r][1].values() if v > 0) / max(1, len(reps[r][1])) for r in sorted(reps)]
    return (f"| {label} | {' / '.join(map(str, vals))} | {mean:.1f}({100 * mean / n:.1f}%) | "
            f"{min(vals)}–{max(vals)} | {100 * sum(used) / len(used):.0f}% |")


def compare(a, b):
    """逐次配对 McNemar:a 减 b。"""
    cells = []
    for rep in sorted(set(a) & set(b)):
        p, x, y = mcnemar(a[rep][0], b[rep][0])
        cells.append(f"r{rep} {sum(a[rep][0].values()) - sum(b[rep][0].values()):+d} / p={p:.3f}")
    return "; ".join(cells) if cells else "—"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--legacy", action="store_true")
    ap.add_argument("--out", default=str(V2))
    a = ap.parse_args()
    units = collect(a.legacy)
    Os = ["none", "evo-d", "evo-w", "dm-d", "dm-w", "dm-d-legacy", "dm-d-main"]
    lines = ["# 协议 v2 汇总", "", "数值为各次重复答对题数;本体使用率 = 该次重复中至少调用/锚定过一次本体的题目占比。", ""]
    report = {}
    for pool, dirs in POOLS.items():
        n = sum(len(test_ids(d)) for d in dirs)
        for S in ("full", "nofk", "bare"):
            for Q in ("react", "dm"):
                rows = {O: pooled(units, dirs, O, Q, S) for O in Os}
                if not any(rows.values()):
                    continue
                lines += [f"## {pool} · S={S} · Q={Q}(两方向合并 {n} 题)", "",
                          "| O | 各次正确数 | 均值 | 范围 | 本体使用率 |", "|---|---|---|---|---|"]
                for O, reps in rows.items():
                    if reps:
                        lines.append(fmt_row(O, reps, n))
                        report[f"{pool}|{S}|{Q}|{O}"] = {r: sum(reps[r][0].values()) for r in reps}
                lines.append("")
                # 预登记比较 1:构建作用(固定 Q、同轨道)
                for t in ("d", "w"):
                    if rows.get(f"dm-{t}") and rows.get(f"evo-{t}"):
                        lines.append(f"- 构建作用 dm-{t} − evo-{t}:{compare(rows[f'dm-{t}'], rows[f'evo-{t}'])}")
                for O in ("evo-d", "evo-w", "dm-d", "dm-w"):
                    if rows.get(O) and rows.get("none"):
                        lines.append(f"- 本体作用 {O} − none:{compare(rows[O], rows['none'])}")
                lines.append("")
            # 预登记比较 2:消费作用(固定 O)
            for O in Os:
                A, B = pooled(units, dirs, O, "dm", S), pooled(units, dirs, O, "react", S)
                if A and B:
                    lines.append(f"- {pool} S={S} 消费作用 {O}:dm − react:{compare(A, B)}")
            # 预登记比较 3:原生完整系统
            A, B = pooled(units, dirs, "dm-w", "dm", S), pooled(units, dirs, "evo-w", "react", S)
            if A and B:
                lines.append(f"- {pool} S={S} 完整系统 (dm-w,dm) − (evo-w,react):{compare(A, B)}")
            lines.append("")
        # 预登记比较 4:语义剥离的交互(Q=react)
        for t in ("d", "w"):
            f_dm, f_ev = pooled(units, dirs, f"dm-{t}", "react", "full"), pooled(units, dirs, f"evo-{t}", "react", "full")
            n_dm, n_ev = pooled(units, dirs, f"dm-{t}", "react", "nofk"), pooled(units, dirs, f"evo-{t}", "react", "nofk")
            if f_dm and f_ev and n_dm and n_ev:
                reps = sorted(set(f_dm) & set(f_ev) & set(n_dm) & set(n_ev))
                diffs = [(sum(n_dm[r][0].values()) - sum(n_ev[r][0].values())) - (sum(f_dm[r][0].values()) - sum(f_ev[r][0].values())) for r in reps]
                lines.append(f"- {pool} 交互(轨道 {t}):(dm−evo)@nofk − (dm−evo)@full 各次 = {diffs}")
        lines.append("")
    Path(a.out, "summary.md").write_text("\n".join(lines) + "\n")
    Path(a.out, "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
