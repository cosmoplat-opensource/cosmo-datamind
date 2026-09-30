# -*- coding: utf-8 -*-
"""外部库只读抽样。

源库账号可能有写权限;只读由三层保证。这里用假的 DB-API 连接锁住:只读会话先于任何
查询生效、写语句一律被拒、抽样规则与注释保存、半截产物不会成为数据源。
"""
import os
import re
import sqlite3

import pytest

import ext_sample


class FakeCursor:
    def __init__(self, con):
        self.con, self.description, self._rows = con, None, []

    def execute(self, sql, args=None):
        self.con.log.append(sql)
        s = sql.strip()
        if s.upper().startswith("SET"):
            self.description, self._rows = None, []
            return
        if not self.con.log_readonly_first():
            raise AssertionError("在只读会话生效前执行了查询")
        if "information_schema.TABLES" in s:
            rows = [(t, n, c) for t, n, c in self.con.tables]
            self.description = [("t",), ("n",), ("c",)]
        elif "information_schema.COLUMNS" in s:
            rows = [r for r in self.con.columns]
            self.description = [("t",), ("c",), ("ty",), ("k",), ("cm",)]
        else:
            m = re.search(r"FROM `([^`]+)`", s)
            table = m.group(1)
            if table in self.con.fail:
                raise RuntimeError("boom")
            cols = [c.strip("` ") for c in re.search(r"SELECT (.*) FROM", s).group(1).split(",")]
            data = self.con.data[table]
            limit = re.search(r"LIMIT (\d+)", s)
            if "DESC" in s:
                data = sorted(data, key=lambda r: -r["id"])
            if limit:
                data = data[:int(limit.group(1))]
            rows = [tuple(r[c] for c in cols) for r in data]
            self.description = [(c,) for c in cols]
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class FakeCon:
    def __init__(self, fail=()):
        self.log = []
        self.fail = set(fail)
        self.tables = [("small", 3, "小表"), ("big", 10, "大表"), ("files", 1, "")]
        self.columns = [("small", "id", "int(11)", "PRI", "主键"), ("small", "ent_no", "varchar(20)", "", "企业编号"),
                        ("big", "id", "bigint", "PRI", ""), ("big", "v", "decimal(10,2)", "", "数值"),
                        ("files", "id", "int", "PRI", ""), ("files", "blob_col", "longblob", "", "附件")]
        self.data = {"small": [{"id": i, "ent_no": f"E{i}"} for i in range(1, 4)],
                     "big": [{"id": i, "v": i * 1.5} for i in range(1, 11)],
                     "files": [{"id": 1, "blob_col": b"x"}]}

    def log_readonly_first(self):
        return any("TRANSACTION READ ONLY" in x for x in self.log)

    def cursor(self):
        return FakeCursor(self)


def test_readonly_session_is_set_before_any_query():
    con = FakeCon()
    ext_sample.open_readonly(con)
    assert con.log[0] == "SET SESSION TRANSACTION READ ONLY"


@pytest.mark.parametrize("sql", [
    "UPDATE t SET a=1", "DELETE FROM t", "INSERT INTO t VALUES (1)", "DROP TABLE t",
    "SELECT 1; DROP TABLE t", "SET GLOBAL read_only=0", "CALL p()", "select * from t for update; delete from t",
])
def test_write_statements_are_rejected(sql):
    ro = ext_sample.open_readonly(FakeCon())
    with pytest.raises(PermissionError):
        ro.q(sql)


def test_keywords_inside_quotes_do_not_trigger_false_rejection():
    ro = ext_sample.open_readonly(FakeCon())
    assert ro.q("SELECT TABLE_NAME t, TABLE_ROWS n, TABLE_COMMENT c FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA='update_log'")


def test_sampling_rules_comments_and_skipped_columns(tmp_path):
    ro = ext_sample.open_readonly(FakeCon())
    out = str(tmp_path / "s.db")
    st = ext_sample.sample(ro, out, prefix="src__", full=5, sample_rows=4)
    db = sqlite3.connect(out)
    assert st["tables"] == 3 and st["sampled"] == ["src__big"]
    assert db.execute('SELECT COUNT(*) FROM "src__small"').fetchone()[0] == 3          # ≤ full:全量
    assert [r[0] for r in db.execute('SELECT id FROM "src__big" ORDER BY id')] == [7, 8, 9, 10]   # 最新 4 行
    assert db.execute('SELECT blob_col FROM "src__files"').fetchone()[0] is None        # 二进制列不导出
    assert db.execute("SELECT comment FROM _dm_tables WHERE table_name='src__small'").fetchone()[0] == "小表"
    assert db.execute("SELECT comment FROM _dm_columns WHERE table_name='src__small' AND column_name='ent_no'"
                      ).fetchone()[0] == "企业编号"
    assert [r[1] for r in db.execute('PRAGMA table_info("src__small")') if r[5]] == ["id"]
    assert not db.execute("PRAGMA foreign_key_list(\"src__small\")").fetchall()         # 不声明外键
    assert not os.path.exists(out + ".part")


def test_table_failure_is_recorded_not_fatal(tmp_path):
    ro = ext_sample.open_readonly(FakeCon(fail={"big"}))
    st = ext_sample.sample(ro, str(tmp_path / "s.db"))
    assert st["failed"] == [{"table": "big", "error": "RuntimeError"}] and st["tables"] == 3


def test_partial_output_never_replaces_target(tmp_path):
    """中途异常时,已有的抽样库保持原样,半截产物不会顶替它。"""
    out = tmp_path / "s.db"
    out.write_bytes(b"previous")

    class Boom(FakeCon):
        def cursor(self):
            c = super().cursor()
            orig = c.execute
            def execute(sql, args=None):
                if "COLUMNS" in sql:
                    raise PermissionError("stop")
                return orig(sql, args)
            c.execute = execute
            return c
    ro = ext_sample.open_readonly(Boom())
    with pytest.raises(PermissionError):
        ext_sample.sample(ro, str(out))
    assert out.read_bytes() == b"previous"


def test_multi_source_merge_uses_prefixes(tmp_path):
    out = str(tmp_path / "m.db")
    dst = ext_sample.open_target(out)
    for pre in ("a__", "b__"):
        ext_sample.sample_into(ext_sample.open_readonly(FakeCon()), dst, prefix=pre)
    ext_sample.finish_target(dst, out)
    names = {r[0] for r in sqlite3.connect(out).execute("SELECT table_name FROM _dm_tables")}
    assert {"a__small", "b__small", "a__big", "b__big"} <= names
