"""Require explicit package imports without legacy name collisions — DR-065 / ROB-02."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("order", [
    ["importer", "semantics", "compiler", "validator"],
    ["compiler", "validator", "semantics", "importer"],
])
def test_build_and_consume_are_independent_of_cwd_and_import_order(tmp_path, order):
    root = Path(__file__).resolve().parents[2]
    script = """
import importlib, pathlib, socket, sys
before = list(sys.path)
def guard(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = str(args[0])
        if path.endswith(('.owl', '.xml', '.ttl')) or 'yang_implementation' in path:
            raise AssertionError('Import attempted to read domain assets: ' + path)
sys.addaudithook(guard)
def offline(*a, **k): raise AssertionError('Network during import')
socket.socket.connect = offline
for name in ORDER:
    module = importlib.import_module('domains.robotics.' + name)
    assert pathlib.Path(module.__file__).resolve().is_relative_to(pathlib.Path(ROOT) / 'domains')
assert sys.path == before
assert not ({'skill_runtime', 'ontology_pipeline', 'ontology_backend'} & sys.modules.keys())
print('package imports isolated')
""".replace("ORDER", repr(order)).replace("ROOT", repr(str(root)))
    env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-B", "-c", script], cwd=tmp_path,
                            env=env, text=True, capture_output=True, timeout=20, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "package imports isolated"
    assert not list(tmp_path.iterdir())
