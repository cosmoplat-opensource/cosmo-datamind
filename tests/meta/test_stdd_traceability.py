# -*- coding: utf-8 -*-
"""STDD:规格 ↔ 场景 ↔ 测试 ↔ 模型 ↔ 观测的双向追溯(DR-066)。

specs/acceptance.json 是需求的唯一真相来源。本测试保证:
- 每个行为场景(specs/features)都挂在至少一条需求上,标签指向的需求存在;
- 需求引用的场景存在,且该场景确实带着这条需求的标签(双向);
- 本迭代(IR-016)起的新需求必须同时有场景或模型、有测试,并声明限界上下文与决策记录;
- 需求声明的观测指标在代码里真的会被记录,声明的模型文件存在。
"""
import json
import re
from pathlib import Path

from tests.bdd import gherkin

ROOT = Path(__file__).resolve().parents[2]
SPEC = json.loads((ROOT / "specs" / "acceptance.json").read_text(encoding="utf-8"))
CONTEXTS = json.loads((ROOT / "specs" / "models" / "context_map.json").read_text(encoding="utf-8"))["contexts"]
REQS = {r["id"]: r for r in SPEC["requirements"]}
SCENARIOS = {f"specs/features/{f.name}::{s.name}": s
             for f in sorted((ROOT / "specs" / "features").glob("*.feature")) for s in gherkin.parse(f)}


def _tagged(scenario):
    return {t[len("@REQ-"):] for t in scenario.tags if t.startswith("@REQ-")}


def test_every_scenario_is_traced_to_an_existing_requirement():
    for ref, sc in SCENARIOS.items():
        tags = _tagged(sc)
        assert tags, f"场景没有 @REQ 标签:{ref}"
        assert tags <= set(REQS), f"{ref} 标签指向不存在的需求:{tags - set(REQS)}"


def test_requirement_scenarios_exist_and_carry_the_requirement_tag():
    for rid, r in REQS.items():
        for ref in r.get("scenarios", []):
            assert ref in SCENARIOS, f"{rid} 引用的场景不存在:{ref}"
            assert rid in _tagged(SCENARIOS[ref]), f"{ref} 没有带 @REQ-{rid} 标签"


def test_tagged_scenarios_are_listed_by_their_requirement():
    for ref, sc in SCENARIOS.items():
        for rid in _tagged(sc):
            assert ref in REQS[rid].get("scenarios", []), f"{rid} 的 scenarios 漏了 {ref}"


def test_new_requirements_follow_the_stdd_contract():
    for rid, r in REQS.items():
        if r.get("iteration") != "IR-016":
            continue
        assert r["tests"], rid
        assert r.get("scenarios") or r.get("models"), f"{rid} 既没有行为场景也没有模型"
        assert r.get("context") in CONTEXTS, f"{rid} 的限界上下文未登记:{r.get('context')}"
        for dr in r.get("decisions", []):
            assert list((ROOT / "specs" / "decisions").glob(f"{dr}-*.md")), f"{rid} 引用的决策记录不存在:{dr}"
        for model in r.get("models", []):
            assert (ROOT / model).exists(), model


def test_declared_metrics_are_actually_recorded():
    src = (ROOT / "server.py").read_text(encoding="utf-8") + (ROOT / "observability.py").read_text(encoding="utf-8")
    generated = set(re.findall(r'count\(f"\{self\.kind\}\.(\w+)"', src)) | set(re.findall(r'observe\(f"\{self\.kind\}\.(\w+)"', src))
    generated.add("runs")
    for rid, r in REQS.items():
        for m in r.get("metrics", []):
            kind, _, name = m.partition(".")
            literal = f'"{m}"' in src
            assert literal or (kind in ("build", "qa") and name in generated), f"{rid} 声明的指标没有被记录:{m}"
