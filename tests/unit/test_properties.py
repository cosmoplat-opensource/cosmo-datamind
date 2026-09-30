# -*- coding: utf-8 -*-
"""TDD · 性质测试(DR-066):用固定种子随机生成大量输入,检查恒成立的性质。

AI 生成的代码最容易在「没想到的输入」上出错;枚举个例测不全,性质测试把边界交给生成器。
不引入 hypothesis:固定种子的随机生成足够,失败可复现(断言信息带种子与输入)。
"""
import random
import re

import pytest

import code_contracts as cc
import concept_hints as ch
import dao_core
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


@pytest.mark.parametrize("seed", range(40))
def test_join_contracts_are_extracted_exactly_and_hits_are_symmetric(seed):
    rng = random.Random(seed)
    tables = [f"t_{_entity(rng)}" for _ in range(rng.randint(2, 5))]
    tables = list(dict.fromkeys(tables))
    if len(tables) < 2:
        return
    aliases = [f"a{i}" for i in range(len(tables))]
    joins, sql = set(), [f"SELECT 1 FROM {tables[0]} {aliases[0]}"]
    for i in range(1, len(tables)):
        ck, pk = f"c{rng.randint(0, 9)}_id", f"p{rng.randint(0, 9)}_code"
        sql.append(f"LEFT JOIN {tables[i]} {aliases[i]} ON {aliases[i]}.{pk} = {aliases[0]}.{ck}"
                   f" AND {aliases[i]}.tenement_id = {aliases[0]}.tenement_id")
        joins.add(frozenset({(tables[i], pk), (tables[0], ck)}))
    res = cc.extract([("x.zip", "### 文件 m.xml\n" + "\n".join(sql))])
    got = {frozenset({tuple(j["a"]), tuple(j["b"])}) for j in res["joins"] if not j["envelope"]}
    assert got == joins, (seed, sql)
    assert all(j["a"][1] == "tenement_id" for j in res["joins"] if j["envelope"])
    resolved = [{"a": list(next(iter(j))), "b": list(sorted(j)[-1])} for j in joins]
    for r in resolved:
        (ta, ca), (tb, cbb) = r["a"], r["b"]
        assert bool(cc.hit(resolved, ta, ca, tb, cbb)) == bool(cc.hit(resolved, tb, cbb, ta, ca))


@pytest.mark.parametrize("seed", range(60))
def test_key_name_compatibility_is_symmetric(seed):
    rng = random.Random(seed)
    toks = ["dev", "type", "code", "sys", "area", "sn", "mete", "meta", "id", "no", "status", "order", "wd"]
    a = "_".join(rng.choice(toks) for _ in range(rng.randint(1, 4)))
    b = "_".join(rng.choice(toks) for _ in range(rng.randint(1, 4)))
    assert dao_core.key_name_ok(a, b) == dao_core.key_name_ok(b, a), (a, b)


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
