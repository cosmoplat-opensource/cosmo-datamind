# -*- coding: utf-8 -*-
"""图谱列表与表清单的进程内缓存:命中要快,失效要准。

/api/graphs 被前端六处调用,每次都对全部图谱源跑 解析→净化→编辑回放→转图→
执行画像,耗时随 built_* 文件数线性增长。加缓存的风险全在「该失效时没失效」:
用户改了本体却看到旧计数,比慢更糟。故本测试的重点是失效面而非命中率——
IR 文件、编辑栈、底座库三者任一变化都必须重算。
"""
import json
import os

import pytest

server = pytest.importorskip("server")


class TestStatSig:
    def test_missing_file_has_stable_distinct_signature(self, tmp_path):
        """文件不存在也要有指纹,且与「存在且为空」不同——否则从无到有不会触发失效。"""
        p = str(tmp_path / "nope.json")
        assert server._stat_sig(p) == ((p, 0, -1),)
        open(p, "w").close()
        assert server._stat_sig(p) != ((p, 0, -1),)

    def test_content_change_changes_signature(self, tmp_path):
        p = tmp_path / "a.json"
        p.write_text("{}")
        before = server._stat_sig(str(p))
        p.write_text('{"objects": []}')          # 长度变化,mtime 精度不足时也能分辨
        assert server._stat_sig(str(p)) != before

    def test_signature_covers_all_paths(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.write_text("x"); b.write_text("y")
        sig = server._stat_sig(str(a), str(b))
        assert len(sig) == 2 and [s[0] for s in sig] == [str(a), str(b)]


class TestTableInventoryCache:
    def test_returns_copies_so_callers_cannot_poison_cache(self, monkeypatch, tmp_path):
        """调用方拿到的是副本:_qa_graph_profile 之类若就地改集合,不能污染下一次调用。"""
        db = tmp_path / "m.db"
        import sqlite3
        con = sqlite3.connect(str(db)); con.execute("CREATE TABLE t1(a)"); con.commit(); con.close()
        monkeypatch.setattr(server, "DB", str(db))
        monkeypatch.setattr(server, "UPLOAD_DB", str(tmp_path / "none.db"))
        server._INV_CACHE["sig"] = None

        main, _ = server._qa_table_inventory()
        main.add("污染表")
        main2, _ = server._qa_table_inventory()
        assert "污染表" not in main2 and "t1" in main2

    def test_new_table_invalidates_cache(self, monkeypatch, tmp_path):
        """上传新表后必须立刻可见——缓存住旧清单会让新表被判为「未接入」。"""
        import sqlite3
        db = tmp_path / "m.db"
        con = sqlite3.connect(str(db)); con.execute("CREATE TABLE t1(a)"); con.commit(); con.close()
        monkeypatch.setattr(server, "DB", str(db))
        monkeypatch.setattr(server, "UPLOAD_DB", str(tmp_path / "none.db"))
        server._INV_CACHE["sig"] = None
        assert "t2" not in server._qa_table_inventory()[0]

        con = sqlite3.connect(str(db)); con.execute("CREATE TABLE t2(a)"); con.commit(); con.close()
        assert "t2" in server._qa_table_inventory()[0]

    def test_second_call_does_not_reopen_database(self, monkeypatch, tmp_path):
        """命中时不得再连库——这正是加缓存要省掉的开销。"""
        import sqlite3
        db = tmp_path / "m.db"
        con = sqlite3.connect(str(db)); con.execute("CREATE TABLE t1(a)"); con.commit(); con.close()
        monkeypatch.setattr(server, "DB", str(db))
        monkeypatch.setattr(server, "UPLOAD_DB", str(tmp_path / "none.db"))
        server._INV_CACHE["sig"] = None
        server._qa_table_inventory()

        calls = []
        real = server.ro_connect
        monkeypatch.setattr(server, "ro_connect", lambda p: (calls.append(p), real(p))[1])
        server._qa_table_inventory()
        assert calls == []


class TestGraphRowCache:
    def _seed(self, tmp_path, monkeypatch, objects):
        p = tmp_path / "built_cachetest.json"
        p.write_text(json.dumps({"scenario": {"name": "缓存用例"}, "objects": objects,
                                 "relations": []}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(server, "WORK", str(tmp_path))
        server._GRAPH_ROW_CACHE.clear()
        return p

    def test_hit_avoids_recomputation(self, tmp_path, monkeypatch):
        p = self._seed(tmp_path, monkeypatch, [{"name": "订单", "table": "fact_order"}])
        inv = ({"fact_order"}, set())
        sig = server._stat_sig(str(p))
        first = server._graph_row("built_cachetest", "built", [str(p)], inv, sig)

        calls = []
        monkeypatch.setattr(server, "load_ir_edited",
                            lambda k: calls.append(k) or {"objects": []})
        second = server._graph_row("built_cachetest", "built", [str(p)], inv, sig)
        assert calls == [] and second == first

    def test_ir_change_invalidates(self, tmp_path, monkeypatch):
        """改了本体就必须重算,否则界面上的对象数会一直停在旧值。"""
        p = self._seed(tmp_path, monkeypatch, [{"name": "订单", "table": "fact_order"}])
        inv = ({"fact_order", "dim_customer"}, set())
        sig = server._stat_sig(str(p))
        assert server._graph_row("built_cachetest", "built", [str(p)], inv, sig)["nodes"] == 1

        p.write_text(json.dumps({"scenario": {"name": "缓存用例"},
                                 "objects": [{"name": "订单", "table": "fact_order"},
                                             {"name": "客户", "table": "dim_customer"}],
                                 "relations": []}, ensure_ascii=False), encoding="utf-8")
        row = server._graph_row("built_cachetest", "built", [str(p)],
                                inv, server._stat_sig(str(p)))
        assert row["nodes"] == 2

    def test_inventory_change_invalidates(self, tmp_path, monkeypatch):
        """换了查询连接,同一本体的「可执行」判定会翻转,缓存必须跟着失效。"""
        p = self._seed(tmp_path, monkeypatch, [{"name": "订单", "table": "fact_order"}])
        sig = server._stat_sig(str(p))
        off = server._graph_row("built_cachetest", "built", [str(p)], (set(), set()), sig)
        assert off["queryable"] is False

        on = server._graph_row("built_cachetest", "built", [str(p)],
                               ({"fact_order"}, set()), ("changed",))
        assert on["queryable"] is True

    def test_edits_change_invalidates(self, tmp_path, monkeypatch):
        """人工编辑走的是编辑栈而非 IR 文件——它变了同样要重算。"""
        p = self._seed(tmp_path, monkeypatch, [{"name": "订单", "table": "fact_order"}])
        inv = ({"fact_order"}, set())
        sig = server._stat_sig(str(p))
        server._graph_row("built_cachetest", "built", [str(p)], inv, sig)

        ep = server._edits_path("built_cachetest")
        os.makedirs(os.path.dirname(ep), exist_ok=True)
        with open(ep, "w") as fp:
            json.dump({"ops": []}, fp)
        try:
            calls = []
            real = server.load_ir_edited
            monkeypatch.setattr(server, "load_ir_edited",
                                lambda k: (calls.append(k), real(k))[1])
            server._graph_row("built_cachetest", "built", [str(p)], inv, sig)
            assert calls == ["built_cachetest"]      # 确实重算了
        finally:
            os.path.exists(ep) and os.unlink(ep)

    def test_missing_source_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "WORK", str(tmp_path))
        server._GRAPH_ROW_CACHE.clear()
        assert server._graph_row("built_nope", "built", [str(tmp_path / "x.json")],
                                 (set(), set()), ()) is None
