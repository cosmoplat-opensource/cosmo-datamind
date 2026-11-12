"""DR-093: catalog evidence is scoped, replayable and does not certify meaning."""
import sqlite3

import pytest


def _db():
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE facts(entity_id TEXT, fact_name TEXT, fact_value REAL, unit TEXT);
        CREATE TABLE column_documentation(table_name TEXT, column_name TEXT, documentation TEXT);
        INSERT INTO column_documentation VALUES ('facts','fact_value','Reported value'),
            ('absent','x','Invented'),('facts','absent','Invented');
    """)
    con.executemany("INSERT INTO facts VALUES (?,?,?,?)",
                    [(f"E{i % 3}", f"Metric{i % 20}", i, 'USD' if i % 2 else 'EUR') for i in range(120)])
    return con


def test_dictionary_only_describes_existing_columns():
    import data_catalog
    with _db() as con:
        assert data_catalog.doc_dictionary(con) == {"facts": {"fact_value": "Reported value"}}


def test_catalog_records_coverage_and_unit_ambiguity():
    import data_catalog
    with _db() as con:
        cols = [(r[1], r[2]) for r in con.execute('PRAGMA table_info(facts)')]
        # One code uses multiple units; the directory must not silently choose one.
        con.execute("INSERT INTO facts VALUES ('E0','Metric0',12,'USD')")
        cat = data_catalog.value_catalog(con, "facts", cols, entity_col="entity_id", top=5)
        assert cat["distinct"] == 20 and len(cat["entries"]) == 5
        assert cat["truncated"] and cat["evidence"]["method"] == "sql_group_by"
        hit = next(x for x in cat["entries"] if x["value"] == "Metric0")
        assert set(hit["units"]) == {"USD", "EUR"} and hit["unit"] == ""
        assert cat["per_entity"]["entities"] == 3


def test_catalog_distinguishes_complete_scan_from_truncated_directory():
    import data_catalog
    with _db() as con:
        cols = [(r[1], r[2]) for r in con.execute('PRAGMA table_info(facts)')]
        partial = data_catalog.value_catalog(con, 'facts', cols, top=5)
        assert partial['evidence']['source_scan_complete'] is True
        assert partial['evidence']['coverage_complete'] is False
        complete = data_catalog.value_catalog(con, 'facts', cols, top=30)
        assert complete['evidence']['source_scan_complete'] is True
        assert complete['evidence']['coverage_complete'] is True


def test_catalog_preserves_zero_unit_code_and_missing_unit_separately():
    import data_catalog
    with sqlite3.connect(':memory:') as con:
        con.execute('CREATE TABLE facts(fact_name TEXT, fact_value REAL, unit NUMERIC)')
        con.executemany('INSERT INTO facts VALUES (?,?,?)', [(f'Metric{i % 20}', i, 0) for i in range(120)])
        cols = [(r[1], r[2]) for r in con.execute('PRAGMA table_info(facts)')]
        cat = data_catalog.value_catalog(con, 'facts', cols)
        first = next(e for e in cat['entries'] if e['value'] == 'Metric0')
        assert first['unit'] == '0' and first['units'] == ['0']
        con.execute("INSERT INTO facts VALUES ('Metric0',12,NULL)")
        cat = data_catalog.value_catalog(con, 'facts', cols)
        first = next(e for e in cat['entries'] if e['value'] == 'Metric0')
        assert set(first['units']) == {'0', ''} and first['unit'] == ''
        assert first['unit_counts'] == [{'unit': '0', 'rows': 6}, {'unit': '', 'rows': 1}]


def test_null_profile_checks_real_columns_and_includes_blanks():
    import data_catalog
    con = sqlite3.connect(":memory:")
    con.execute('CREATE TABLE "odd table"("odd column" TEXT)')
    con.executemany('INSERT INTO "odd table" VALUES (?)', [(None,), (' ',), ('a',), ('b',)])
    assert data_catalog.null_profile(con, "odd table", "odd column")["ratio"] == 0.5
    with pytest.raises(ValueError):
        data_catalog.null_profile(con, "odd table", "missing")
    con.close()


def test_context_uses_aliases_themes_and_preserves_derived_candidate():
    import workload_concepts as wc
    ir = {"concepts": [{"name": "Output", "aliases": ["产量"], "table": "facts", "column": "fact_value",
                        "filter": "fact_name='Output'", "status": "candidate", "evidence": {"query_verified": True}}],
          "themes": [{"name": "Energy", "aliases": ["能耗"], "table": "facts", "name_col": "fact_name",
                      "codes": [{"code": "Electricity"}, {"code": "Gas"}], "status": "candidate"}],
          "derived": [{"name": "Intensity", "aliases": ["单位产量能耗"], "formula": "Energy / Output",
                       "uses": ["Output"], "status": "candidate"}]}
    text, n = wc.context("单位产量能耗", ir)
    assert n >= 2 and "facts" in text and "Electricity" in text and "Intensity" in text
    assert "待确认" in text and "已认证" not in text


def test_request_profiles_follow_database_changes(make_sqlite):
    import server
    db = make_sqlite({"facts": ("value TEXT", [(None,), ('x',)])})
    token = server._QA_DB.set(db)
    try:
        assert server._null_ratio("facts", "value") == 0.5
        with sqlite3.connect(db) as con:
            con.execute("UPDATE facts SET value='x'")
        assert server._null_ratio("facts", "value") == 0.0
    finally:
        server._QA_DB.reset(token)


def test_schema_values_dictionary_and_value_owners_follow_source_updates(make_sqlite):
    import server
    db = make_sqlite({'facts': ('code TEXT', [('A1',), ('B2',)]),
                      'dim_column_metadata': ('table_name TEXT, column_name TEXT, column_name_cn TEXT',
                                             [('facts', 'code', '旧名称')])})
    token = server._QA_DB.set(db)
    try:
        assert server._table_cols('facts') == ['code']
        assert server._col_values('facts', 'code', 1) is None
        assert server._col_values('facts', 'code', 2) == ['A1', 'B2']
        assert server._dict_cn('facts')['code'] == '旧名称'
        objs = [{'table': 'facts', 'attrs': [{'col': 'code', 'type': 'TEXT'}]}]
        assert not server._value_owners('C30', objs)
        with sqlite3.connect(db) as con:
            con.execute('ALTER TABLE facts ADD COLUMN value REAL')
            con.execute("INSERT INTO facts(code) VALUES ('C30')")
            con.execute("UPDATE dim_column_metadata SET column_name_cn='新名称'")
        assert server._table_cols('facts') == ['code', 'value']
        assert server._col_values('facts', 'code', 3) == ['A1', 'B2', 'C30']
        assert server._dict_cn('facts')['code'] == '新名称'
        assert server._value_owners('C30', objs) == {'facts': ['C30']}
    finally:
        server._QA_DB.reset(token)
