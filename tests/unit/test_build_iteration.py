# -*- coding: utf-8 -*-
"""构建对话在已有本体上迭代:合并规则与「无法裁决」的成因诊断。

背景:构建对话原先每轮都新建一张图,用户提「补上客户与工单的关系」会得到一张
互不相干的新图,上一轮的人审成果无从延续。合并必须守住两条:已确立的结论不被
新一轮覆盖;新增只做补充、不做删除。
"""
import json

import pytest

server = pytest.importorskip("server")


def _rel(s, t, status, verb="关联", **kw):
    r = {"source_concept": s, "target_concept": t, "verb": verb,
         "status": status, "evidence_status": status}
    r.update(kw)
    return r


def _base():
    return {"scenario": {"name": "底本", "object_count": 2, "relation_count": 1},
            "objects": [{"name": "订单", "cn": "订单", "table": "fact_order",
                         "definition": "人工写好的定义", "attrs": [{"col": "id"}]},
                        {"name": "客户", "cn": "客户", "table": "dim_customer"}],
            "relations": [_rel("订单", "客户", "verified", overlap=97.0, note="原有数据证据")]}


class TestMergeKeepsEstablishedConclusions:
    def test_verified_not_downgraded_by_new_candidate(self):
        """模型本轮把同一条关系只提为 candidate,不得把已有 verified 打回去。"""
        new = {"objects": [], "relations": [_rel("订单", "客户", "candidate", note="本轮仅提议")]}
        out, stat = server._merge_ir(_base(), new)
        rel = out["relations"][0]
        assert rel["status"] == "verified"
        assert rel["note"] == "原有数据证据"          # 连证据说明都不该被覆盖
        assert stat["relations_kept"] == 1 and stat["relations_upgraded"] == 0

    def test_human_asserted_outranks_new_verified(self):
        """人审断言高于数据证据:asserted 不因本轮 verified 而被改写。"""
        base = _base()
        base["relations"][0].update(status="asserted", evidence_status="asserted", note="人审确认")
        new = {"objects": [], "relations": [_rel("订单", "客户", "verified", note="本轮数据证据")]}
        out, _ = server._merge_ir(base, new)
        assert out["relations"][0]["status"] == "asserted"
        assert out["relations"][0]["note"] == "人审确认"

    def test_stronger_evidence_upgrades(self):
        """本轮拿到更强证据时才覆盖:candidate → verified。"""
        base = _base()
        base["relations"][0].update(status="candidate", evidence_status="candidate")
        new = {"objects": [], "relations": [_rel("订单", "客户", "verified", overlap=99.0)]}
        out, stat = server._merge_ir(base, new)
        assert out["relations"][0]["status"] == "verified"
        assert stat["relations_upgraded"] == 1

    def test_absent_in_new_round_is_never_deleted(self):
        """模型本轮没提到的对象与关系一律保留——删除只能走可撤销的人审编辑。"""
        out, _ = server._merge_ir(_base(), {"objects": [], "relations": []})
        assert {o["name"] for o in out["objects"]} == {"订单", "客户"}
        assert len(out["relations"]) == 1


class TestMergeAddsAndEnriches:
    def test_new_object_and_relation_appended(self):
        new = {"objects": [{"name": "工单", "cn": "工单", "table": "fact_work_order"}],
               "relations": [_rel("工单", "订单", "candidate")]}
        out, stat = server._merge_ir(_base(), new)
        assert {o["name"] for o in out["objects"]} == {"订单", "客户", "工单"}
        assert stat["objects_added"] == 1 and stat["relations_added"] == 1
        assert out["scenario"]["object_count"] == 3 and out["scenario"]["relation_count"] == 2

    def test_only_empty_fields_are_filled(self):
        """补空不覆盖:已有定义保住,缺失的 cn/definition 才由本轮补上。"""
        new = {"objects": [{"name": "订单", "definition": "模型新写的定义"},
                           {"name": "客户", "definition": "客户的定义"}],
               "relations": []}
        out, stat = server._merge_ir(_base(), new)
        by = {o["name"]: o for o in out["objects"]}
        assert by["订单"]["definition"] == "人工写好的定义"     # 不被覆盖
        assert by["客户"]["definition"] == "客户的定义"         # 原本为空,补上
        assert stat["objects_enriched"] == 1

    def test_iteration_counter_increments(self):
        out, _ = server._merge_ir(_base(), {"objects": [], "relations": []})
        assert out["scenario"]["iterations"] == 2
        out2, _ = server._merge_ir(out, {"objects": [], "relations": []})
        assert out2["scenario"]["iterations"] == 3

    def test_base_is_not_mutated(self):
        """合并是纯函数:底本对象不得被就地改写。"""
        base = _base()
        snapshot = json.dumps(base, ensure_ascii=False, sort_keys=True)
        server._merge_ir(base, {"objects": [{"name": "新增"}], "relations": []})
        assert json.dumps(base, ensure_ascii=False, sort_keys=True) == snapshot

    def test_malformed_entries_are_skipped(self):
        """缺名称/缺端点的条目跳过,不制造匿名节点或半截边。"""
        new = {"objects": [{"cn": "无名"}, {"name": ""}],
               "relations": [_rel("订单", "", "candidate"), _rel("", "客户", "candidate")]}
        out, stat = server._merge_ir(_base(), new)
        assert stat["objects_added"] == 0 and stat["relations_added"] == 0
        assert len(out["objects"]) == 2 and len(out["relations"]) == 1
