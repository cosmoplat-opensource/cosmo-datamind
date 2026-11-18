"""Validate SDD-to-executable-test links; behavior is checked by the referenced tests."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_acceptance_requirements_reference_existing_behavior_tests():
    spec = json.loads((ROOT / "specs/acceptance.json").read_text())
    requirements = spec["requirements"]
    assert requirements and len({r["id"] for r in requirements}) == len(requirements)
    for requirement in requirements:
        assert requirement["criterion"] and requirement["tests"]
        for reference in requirement["tests"]:
            filename, name = reference.split("::")
            path = (ROOT / filename).resolve()
            assert path.is_relative_to(ROOT / "tests"), reference
            functions = {node.name for node in ast.walk(ast.parse(path.read_text()))
                         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
            assert name in functions and name.startswith("test_"), reference


def test_delivered_requirements_do_not_list_missing_planned_tests():
    """已交付的需求不能再挂着计划阶段、实际没有写出来的测试(读者会以为它们存在)。"""
    import json
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    reqs = json.loads((root / "specs" / "acceptance.json").read_text(encoding="utf-8"))["requirements"]
    missing = [(r["id"], t) for r in reqs if r.get("status") == "delivered" for t in r.get("planned_tests") or []
               if not (root / t.split("::")[0]).is_file()]
    assert not missing, missing
