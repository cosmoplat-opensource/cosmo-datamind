# -*- coding: utf-8 -*-
"""随仓分发的构建产物本身要过得了质量闸(DR-113):示例里出现「已验证」却拿不出证据的关系,
等于用示例否定了「verified 只来自可回放的数据证据」这条原则。"""
import glob
import json
import os

import build_quality

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _shipped():
    return sorted(glob.glob(os.path.join(ROOT, "workdir", "built_*.json")))


def test_every_shipped_graph_has_evidence_behind_its_verified_relations():
    assert len(_shipped()) >= 10
    bad = {}
    for path in _shipped():
        with open(path, encoding="utf-8") as f:
            ir = json.load(f)
        issues = build_quality.audit_verified_evidence(ir)["issues"]
        if issues:
            bad[os.path.basename(path)] = len(issues)
    assert bad == {}


def test_the_demo_readme_lists_only_graphs_that_exist():
    import re
    text = open(os.path.join(ROOT, "examples", "README-demo.md"), encoding="utf-8").read()
    listed = set(re.findall(r"`(built_\w+\.json)`", text))
    have = {os.path.basename(p) for p in _shipped()}
    assert listed and listed <= have, listed - have
