# -*- coding: utf-8 -*-
"""行业参照/本体标准从请求到注释、质量门的确定性契约。"""
import pytest

import build_quality
import build_references as refs
import server
import agent_runtime


def profile(industry="none", standard="none", industry_mode="reference",
            standard_mode="reference"):
    return refs.normalize({
        "industry": {"id": industry, "mode": industry_mode},
        "ontology_standard": {"id": standard, "mode": standard_mode},
    })


def test_catalog_has_optional_industries_standards_and_legacy_default():
    data = refs.catalog()
    assert {x["id"] for x in data["industries"]} >= {"none", "manufacturing", "chemical", "pcba"}
    assert {x["id"] for x in data["ontology_standards"]} >= {"none", "bfo_iof", "isa95", "ufo"}
    assert data["default"]["industry"]["id"] == "none"
    assert data["default"]["ontology_standard"]["id"] == "bfo_iof"
    assert all(x["asset"]["ready"] for x in data["ontology_standards"])


def test_explicit_none_differs_from_legacy_omission():
    explicit = refs.normalize({"industry": "none", "ontology_standard": "none"},
                              legacy_default=True)
    omitted = refs.normalize(None, legacy_default=True)
    assert explicit["ontology_standard"]["selected"] is False
    assert omitted["ontology_standard"]["id"] == "bfo_iof"
    assert omitted["ontology_standard"]["mode"] == "reference"


@pytest.mark.parametrize("value,message", [
    ({"industry": "unknown"}, "未知的 industry"),
    ({"ontology_standard": {"id": "bfo_iof", "mode": "maybe"}}, "mode"),
    ([], "references 必须是对象"),
])
def test_invalid_configuration_is_rejected(value, message):
    with pytest.raises(ValueError, match=message):
        refs.normalize(value)


def test_prompt_uses_selected_reference_but_none_does_not_smuggle_bfo_iof():
    constrained = refs.prompt_block(profile("pcba", "isa95", "constraint", "reference"))
    assert "PCBA / SMT" in constrained and "核心检查项" in constrained
    assert "严禁为凑模板编造" in constrained
    assert "ISA-95 / IEC 62264" in constrained and "候选类别" in constrained
    assert "本地机器可读资产已加载" in constrained and "MESA B2MML" in constrained

    unselected = refs.prompt_block(profile())
    assert "显式不选择" in unselected
    assert "BFO 2020" not in unselected and "ISA-95" not in unselected


def test_server_extraction_prompt_obeys_explicit_none(monkeypatch):
    captured = {}

    class Runtime:
        model = "test-model"

        def run_turn(self, _sid, prompt_text, timeout):
            captured["prompt"] = prompt_text
            return True, '{"objects":[{"name":"local_record","cn":"本地记录","kind":"ice"}],"relations":[]}'

    monkeypatch.setattr(server, "_drv_order", lambda: ["test"])
    monkeypatch.setattr(server, "_rejected_patterns", lambda: [])
    monkeypatch.setattr(agent_runtime, "available", lambda: ["test"])
    monkeypatch.setattr(agent_runtime, "get_runtime", lambda _name: Runtime())
    result = server._llm_extract_ontology(
        "只按数据构建", {"docs": "", "refs": [], "schema": "TABLE local_record(id)",
                         "tab_cols": {"local_record": [("id", "INTEGER")]}, "n_docs": 0},
        [], references=profile(),
    )
    assert result["objects"][0]["name"] == "local_record"
    assert "显式不选择行业参照或本体标准" in captured["prompt"]
    assert "founded_relation" not in captured["prompt"]
    assert "借鉴 IOF" not in captured["prompt"]


def test_apply_profile_adds_industry_and_isa95_candidates_then_can_remove_them():
    ir = {
        "objects": [
            {"name": "fact_work_order", "cn": "生产工单", "kind": "ice"},
            {"name": "dim_machine", "cn": "生产设备", "kind": "asset"},
        ],
        "relations": [{"source_concept": "fact_work_order", "target_concept": "dim_machine",
                       "verb": "关联", "status": "candidate", "founded_relation": "hasInput"}],
    }
    refs.apply_profile(ir, profile("manufacturing", "isa95"))
    by_name = {obj["name"]: obj for obj in ir["objects"]}
    assert by_name["fact_work_order"]["industry_reference"]["concept"] == "work_order"
    assert by_name["fact_work_order"]["standard_alignment"]["candidate"] == "WorkOrder"
    assert by_name["dim_machine"]["standard_alignment"]["candidate"] == "Equipment"
    assert "bfo" not in by_name["dim_machine"]
    assert "founded_relation" not in ir["relations"][0]

    refs.apply_profile(ir, profile())
    assert all("industry_reference" not in obj and "standard_alignment" not in obj
               for obj in ir["objects"])
    assert "reused_from" not in by_name["fact_work_order"]


def test_bfo_profile_uses_validated_kind_and_relation_mapping():
    ir = {
        "objects": [
            {"name": "production", "kind": "event"},
            {"name": "product", "kind": "object"},
        ],
        "relations": [{"source_concept": "production", "target_concept": "product",
                       "verb": "产生", "status": "candidate"}],
    }
    refs.apply_profile(ir, profile(standard="bfo_iof"))
    assert ir["objects"][0]["bfo"] == "Process"
    assert ir["relations"][0]["founded_relation"] == "hasOutput"
    assert ir["relations"][0]["grounding_status"] == "mapped"


