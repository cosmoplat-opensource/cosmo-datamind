"""Isolated ledgers with independently countable records for API and browser tests."""
import json

import pytest

import bp_actions
import server
import srv_actions


@pytest.fixture
def action_ledger(tmp_path, monkeypatch):
    path = tmp_path / "actions.json"
    types = tmp_path / "types.json"
    types.write_text(json.dumps([{"id": "act_scope", "cn": "班次登记", "enabled": True,
                                 "risk": "low", "params": [], "effects": []}]), encoding="utf-8")
    monkeypatch.setattr(srv_actions, "ACTION_TYPES_F", str(types))
    for module in (bp_actions, srv_actions):
        monkeypatch.setattr(module, "ACTION_LOG_F", str(path))
    monkeypatch.setenv("DATAMIND_ACTION_EXECUTORS", "{}")

    def seed(completed):
        def entry(i, status):
            return {"id": f"evt_{i}", "action_id": "act_scope", "action_cn": f"班次 {i}",
                    "status": status, "operator": f"工位 {i}", "params": {}, "effects": [],
                    "ts": "2026-01-01 09:00"}
        items = [entry(i, "executed") for i in range(completed, 0, -1)] + [entry(0, "pending")]
        path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        return path

    return seed


@pytest.fixture
def review_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK", str(tmp_path))

    def create(key, label, size):
        ids = [f"unit_{i:03d}" for i in range(1, size + 1)]
        objects = [{"id": n, "name": n, "cn": label + n} for n in ids + ["plant"]]
        links = [{"source": n, "target": "plant", "verb": label, "status": "candidate"} for n in ids]
        ir = {"scenario": {"name": label}, "objects": objects, "links": links}
        (tmp_path / (key + ".json")).write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")
        return key

    return create
