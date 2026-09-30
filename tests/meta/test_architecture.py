# -*- coding: utf-8 -*-
"""架构适应度(DR-066):领域模块不依赖 Web 层;随技能发布的脚本只用标准库。

只保留这两条可执行的边界。Web 层以外的模块被 server.py 调用,反过来依赖 Web 层就会形成环、
也无法离线单测;技能脚本要能脱离本仓独立运行。
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Web / 接口层:可以依赖一切;其余模块都是领域或基础设施
APPLICATION = {"server", "wsgi", "srv_actions", "srv_engine", "srv_hardening", "openai_runtime",
               "mcp_action_server", "mcp_build_server", "gunicorn.conf", "test_all", "test_ui", "test_ui_ops"}
WEB = {"flask", "werkzeug", "gunicorn"}


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            out.add(n.module.split(".")[0])
    return out


def _domain_modules():
    return [p for p in ROOT.glob("*.py") if p.stem not in APPLICATION and not p.stem.startswith("bp_")]


def test_domain_modules_do_not_depend_on_the_web_layer():
    web_modules = APPLICATION | {p.stem for p in ROOT.glob("bp_*.py")}
    bad = []
    for p in _domain_modules():
        deps = _imports(p) & (WEB | web_modules)
        if deps:
            bad.append(f"{p.name} → {sorted(deps)}")
    assert not bad, "领域模块依赖了 Web 层:\n" + "\n".join(bad)


def test_skill_scripts_are_self_contained():
    stdlib = set(sys.stdlib_module_names)
    for script in (ROOT / "skills_seed").glob("*/scripts/*.py"):
        siblings = {p.stem for p in script.parent.glob("*.py")}
        extra = _imports(script) - stdlib - siblings
        assert not extra, f"{script.relative_to(ROOT)} 依赖了非标准库:{extra}"