def test_explicit_non_bfo_selection_is_respected_by_graph_and_owl_export():
    chosen = profile("manufacturing", "isa95")
    ir = {
        "scenario": {"name": "制造测试"},
        "objects": [{"name": "machine", "cn": "生产设备", "kind": "asset",
                     "definition": "承担生产活动的物理装置"}],
        "relations": [],
        "build_manifest": {"references": chosen},
    }
    refs.apply_profile(ir, chosen)
    graph = server.ir_to_graph("built_test", ir)
    assert graph["nodes"][0]["bfo"] is None
    assert graph["nodes"][0]["standard_alignment"]["candidate"] == "Equipment"
    ttl = server._ir_to_turtle("built_test", ir)
    assert "spec.industrialontologies.org" not in ttl
    assert ':standardCandidate "isa95::Equipment"' in ttl


def test_industry_reference_is_review_but_constraint_is_blocking():
    source = {"objects": [{"name": "product", "cn": "产品", "kind": "object"}],
              "relations": []}
    soft = profile("manufacturing", "none", "reference")
    refs.apply_profile(source, soft)
    soft_report = refs.evaluate(source, soft)
    assert soft_report["review_queue"] and not soft_report["blocking_issues"]
    assert "生产工单" in soft_report["industry"]["missing_required"]

    hard = profile("manufacturing", "none", "constraint")
    refs.apply_profile(source, hard)
    hard_report = refs.evaluate(source, hard)
    assert hard_report["blocking_issues"]
    assert hard_report["industry"]["coverage"] < 1


def test_ufo_constraint_blocks_unknown_object_kind():
    chosen = profile(standard="ufo", standard_mode="constraint")
    ir = {"objects": [{"name": "known", "kind": "object"},
                      {"name": "unknown", "kind": "unexpected"}], "relations": []}
    refs.apply_profile(ir, chosen)
    report = refs.evaluate(ir, chosen)
    assert report["blocking_issues"][0]["type"] == "standard_objects_unclassified"
    assert report["ontology_standard"]["objects_aligned"] == 1


def test_missing_standard_asset_follows_reference_vs_constraint_mode(monkeypatch):
    monkeypatch.setattr(refs, "asset_trace", lambda _profile: {"id": "ufo", "ready": False})
    ir = {"objects": [{"name": "known", "kind": "object"}], "relations": []}
    soft = profile(standard="ufo")
    refs.apply_profile(ir, soft)
    soft_report = refs.evaluate(ir, soft)
    assert soft_report["review_queue"][0]["type"] == "standard_asset_unavailable"
    hard = profile(standard="ufo", standard_mode="constraint")
    hard_report = refs.evaluate(ir, hard)
    assert hard_report["blocking_issues"][0]["type"] == "standard_asset_unavailable"


def test_constraint_is_merged_into_build_quality_gate():
    ir = {
        "objects": [{"name": "misc", "cn": "任意记录", "kind": "ice",
                     "definition": "记录业务事实的信息对象", "counterExample": "物理设备"}],
        "relations": [],
    }
    hard = profile("pcba", "none", "constraint")
    refs.apply_profile(ir, hard)
    ir["build_manifest"] = {"references": hard}
    report = build_quality.evaluate(ir, ["任意记录是什么"])
    assert report["result"] == "fail"
    assert any(x["type"] == "industry_reference_gaps" for x in report["blocking_issues"])
    assert report["references"]["industry"]["id"] == "pcba"


def test_reference_catalog_endpoint_and_invalid_request_contract():
    client = server.app.test_client()
    response = client.get("/api/build/references")
    assert response.status_code == 200
    body = response.get_json()
    assert body["default"]["ontology_standard"]["id"] == "bfo_iof"
    assert response.headers["Cache-Control"] == "no-store"
    assert all(item["asset"]["ready"] for item in body["ontology_standards"])

    invalid = client.post("/api/build/inquire", json={
        "q": "测试", "references": {"industry": "not-real"},
    })
    assert invalid.status_code == 400
    assert invalid.get_json()["field"] == "references"


def test_ui_has_controls_payload_history_and_external_module():
    ui = (server.HERE + "/ui/index.html")
    text = open(ui, encoding="utf-8").read()
    module = open(server.HERE + "/ui/modules/build-references.js", encoding="utf-8").read()
    stream = open(server.HERE + "/ui/modules/build-stream.js", encoding="utf-8").read()
    assert all(token in text for token in (
        "bc_ref_industry", "bc_ref_standard", "bc_ref_industry_mode",
        "bc_ref_standard_mode", "bcRefsApply(d.references)",
        "/assets/modules/build-references.js", "/assets/modules/build-stream.js",
    ))
    assert "references,base_graph" in stream
    for select_id in ("bc_ref_industry", "bc_ref_standard"):
        start = text.index(f'id="{select_id}"')
        assert "加载中" not in text[start:text.index("</select>", start)]
    assert "目录加载中" not in text
    assert "window.bcRefsPayload" in module and "/api/build/references" in module
    assert all(token in module for token in (
        "fallbackCatalog", "manufacturing", "chemical", "pcba", "isa95", "ufo",
        "cache: 'no-store'", "重新加载目录", "installCatalog(fallbackCatalog",
    ))
