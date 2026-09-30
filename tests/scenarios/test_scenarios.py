# -*- coding: utf-8 -*-
"""场景回归基准(DR-067):两种数据形态端到端比对标准答案。逐项结果:python3 scripts/scenario_bench.py"""
import pytest

from tests.scenarios import bench


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    return bench.run_all(tmp_path_factory.mktemp("scenarios"))


@pytest.mark.parametrize("scenario", ["多厂多层湖仓", "元数据驱动业务库"])
def test_scenario_matches_golden_answers(results, scenario):
    mine = [c for c in results if c.scenario == scenario]
    assert len(mine) >= 10, f"{scenario} 的检查项缺失(构建可能中途失败):{[c.id for c in mine]}"
    failed = [f"{c.id}:{c.detail}" for c in mine if not c.ok]
    assert not failed, "\n".join(failed)
