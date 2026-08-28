# -*- coding: utf-8 -*-
"""定义质量评分单测(DR-040)。

结构维度(属加种差/非循环/反例/实义)+ 参考式(与参考术语表 token 重叠),
全确定性、离线。与元数据覆盖统计(测「有没有定义」)正交——本模块测「定义好不好」。
"""
import definition_eval as de


class TestStructural:
    def test_good_genus_differentia_scores_high(self):
        r = de.score_definition("客户", "与企业进行产品或服务交易的外部组织或个人。",
                                counter_example="供应商(向企业提供资源而非购买产品的对象)")
        assert r["score"] >= 0.9
        assert r["dims"]["genus_differentia"] == 1.0
        assert r["dims"]["counter_example"] == 1.0

    def test_empty_definition_scores_low(self):
        r = de.score_definition("客户", "")
        assert r["dims"]["present"] == 0.0
        assert r["score"] < 0.3
        assert any("缺失" in i for i in r["issues"])

    def test_empty_definition_earns_no_structural_credit(self):
        # 不存在的定义不得因「不循环」白拿分:无定义时结构维度一律 0,总分 0
        r = de.score_definition("客户", "")
        assert r["dims"]["non_circular"] == 0.0
        assert r["dims"]["genus_differentia"] == 0.0
        assert r["score"] == 0.0

    def test_circular_definition_flagged(self):
        r = de.score_definition("客户", "客户是客户。")
        assert r["dims"]["non_circular"] == 0.0
        assert any("循环" in i for i in r["issues"])

    def test_missing_counter_example_costs_a_dimension(self):
        with_ce = de.score_definition("销售订单", "记录客户确认购买产品的契约性单据。",
                                      counter_example="报价单(仅价格意向)")
        no_ce = de.score_definition("销售订单", "记录客户确认购买产品的契约性单据。")
        assert with_ce["score"] > no_ce["score"]
        assert no_ce["dims"]["counter_example"] == 0.0

    def test_copula_form_also_recognized(self):
        # 系词式「X 是……」也算属加种差
        r = de.score_definition("泵", "是一种通过机械作用输送流体的机械设备。",
                                counter_example="阀门(只控制通断不提供动力)")
        assert r["dims"]["genus_differentia"] == 1.0


class TestReference:
    def test_reference_overlap_raises_score(self):
        gold = "工单计划生产数量合计"
        close = de.score_definition("计划产量", "指工单计划生产的数量合计。",
                                    counter_example="实际产量(实际完成数)", gold=gold)
        far = de.score_definition("计划产量", "指工单计划生产的数量合计。",
                                  counter_example="实际产量(实际完成数)", gold="与生产完全无关的另一个概念表述")
        assert close["dims"]["reference"] > far["dims"]["reference"]


class TestJudgeLayer:
    def test_judge_lowers_score_for_vacuous_but_structural(self):
        # 结构成立但空洞的定义:结构分高,但 LLM-judge 语义分低 → 总分被拉下,并给 issue
        vacuous = "与某些事物相关的一类通用的对象实体。"   # 属加种差形式成立,但空洞
        without = de.score_definition("设备", vacuous, counter_example="非设备(反例)")
        with_judge = de.score_definition("设备", vacuous, counter_example="非设备(反例)",
                                         judge=lambda t, d: 0.2)
        assert with_judge["score"] < without["score"]
        assert with_judge["dims"]["semantic"] == 0.2
        assert any("语义" in i for i in with_judge["issues"])

    def test_judge_exception_falls_back_gracefully(self):
        def bad_judge(t, d):
            raise RuntimeError("engine offline")
        r = de.score_definition("客户", "与企业进行交易的外部组织或个人。",
                                counter_example="供应商(提供资源方)", judge=bad_judge)
        assert "semantic" not in r["dims"]   # judge 异常 → 退回确定性分,不臆造


class TestBatch:
    def test_score_ontology_aggregates(self):
        ir = {"objects": [
            {"cn": "客户", "definition": "与企业进行交易的外部组织或个人。",
             "counterExample": "供应商(提供资源方)"},
            {"cn": "废弃对象", "definition": "", "counterExample": ""},
        ]}
        rep = de.score_ontology(ir)
        assert rep["scored"] == 2
        assert 0.0 <= rep["mean_score"] <= 1.0
        assert rep["weak"] >= 1        # 空定义那条应计入待改进
