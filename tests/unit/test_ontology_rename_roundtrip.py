"""已支持的两种本体格式都应能从图谱取得对象、按其标识改名并撤销。"""
import json
import sys
from types import SimpleNamespace

import pytest

import server


def upstream_rename(ir, op):
    # serve_claw 的公开编辑契约按对象 id 查找，更新 cn。日常 CI 不依赖外部引擎安装。
    oid = op["target"].removeprefix("obj:")
    obj = next(row for row in ir["objects"] if row["id"] == oid)
    obj["cn"] = op["params"]["cn"]


@pytest.mark.parametrize("shape", ["links", "relations"], ids=["upstream-id", "built-name"])
def test_displayed_object_can_be_renamed_reloaded_and_restored(shape, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    monkeypatch.setitem(sys.modules, "serve_claw", SimpleNamespace(ALLOWED_OPS=("rename",), apply_op=upstream_rename))
    key = "built_rename_test"
    obj = {"name": "Order", "cn": "原始订单", "table": "audit_order"}
    if shape == "links":
        obj["id"] = "order-1"
    path = tmp_path / (key + ".json")
    path.write_text(json.dumps({"objects": [obj], shape: []}), encoding="utf-8")
    original = path.read_bytes()
    client = server.app.test_client()
    graph = "/api/graph/" + key
    first, = client.get(graph).get_json()["nodes"]
    assert first["name"] == "原始订单"
    op = {"op": "rename", "target": "obj:" + first["id"], "params": {"cn": "已复核订单"}}
    response = client.post("/api/ont/apply", json={"graph": key, "op": op})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["ok"]
    renamed, = client.get(graph).get_json()["nodes"]
    assert (renamed["id"], renamed["name"]) == (first["id"], "已复核订单")
    undone = client.post("/api/ont/undo", json={"graph": key})
    assert undone.status_code == 200 and undone.get_json()["ok"]
    assert client.get(graph).get_json()["nodes"] == [first]
    assert path.read_bytes() == original
