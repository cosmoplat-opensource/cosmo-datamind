# -*- coding: utf-8 -*-
"""rule_engine 单测(DR-028)—— 确定性决策层:求值/追溯/冲突/一致性。"""
import rule_engine


class TestValidateRule:
    def test_valid_rule_passes(self, rules_amount):
        assert rule_engine.validate_rule(rules_amount[0]) is None

    def test_bad_operator_rejected(self):
        r = {"id": "r1", "cn": "x", "on": "o",
             "when": [{"field": "a", "op": "≈", "value": 1}],
             "then": {"decision": "d"}}
        assert "运算符" in rule_engine.validate_rule(r)

    def test_missing_decision_rejected(self):
        r = {"id": "r1", "cn": "x", "on": "o",
             "when": [{"field": "a", "op": ">=", "value": 1}], "then": {}}
        assert "decision" in rule_engine.validate_rule(r)

    def test_numeric_compare_not_string_trap(self):
        # "100" >= "99" 作字符串会判 False;引擎须按数值比
        r = {"id": "r1", "cn": "x", "on": "o",
             "when": [{"field": "amt", "op": ">=", "value": 99}], "then": {"decision": "d"}}
        res = rule_engine.evaluate([r], "o", {"amt": "100"})
        assert res["fired_count"] == 1


class TestEvaluate:
    def test_fires_with_trace(self, rules_amount):
        res = rule_engine.evaluate(rules_amount, "sales_order", {"amount": 250000})
        assert res["fired_count"] == 1
        f = res["fired"][0]
        assert f["decision"] == "需总监审批"
        # 每条结论必带可回溯 trace(字段/运算/阈值/实际值)
        assert f["trace"][0]["field"] == "amount"
        assert f["trace"][0]["actual"] == 250000

    def test_not_fired_when_below_threshold(self, rules_amount):
        res = rule_engine.evaluate(rules_amount, "sales_order", {"amount": 5000})
        assert res["fired_count"] == 0

    def test_object_scope_filters(self, rules_amount):
        # on 不匹配的对象不参与求值
        res = rule_engine.evaluate(rules_amount, "other_obj", {"amount": 999999})
        assert res["fired_count"] == 0

    def test_conflict_same_action_different_decision(self):
        rules = [
            {"id": "a", "cn": "A", "on": "o", "when": [{"field": "x", "op": ">=", "value": 1}],
             "then": {"decision": "批准", "action": "act"}},
            {"id": "b", "cn": "B", "on": "o", "when": [{"field": "x", "op": ">=", "value": 1}],
             "then": {"decision": "拒绝", "action": "act"}},
        ]
        res = rule_engine.evaluate(rules, "o", {"x": 5})
        # 同动作两个相反结论 → 报冲突,绝不静默择一
        assert len(res["conflicts"]) == 1
        assert set(res["conflicts"][0]["decisions"]) == {"批准", "拒绝"}


class TestConsistency:
    def test_duplicate_id_flagged(self, rules_amount):
        dup = rules_amount + [dict(rules_amount[0])]
        res = rule_engine.consistency_check(dup)
        assert any(i["type"] == "duplicate_id" for i in res["issues"])

    def test_contradiction_same_cond_diff_decision(self):
        rules = [
            {"id": "a", "cn": "A", "on": "o", "when": [{"field": "x", "op": ">=", "value": 1}],
             "then": {"decision": "批准"}},
            {"id": "b", "cn": "B", "on": "o", "when": [{"field": "x", "op": ">=", "value": 1}],
             "then": {"decision": "拒绝"}},
        ]
        res = rule_engine.consistency_check(rules)
        assert res["healthy"] is False
        assert any(i["type"] == "contradiction" for i in res["issues"])
