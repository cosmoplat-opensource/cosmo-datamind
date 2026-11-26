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


@pytest.mark.parametrize('seed', range(12))
def test_catalog_unit_sets_match_actual_data_with_quoted_identifiers(seed):
    import sqlite3
    import data_catalog
    rng = random.Random(seed)
    table = 'odd "table' + str(seed)
    con = sqlite3.connect(':memory:')
    con.execute(f'CREATE TABLE {data_catalog.qi(table)}(metric_name TEXT, metric_value REAL, unit TEXT)')
    rows = [(f'M{i % 4}', i, rng.choice(['USD', 'EUR', ''])) for i in range(80)]
    con.executemany(f'INSERT INTO {data_catalog.qi(table)} VALUES (?,?,?)', rows)
    cat = data_catalog.value_catalog(con, table, [('metric_name', 'TEXT'), ('metric_value', 'REAL'), ('unit', 'TEXT')],
                                     min_distinct=4)
    for e in cat['entries']:
        units = {r[2] for r in rows if r[0] == e['value']}
        assert set(e['units']) == units
        assert e['rows'] == sum(r[0] == e['value'] for r in rows)
        assert e['unit'] == (next(iter(units)) if len(units) == 1 else '')
    con.close()


def test_graph_edit_deletes_only_selected_named_individual_facts():
    """DR-188：不同密度、循环和自环下，删除范围只由 RDF 资源身份决定。"""
    from rdflib import Graph, Literal, Namespace, OWL, RDF
    from ontology_assets.graph_edit_copy import apply_edit

    rng = random.Random(188)
    ns = Namespace('urn:sample:')
    for size in range(2, 22):
        graph = Graph()
        graph.add((ns.link, RDF.type, OWL.ObjectProperty))
        graph.add((ns.value, RDF.type, OWL.DatatypeProperty))
        nodes = [ns[f'n{i}'] for i in range(size)]
        target = rng.choice(nodes)
        for node in nodes:
            graph.add((node, RDF.type, OWL.NamedIndividual))
            graph.add((node, ns.value, Literal(str(target))))
            for neighbor in rng.sample(nodes, rng.randrange(size + 1)):
                graph.add((node, ns.link, neighbor))
        expected = {t for t in graph if target not in t}
        apply_edit(graph, {'op': 'delete', 'iri': str(target)})
        assert set(graph) == expected


def test_graph_edit_date_values_match_calendar_and_xsd_shape():
    """DR-193：随机日历日期、时区与小数秒的合法写法都通过，单处结构破坏都被拒绝。"""
    from datetime import date, timedelta
    from ontology_assets.graph_edit_copy import validate_literal

    rng = random.Random(193)
    date_type, datetime_type = 'http://www.w3.org/2001/XMLSchema#date', 'http://www.w3.org/2001/XMLSchema#dateTime'

    def rejected(value, datatype):
        try:
            validate_literal(value, datatype)
        except ValueError:
            return True
        return False

    for _ in range(400):
        day = date(1, 1, 1) + timedelta(days=rng.randrange(date(9999, 12, 31).toordinal()))
        zone = rng.choice(['', 'Z', f"{rng.choice('+-')}{rng.randrange(14):02d}:{rng.randrange(60):02d}", '+14:00'])
        clock = f'{rng.randrange(24):02d}:{rng.randrange(60):02d}:{rng.randrange(60):02d}'
        fraction = rng.choice(['', '.' + str(rng.randrange(10 ** 6)).zfill(rng.randint(1, 6))])
        validate_literal(day.isoformat() + zone, date_type)
        validate_literal(f'{day.isoformat()}T{clock}{fraction}{zone}', datetime_type)
        assert rejected(day.isoformat().replace('-', ''), date_type)
        assert rejected(f'{day.isoformat()}T{clock[:5]}{zone}', datetime_type)
        assert rejected(f'{day.isoformat()}T{clock}{fraction}+00:00Z', datetime_type)
        last = (date(day.year + (day.month == 12), day.month % 12 + 1, 1) - timedelta(days=1)).day if day.year < 9999 or day.month < 12 else 31
        assert rejected(f'{day.year:04d}-{day.month:02d}-{last + 1:02d}', date_type)
