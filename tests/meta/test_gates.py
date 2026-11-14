# -*- coding: utf-8 -*-
"""门槛本身的约束(DR-106):覆盖率统计不得再把路由层排除在外;函数体类型检查的名单不得缩水到空。"""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
ROUTE_LAYER = {"server", "bp_actions", "bp_engine", "bp_metrics", "bp_semantic", "srv_actions", "srv_engine"}


def test_coverage_counts_the_route_layer_and_has_layered_floors():
    source = set(CONFIG["tool"]["coverage"]["run"]["source"])
    assert ROUTE_LAYER <= source, sorted(ROUTE_LAYER - source)
    production = {p.stem for p in ROOT.glob("*.py")} - {"test_all", "test_ui", "test_ui_ops", "gunicorn.conf", "wsgi"}
    assert production <= source, "未计入覆盖率的生产模块:%s" % sorted(production - source)
    assert CONFIG["tool"]["coverage"]["report"]["fail_under"] >= 74.0
    check = (ROOT / "scripts/check.py").read_text(encoding="utf-8")
    floors = {name: int(v) for name, v in re.findall(r'\("(coverage-(?:core|routes))".*?--fail-under=(\d+)', check, re.S)}
    assert floors.get("coverage-core", 0) >= 87 and floors.get("coverage-routes", 0) >= 61, floors
    assert all(m + ".py" in check for m in ROUTE_LAYER)


def test_function_bodies_are_type_checked_for_listed_modules():
    strict = [o for o in CONFIG["tool"]["mypy"]["overrides"] if o.get("check_untyped_defs") is True]
    modules = {m for o in strict for m in o["module"]}
    assert len(modules) >= 37
    assert {"dao_core", "srv_context", "sqlite_evidence", "key_links", "relation_triage", "bp_engine"} <= modules
    missing = [m for m in modules if not (ROOT / (m.replace(".", "/") + ".py")).exists()]
    assert not missing, "名单里的模块不存在:%s" % missing
