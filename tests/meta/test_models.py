# -*- coding: utf-8 -*-
"""模型与代码一致性(DR-066)。

- 验收与问数从模型取有效行规则,复核技能脚本自带的规则名与模型一致;
- 构建代码推送的 SSE 步骤与流水线说明(docs/pipelines/ontology_build.yaml)登记的完全一致,
  防止说明在改代码后悄悄漂移。
"""
import ast
import re
from pathlib import Path

import ontology_domain as od

ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPTS = ROOT / "skills_seed" / "ontology-cross-check" / "scripts"



class TestValidRowsModel:
    def test_cross_check_script_speaks_the_model_language(self):
        """复核技能脚本独立发布、不能导入仓库模块,只能自带常量;这里保证它与模型一致。"""
        src = (SKILL_SCRIPTS / "cross_check.py").read_text(encoding="utf-8")
        rules = set(re.findall(r'"rule":\s*"([a-z_]+)"', src))
        assert rules and rules <= set(od.VALID_ROWS_RULES), rules - set(od.VALID_ROWS_RULES)

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
