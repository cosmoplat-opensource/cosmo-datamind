# -*- coding: utf-8 -*-
"""JSON store 抽象单测(DR-044)。

补审计点名的空白:_atomic_json 是安全叙事却无负向持久化测试
(坏文件恢复 / 原子写不留残片 / 并发写)。本模块把这些变成回归锁。
"""
import json
import threading
import pytest
import store


def test_load_missing_returns_default(tmp_path):
    s = store.JsonStore(str(tmp_path / "x.json"), default={"a": 1})
    assert s.load() == {"a": 1}


def test_save_then_load_roundtrip(tmp_path):
    s = store.JsonStore(str(tmp_path / "x.json"), default={})
    s.save({"k": "值", "n": 3})
    assert s.load() == {"k": "值", "n": 3}


def test_corrupt_file_recovers_to_default(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{ this is not valid json ", encoding="utf-8")
    s = store.JsonStore(str(p), default={"safe": True})
    assert s.load() == {"safe": True}   # 坏文件不崩,退回 default


def test_atomic_write_leaves_no_tmp(tmp_path):
    s = store.JsonStore(str(tmp_path / "x.json"), default={})
    s.save({"k": 1})
    leftovers = [f.name for f in tmp_path.iterdir() if f.name != "x.json"]
    assert leftovers == []   # 无 .tmp 残片


def test_validate_rejects_bad_shape(tmp_path):
    def must_be_list(d):
        return isinstance(d, list)
    s = store.JsonStore(str(tmp_path / "x.json"), default=[], validate=must_be_list)
    with pytest.raises(ValueError):
        s.save({"not": "a list"})
    assert s.load() == []   # 拒绝后旧值不被破坏


def test_migrate_runs_on_load(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"v": 1}), encoding="utf-8")

    def migrate(d):
        if d.get("v") == 1:
            d = {**d, "v": 2, "migrated": True}
        return d
    s = store.JsonStore(str(p), default={}, migrate=migrate)
    assert s.load()["v"] == 2 and s.load()["migrated"] is True


def test_concurrent_update_is_atomic(tmp_path):
    s = store.JsonStore(str(tmp_path / "c.json"), default={"n": 0})

    def bump():
        for _ in range(50):
            s.update(lambda d: {"n": d["n"] + 1})
    threads = [threading.Thread(target=bump) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert s.load()["n"] == 200   # 4×50,无丢更新 → 锁生效
