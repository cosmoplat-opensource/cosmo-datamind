# -*- coding: utf-8 -*-
"""模型与代码一致性(DR-066)。

- 关系生命周期模型自洽;验收与问数从模型取有效行规则,复核技能脚本自带的规则名与模型一致;
  复核的每次升降级都是模型允许的迁移;
- 构建代码推送的 SSE 步骤与流水线说明(docs/pipelines/ontology_build.yaml)登记的完全一致,
  防止说明在改代码后悄悄漂移。
"""
import ast
import importlib.util
import json
import re
import sys
from pathlib import Path

import ontology_domain as od

ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPTS = ROOT / "skills_seed" / "ontology-cross-check" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_m_{name}", SKILL_SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SKILL_SCRIPTS))
    spec.loader.exec_module(mod)
    return mod


class TestRelationLifecycleModel:
    def test_model_is_self_consistent(self):
        states = set(od.STATES)
        assert od.INITIAL in states
        for t in od.TRANSITIONS:
            assert t.source in states and t.target in states and t.via
        # 人工结论只能由人授予:自动流程(裁决、复核)不得产生 asserted / rejected
        assert all(t.actor == "human" for t in od.TRANSITIONS if t.target in ("asserted", "rejected"))
        assert not any(t.source in ("asserted", "rejected") for t in od.TRANSITIONS if t.actor != "human")

    def test_allowed_transitions(self):
        assert od.allowed("candidate", "verified", "cross_check")
        assert od.allowed("verified", "candidate", "cross_check")
        assert not od.allowed("candidate", "asserted", "cross_check")
        assert not od.allowed("asserted", "candidate", "adjudicator")

    def test_cross_check_script_speaks_the_model_language(self):
        """复核技能脚本独立发布、不能导入仓库模块,只能自带常量;这里保证它与模型一致。"""
        src = (SKILL_SCRIPTS / "cross_check.py").read_text(encoding="utf-8")
        rules = set(re.findall(r'"rule":\s*"([a-z_]+)"', src))
        verdicts = set(re.findall(r'"(refuted|review|corroborated|single_channel|conditional_candidate|'
                                  r'valid_rows_candidate|unchanged|not_checkable|[a-z_]+_candidate)"', src))
        assert rules and rules <= set(od.VALID_ROWS_RULES), rules - set(od.VALID_ROWS_RULES)
        assert verdicts <= set(od.CROSS_CHECK_VERDICTS), verdicts - set(od.CROSS_CHECK_VERDICTS)

    def test_every_qa_enforced_rule_produces_a_join_condition(self):
        """模型里标明问数必须执行的有效行规则,问数的 JOIN 提示都要写出对应条件。"""
        import server
        sample = {"soft_delete": {"column": "is_del"}, "tenant_scoped": {"tenant_column": "factory_code"},
                  "scoped": {"column": "dict_code", "group": "TRACK"},
                  "scoped_tenant": {"column": "dict_code", "group": "TRACK", "tenant_column": "factory_code"}}
        assert set(sample) == set(od.QA_ENFORCED_RULES)
        assert od.QA_CONDITIONS["keep_latest"] == ("latest",)          # 版本重复只提示、不强制
        for rule, extra in sample.items():
            ev = {"valid_rows": {"state": "support", "rule": rule, **extra}}
            assert server._scope_join_text(ev, "c", "p").startswith(" AND "), rule

    def test_cross_check_apply_only_makes_model_transitions(self, make_sqlite):
        """执行复核的升降级,每一次迁移都是模型允许的(actor = cross_check)。"""
        xc = _load("cross_check")
        db = make_sqlite({
            "p": ("k TEXT, name TEXT, is_del INTEGER", [("a", "A", 0), ("a", "A", 1), ("b", "B", 0)]),
            "c": ("k TEXT", [("a",), ("b",)]),
            "cfg": ("id INTEGER", [(i,) for i in range(1, 31)]),
            "log_x": ("id INTEGER", [(i,) for i in range(1, 31)]),
        })
        ir = {"objects": [{"name": n, "table": n} for n in ("p", "c", "cfg", "log_x")], "relations": [
            {"source_concept": "c", "target_concept": "p", "status": "candidate",
             "evidence": {"child_key": "k", "parent_key": "k", "overlap": 100.0, "parent_unique": False, "name_ok": True}},
            {"source_concept": "log_x", "target_concept": "cfg", "status": "verified",
             "evidence": {"child_key": "id", "parent_key": "id", "overlap": 100.0}}]}
        before = [r["status"] for r in ir["relations"]]
        rows, _ = xc.check(ir, xc.Db(db))
        after = [r["status"] for r in xc.apply(json.loads(json.dumps(ir)), rows)["relations"]]
        assert before != after
        for a, b in zip(before, after):
            assert od.allowed(a, b, "cross_check"), (a, b)


def _emitted_build_steps():
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    i = src.index("def build_inquire():")
    j = src.index("\n@app.", i + 10)
    return set(re.findall(r'push\("([a-z_]+)"', src[i:j]))


def _pipeline_model():
    text = (ROOT / "docs" / "pipelines" / "ontology_build.yaml").read_text(encoding="utf-8")
    nodes = {}
    for block in re.split(r"\n(?=  - id: )", text.split("\nnodes:", 1)[1].split("\nedges:", 1)[0]):
        m = re.search(r"- id: (\w+)", block)
        steps = re.search(r"sse_steps: \[([^\]]*)\]", block)
        if m:
            nodes[m.group(1)] = [x.strip() for x in steps.group(1).split(",") if x.strip()] if steps else []
    edges = re.findall(r"^  - \[(\w+), (\w+)\]", text.split("\nedges:", 1)[1], re.M)
    return nodes, edges


def test_emitted_steps_equal_model_steps():
    nodes, edges = _pipeline_model()
    declared = [s for steps in nodes.values() for s in steps]
    assert len(declared) == len(set(declared)), "同一步骤名登记在多个节点"
    emitted = _emitted_build_steps()
    assert set(declared) - emitted == set(), f"说明里有、代码不再推送:{set(declared) - emitted}"
    assert emitted - set(declared) == set(), f"代码推送了、说明没登记:{emitted - set(declared)}"
    assert all(x in nodes and y in nodes for x, y in edges)


def test_domain_kernel_has_no_web_or_repo_dependencies():
    tree = ast.parse((ROOT / "ontology_domain.py").read_text(encoding="utf-8"))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= {"__future__", "json", "os", "dataclasses"}
