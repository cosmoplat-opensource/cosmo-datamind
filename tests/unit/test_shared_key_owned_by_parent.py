"""父表声明为主键的列在多张表里同名出现时,仍是父表的标识(DR-085)。

DDR-Bench 10-K 库中 cik 是 companies 的主键,filings、financial_facts、company_tickers 等表都带 cik;
「一成以上的表都有的同名列是模板列」的规则曾把这些引用全部降为候选。模板列(如 legacy_code)不是父表主键,仍按原规则处理。"""
from __future__ import annotations

import server


def _db(make_sqlite, parent_pk=True):
    companies = [(f"{i:010d}", f"Co{i}") for i in range(1, 41)]
    cols = "cik TEXT PRIMARY KEY, name TEXT" if parent_pk else "cik TEXT, name TEXT"
    tables = {"companies": (cols, companies)}
    for t in ("filings", "financial_facts", "company_tickers", "company_addresses"):
        tables[t] = ("id INTEGER PRIMARY KEY, cik TEXT, v TEXT",
                     [(j, f"{(j % 40) + 1:010d}", "x") for j in range(1, 121)])
    return make_sqlite(tables)


def _adjudicate(db):
    extracted = {"objects": [{"name": t, "table": t} for t in
                             ("companies", "filings", "financial_facts", "company_tickers", "company_addresses")],
                 "relations": [{"source": t, "target": "companies", "verb": "属于", "child_key": "cik", "parent_key": "cik"}
                               for t in ("filings", "financial_facts", "company_tickers", "company_addresses")]}
    return server._adjudicate_ir(db, "t", extracted, server._gather_evidence(db))


def test_key_declared_as_parent_primary_key_is_not_a_template_column(make_sqlite):
    ir = _adjudicate(_db(make_sqlite))
    st = {r["source_concept"]: r["status"] for r in ir["relations"] if r.get("target_concept") == "companies"}
    assert st and all(v == "verified" for v in st.values()), st


def test_shared_column_without_parent_primary_key_still_goes_to_review(make_sqlite):
    ir = _adjudicate(_db(make_sqlite, parent_pk=False))
    st = [r["status"] for r in ir["relations"] if r.get("target_concept") == "companies"]
    assert st and "verified" not in st
