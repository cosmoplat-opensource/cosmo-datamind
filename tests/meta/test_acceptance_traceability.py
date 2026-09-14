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
