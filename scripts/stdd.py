#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STDD 工作流(规格 → 场景 → 测试 → 代码 → 观测),供人和 AI 编程助手共用(DR-066)。

  python3 scripts/stdd.py status [--strict]
      列出每条需求的场景、测试、模型、指标;--strict 时有缺口即返回非零(CI 关卡)。

  python3 scripts/stdd.py new REQ-ID "验收标准" --context <上下文> --feature <功能文件名> --decision DR-xxx
      先写规格:在 specs/acceptance.json 登记需求,并在 specs/features/<功能>.feature 追加一个
      带 @REQ-ID 标签的待实现场景。场景的步骤还没有定义,运行 pytest 时天然是红的——
      接着补步骤与单测(红),再写代码让它们变绿,最后在需求里补上 tests 与 metrics。

只用标准库。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "specs" / "acceptance.json"
FEATURES = ROOT / "specs" / "features"
CONTEXTS = ROOT / "specs" / "models" / "context_map.json"
CURRENT_ITERATION = "IR-016"


def _load():
    return json.loads(SPEC.read_text(encoding="utf-8"))


def _scenario_tags():
    """{场景引用: 标签集合},只用正则读取,不依赖测试包。"""
    out = {}
    for f in sorted(FEATURES.glob("*.feature")):
        tags = []
        for line in f.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("@"):
                tags += [t[len("@REQ-"):] for t in s.split() if t.startswith("@REQ-")]
            elif re.match(r"场景[::]", s):
                out[f"specs/features/{f.name}::{re.split(r'[::]', s, maxsplit=1)[1].strip()}"] = set(tags)
                tags = []
    return out


def status(strict=False):
    spec, scen = _load(), _scenario_tags()
    gaps = []
    print(f"{'需求':8s} {'上下文':16s} 场景 测试 模型 指标  验收标准")
    for r in spec["requirements"]:
        rid = r["id"]
        n_s, n_t = len(r.get("scenarios", [])), len(r.get("tests", []))
        n_m, n_o = len(r.get("models", [])), len(r.get("metrics", []))
        print(f"{rid:8s} {r.get('context', '-'):16s} {n_s:^4d} {n_t:^4d} {n_m:^4d} {n_o:^4d}  {r['criterion'][:40]}")
        if not n_t:
            gaps.append(f"{rid}:没有测试")
        if r.get("iteration") == CURRENT_ITERATION and not (n_s or n_m):
            gaps.append(f"{rid}:本迭代的新需求既没有行为场景也没有模型")
        for ref in r.get("scenarios", []):
            if ref not in scen:
                gaps.append(f"{rid}:场景不存在 {ref}")
    for ref, tags in scen.items():
        if not tags:
            gaps.append(f"场景没有 @REQ 标签:{ref}")
    print(f"\n需求 {len(spec['requirements'])} 条 · 场景 {len(scen)} 个 · 缺口 {len(gaps)} 处")
    for g in gaps:
        print("  -", g)
    return 1 if strict and gaps else 0


def new(rid, criterion, context, feature, decision):
    spec = _load()
    contexts = json.loads(CONTEXTS.read_text(encoding="utf-8"))["contexts"]
    if context not in contexts:
        sys.exit(f"未知的限界上下文 {context};可选:{', '.join(contexts)}")
    if any(r["id"] == rid for r in spec["requirements"]):
        sys.exit(f"需求 {rid} 已存在")
    name = f"{criterion}(待实现)"
    ref = f"specs/features/{feature}.feature::{name}"
    spec["requirements"].append({"id": rid, "context": context, "decisions": [decision] if decision else [],
                                 "criterion": criterion, "scenarios": [ref], "tests": [],
                                 "iteration": CURRENT_ITERATION})
    SPEC.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path = FEATURES / f"{feature}.feature"
    head = "" if path.exists() else f"# 限界上下文:{context}\n功能: {feature}\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{head}\n  @REQ-{rid}\n  场景: {name}\n    假如 <前置条件>\n    当 <动作>\n    那么 <可观察的结果>\n")
    print(f"已登记 {rid},场景写入 {path.relative_to(ROOT)}。下一步:\n"
          f"  1. 把场景里的 <…> 换成具体业务语言,运行 pytest tests/bdd(红:步骤未定义)\n"
          f"  2. 在 tests/bdd/steps.py 补步骤、在 tests/unit 写单测(仍是红)\n"
          f"  3. 写代码让它们变绿;把测试写进 acceptance.json 的 tests,需要观测的补 metrics\n"
          f"  4. python3 scripts/stdd.py status --strict")
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("--strict", action="store_true")
    n = sub.add_parser("new")
    n.add_argument("rid")
    n.add_argument("criterion")
    n.add_argument("--context", required=True)
    n.add_argument("--feature", required=True)
    n.add_argument("--decision", default="")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        return status(a.strict)
    return new(a.rid, a.criterion, a.context, a.feature, a.decision)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
