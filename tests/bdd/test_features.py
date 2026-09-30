# -*- coding: utf-8 -*-
"""执行 specs/features/*.feature 中的中文行为场景(BDD,DR-066)。每个场景是一条测试。"""
from pathlib import Path

import pytest

from tests.bdd import gherkin
from tests.bdd import steps  # noqa: F401  注册步骤

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = [s for f in sorted((ROOT / "specs" / "features").glob("*.feature")) for s in gherkin.parse(f)]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[f"{Path(s.path).stem}::{s.name}" for s in SCENARIOS])
def test_scenario(scenario, tmp_path, monkeypatch, make_sqlite):
    world = {"tmp": tmp_path, "monkeypatch": monkeypatch, "make_sqlite": make_sqlite}
    gherkin.run(scenario, world)
