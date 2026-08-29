# -*- coding: utf-8 -*-
import action_ontology
import server


def _ir():
    return {
        "scenario": {"name": "制造本体"},
        "objects": [
            {"name": "dim_equipment", "cn": "设备", "kind": "object",
             "table": "dim_equipment", "tables": ["dim_equipment"]},
        ],
        "relations": [],
    }


def _actions():
    return [{
        "id": "report_repair", "cn": "设备报修", "object": "设备",
        "object_table": "dim_equipment", "risk": "low", "enabled": True,
        "source": "本体动作 · 报修", "desc": "登记维修申请",
        "params": [{"name": "equipment", "type": "text", "required": True}],
        "effects": [{"type": "append_event", "event": "维修申请已登记"}],
    }]


def test_registered_action_becomes_first_class_ir_node_and_binding():
    ir = _ir()
    stats = action_ontology.project_registered_actions(ir, _actions())
    action = next(obj for obj in ir["objects"] if obj["kind"] == "action")
    assert stats == {
        "source": "action_registry", "added_nodes": 1, "enriched_nodes": 0,
        "added_relations": 1, "execution_mode": "decision_capture", "real_writeback": False,
    }
    assert action["name"] == "action__report_repair"
    assert action["bfo"] == "PlannedProcess"
    assert action["action_spec"]["invocable"] is True
    assert action["action_spec"]["real_writeback"] is False
    assert action["action_spec"]["binding_status"] == "configured"
    assert action["action_spec"]["target_objects"] == ["dim_equipment"]
    relation = ir["relations"][0]
    assert relation["target_concept"] == "dim_equipment"
    assert relation["verb"] == "作用于"
    assert relation["status"] == "candidate"
    assert relation["evidence_status"] == "configured"
    assert ir["scenario"]["action_count"] == 1


def test_projection_is_idempotent_and_skips_disabled_actions():
    ir = _ir()
    actions = _actions() + [{"id": "disabled", "enabled": False}]
    action_ontology.project_registered_actions(ir, actions)
    stats = action_ontology.project_registered_actions(ir, actions)
    assert len([obj for obj in ir["objects"] if obj.get("kind") == "action"]) == 1
    assert len(ir["relations"]) == 1
    assert stats["added_nodes"] == 0
    assert stats["added_relations"] == 0
    assert stats["enriched_nodes"] == 1


def test_unbound_action_is_kept_but_marked_unbound_without_fake_relation():
    ir = _ir()
    action = _actions()[0]
    action["object"] = "不存在对象"
    action["object_table"] = "missing_table"
    action_ontology.project_registered_actions(ir, [action])
    node = next(obj for obj in ir["objects"] if obj.get("kind") == "action")
    assert node["action_spec"]["binding_status"] == "unbound"
    assert node["action_spec"]["target_objects"] == []
    assert ir["relations"] == []


def test_unknown_risk_defaults_to_high_and_requires_approval():
    ir = _ir()
    action = _actions()[0]
    action["risk"] = "unknown"
    action_ontology.project_registered_actions(ir, [action])
    spec = next(obj for obj in ir["objects"] if obj.get("kind") == "action")["action_spec"]
    assert spec["risk"] == "high"
    assert spec["approval_required"] is True


def test_graph_projection_keeps_action_binding_for_ui_invocation():
    ir = _ir()
    action_ontology.project_registered_actions(ir, _actions())
    graph = server.ir_to_graph("built_test", ir)
    node = next(item for item in graph["nodes"] if item["kind"] == "action")
    assert node["action_id"] == "report_repair"
    assert node["action_spec"]["execution_mode"] == "decision_capture"
    edge = next(item for item in graph["edges"] if item["s"] == node["id"])
    assert edge["evidence_status"] == "configured"
    assert edge["semantic_status"] == "not_reviewed"
