# -*- coding: utf-8 -*-
"""DDD 架构适应度:限界上下文地图(specs/models/context_map.json)约束模块依赖(DR-066)。

- 仓库根目录的每个模块都登记在且只登记在一个上下文里(新增模块先登记);
- 模块只能导入本上下文或地图允许的上游上下文;
- 领域上下文不得依赖 Web 框架;
- 随技能发布的脚本只用标准库与同目录脚本(技能要能独立运行)。
"""
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAP = json.loads((ROOT / "specs" / "models" / "context_map.json").read_text(encoding="utf-8"))


def _modules():
    return sorted(p.stem for p in ROOT.glob("*.py"))


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            out.add(n.module.split(".")[0])
    return out


def _owner():
    owner = {}
    for cid, c in MAP["contexts"].items():
        for m in c["modules"]:
            assert m not in owner, f"{m} 同时登记在 {owner[m]} 与 {cid}"
            owner[m] = cid
    return owner


def test_every_module_belongs_to_exactly_one_context():
    owner = _owner()
    missing = [m for m in _modules() if m not in owner and m not in MAP["excluded"]]
    assert not missing, f"未登记到限界上下文的模块:{missing}(在 specs/models/context_map.json 中登记)"
    stale = [m for m in owner if m not in _modules()]
    assert not stale, f"地图里登记了不存在的模块:{stale}"


def test_imports_respect_context_dependencies():
    owner, mods = _owner(), set(_modules())
    violations = []
    for m in _modules():
        if m not in owner:
            continue
        ctx = MAP["contexts"][owner[m]]
        if "*" in ctx["depends_on"]:
            continue
        allowed = {owner[m], *ctx["depends_on"]}
        for dep in _imports(ROOT / f"{m}.py") & mods:
            if dep != m and owner.get(dep) not in allowed:
                violations.append(f"{m}({owner[m]}) → {dep}({owner.get(dep)})")
    assert not violations, "越界依赖:\n" + "\n".join(violations)


def test_domain_contexts_do_not_depend_on_web_frameworks():
    owner = _owner()
    web = set(MAP["web_frameworks"])
    bad = [f"{m} → {sorted(_imports(ROOT / f'{m}.py') & web)}" for m in _modules()
           if owner.get(m) not in (None, "application") and _imports(ROOT / f"{m}.py") & web]
    assert not bad, bad


def test_skill_scripts_are_self_contained():
    stdlib = set(sys.stdlib_module_names)
    for script in (ROOT / "skills_seed").glob("*/scripts/*.py"):
        siblings = {p.stem for p in script.parent.glob("*.py")}
        extra = _imports(script) - stdlib - siblings
        assert not extra, f"{script.relative_to(ROOT)} 依赖了非标准库:{extra}"


def test_every_context_declares_its_ubiquitous_language():
    for cid, c in MAP["contexts"].items():
        assert c["name"] and c["language"], cid
        for dep in c["depends_on"]:
            assert dep == "*" or dep in MAP["contexts"], f"{cid} 依赖未定义的上下文 {dep}"
