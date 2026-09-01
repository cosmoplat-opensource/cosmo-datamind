# -*- coding: utf-8 -*-
"""srv_context 单测(DR-043/006)。

重点是 `sql_is_readonly` —— 只读底座的**第二道防护**(第一道是 `mode=ro`,第三道是单句执行)。
此前它只被集成套件间接覆盖,缺隔离测试;而它一旦放行写语句,DR-001 的只读承诺即失效,
故此处按攻击面逐类锁定。
"""
import sqlite3
import pytest
import srv_context as sc


class TestSqlIsReadonly:
    @pytest.mark.parametrize("sql", [
        "SELECT 1",
        "select * from t",
        "  SELECT a FROM t WHERE b='delete'",          # 字面量里含关键词不应误伤
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "\n\tSELECT 1",                                  # 前导空白
    ])
    def test_allows_pure_queries(self, sql):
        assert sc.sql_is_readonly(sql) is True

    @pytest.mark.parametrize("sql", [
        "DELETE FROM t",
        "INSERT INTO t VALUES(1)",
        "UPDATE t SET a=1",
        "DROP TABLE t",
        "ALTER TABLE t ADD COLUMN c INT",
        "CREATE TABLE t(a INT)",
        "ATTACH DATABASE 'x' AS y",
        "PRAGMA writable_schema=ON",
        "VACUUM",
        "REPLACE INTO t VALUES(1)",
    ])
    def test_rejects_writes(self, sql):
        assert sc.sql_is_readonly(sql) is False

    @pytest.mark.parametrize("sql", [
        "WITH c AS (SELECT 1) DELETE FROM t",           # CTE 掩护下的写
        "with c as (select 1) insert into t values(1)",
        "WITH c AS (SELECT 1) UPDATE t SET a=1",
    ])
    def test_rejects_write_hidden_behind_cte(self, sql):
        """WITH 开头但内嵌 DML —— 只看首关键字会被绕过,必须拦。"""
        assert sc.sql_is_readonly(sql) is False

    @pytest.mark.parametrize("sql", ["", "   ", None, "EXPLAIN SELECT 1", "-- SELECT 1"])
    def test_rejects_non_query_or_empty(self, sql):
        assert sc.sql_is_readonly(sql) is False


class TestRoConnect:
    def test_missing_db_fails_loudly(self, tmp_path):
        # 缺库必须显式报错,不得静默新建空库(否则「库没了」被伪装成「库是空的」)
        missing = str(tmp_path / "nope.db")
        with pytest.raises(FileNotFoundError):
            sc.ro_connect(missing)
        assert not (tmp_path / "nope.db").exists()      # 且未被创建

    def test_readonly_connection_blocks_write(self, tmp_path):
        db = str(tmp_path / "r.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE t(a INT)")
        con.execute("INSERT INTO t VALUES(1)")
        con.commit(); con.close()

        ro = sc.ro_connect(db)
        assert ro.execute("SELECT count(*) FROM t").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("INSERT INTO t VALUES(2)")       # 引擎层拒写
        ro.close()


class TestAtomicWrite:
    def test_atomic_json_roundtrip_and_no_tmp_left(self, tmp_path):
        p = str(tmp_path / "a.json")
        sc._atomic_json(p, {"k": "值"})
        import json
        assert json.load(open(p, encoding="utf-8")) == {"k": "值"}
        assert [f.name for f in tmp_path.iterdir()] == ["a.json"]

    def test_atomic_text_overwrites_without_truncation_window(self, tmp_path):
        p = str(tmp_path / "a.txt")
        sc._atomic_text(p, "第一版")
        sc._atomic_text(p, "第二版内容更长")
        assert open(p, encoding="utf-8").read() == "第二版内容更长"
        assert [f.name for f in tmp_path.iterdir()] == ["a.txt"]

    def test_atomic_bytes_roundtrip_permissions_and_no_tmp(self, tmp_path):
        import os
        import stat
        p = str(tmp_path / "upload.bin")
        sc._atomic_bytes(p, b"\x00\xffpayload", mode=0o600)
        assert open(p, "rb").read() == b"\x00\xffpayload"
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
        assert [f.name for f in tmp_path.iterdir()] == ["upload.bin"]

    @pytest.mark.parametrize("writer,payload", [
        (sc._atomic_text, "x"),
        (sc._atomic_bytes, b"x"),
    ])
    def test_atomic_writers_reject_parent_reference(self, tmp_path, writer, payload):
        with pytest.raises(ValueError):
            writer(str(tmp_path / ".." / "escape"), payload)

    def test_concurrent_atomic_text_writers_publish_one_complete_value(self, tmp_path):
        import threading
        p = str(tmp_path / "shared.txt")
        values = [(str(i) + "-数据-") * 4000 for i in range(8)]
        threads = [threading.Thread(target=sc._atomic_text, args=(p, value)) for value in values]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert open(p, encoding="utf-8").read() in values
        assert [f.name for f in tmp_path.iterdir()] == ["shared.txt"]
