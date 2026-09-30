# -*- coding: utf-8 -*-
"""TDD · 性质测试(DR-066):用固定种子随机生成大量输入,检查恒成立的性质。

只用在「输入空间大、个例枚举不全」的两处:表族识别(随机生成的湖仓,曾据此发现小湖仓里
各厂副本归不到一起的缺陷)与观测脱敏(随机的敏感键与取值)。固定种子,失败可复现。
"""
import random
import re

import pytest

import concept_hints as ch
import observability as obs

WORDS = ["order", "item", "area", "group", "route", "device", "meter", "station", "repair", "pallet", "barcode",
         "project", "material", "supplier", "customer", "shift", "plan", "alarm", "ticket", "bill"]
SUBTYPES = ["fct", "aoi", "ict", "spi"]


def _entity(rng):
    return "_".join(rng.sample(WORDS, rng.choice([2, 3])))


def _lakehouse(rng):
    factories = [f"p{i}" for i in range(rng.randint(3, 6))]
    convs = {f: rng.choice(["t", "tb"]) for f in factories}
    entities = sorted({_entity(rng) for _ in range(rng.randint(12, 25))})
    cols, truth = {}, {}
    for e in entities:
        for f in rng.sample(factories, rng.randint(2, len(factories))):
            name = f"ods_{f}_{convs[f]}_{e}"
            if rng.random() < 0.2:
                name += rng.choice(["_bak", "_offline", "_20240101"])
            cols[name] = ["id", "dt", "insert_time", f"{e}_code", f"{e}_name"]
            truth[name] = e
    for sub in rng.sample(SUBTYPES, 2):
        for f in factories[:2]:
            name = f"dwd_{f}_{sub}_factor_detail"
            cols[name] = ["id", "dt", f"{sub}_value"]
            truth[name] = f"{sub}_factor_detail"
    return cols, truth


@pytest.mark.parametrize("seed", range(40))
def test_table_families_are_disjoint_complete_and_never_merge_different_entities(seed):
    rng = random.Random(seed)
    cols, truth = _lakehouse(rng)
    fams = ch.table_families(cols)["families"]
    seen = {}
    for f in fams:
        assert f["representative"] in f["members"], (seed, f)
        for m in f["members"]:
            assert m not in seen, f"seed={seed} {m} 同时在两个表族"
            seen[m] = f["core"]
        assert len({truth[m] for m in f["members"]}) == 1, f"seed={seed} 不同实体被并在一起:{f['members']}"
    by_entity = {}
    for t, e in truth.items():
        by_entity.setdefault(e, []).append(t)
    for e, ts in by_entity.items():
        if len(ts) >= 2:
            assert len({seen.get(t) for t in ts}) == 1 and seen.get(ts[0]), f"seed={seed} 实体 {e} 的副本没归到一起:{ts}"


@pytest.mark.parametrize("seed", range(30))
def test_observability_never_stores_sensitive_values(seed, tmp_path, monkeypatch):
    monkeypatch.setenv("DATAMIND_OBS_DIR", str(tmp_path))
    rng = random.Random(seed)
    secret = "".join(rng.choice("机密数据ABCxyz0123") for _ in range(rng.randint(6, 30)))
    key = rng.choice(["q", "question", "sql", "password", "api_key", "dsn", "prompt", "user_text"])
    ip = ".".join(str(rng.randint(1, 254)) for _ in range(4))
    r = obs.Run("qa", **{key: secret}, note=f"host {ip} x")
    r.finish("ok")
    dumped = open(next(tmp_path.glob("obs/*.jsonl")), encoding="utf-8").read()
    assert secret not in dumped and ip not in dumped, (seed, key)
    assert re.search(r'"%s_fp"' % re.escape(key), dumped)
