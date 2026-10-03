"""问数上下文的列说明(DR-077):问题提到的列排在前面,低基数列带上库中实际取值,避免模型猜筛选值。"""
from __future__ import annotations

import server


def _wide_object():
    # fact_production_order 的 status 排在第 13 列;只给前 6 列时问题相关列会被截掉
    cols = [r[1] for r in server.ro_connect(server.DB).execute('PRAGMA table_info("fact_production_order")')]
    return {"table": "fact_production_order", "cn": "生产工单",
            "attrs": [{"col": c, "cn": {"status": "工单状态", "end_time": "结束时间"}.get(c, "")} for c in cols]}


def test_low_cardinality_columns_carry_actual_values():
    text = server._obj_cols_text(_wide_object(), question="2025 年完工工单的产量")
    assert "status(工单状态;取值:" in text
    for value in ("已完成", "生产中", "待生产"):
        assert value in text


def test_question_columns_survive_the_column_limit():
    text = server._obj_cols_text(_wide_object(), limit=4, question="各工单的状态分布")
    assert text.split(", ")[0].startswith("status(")          # 问题提到「状态」,该列排第一
    assert len(text.split(", ")) == 4


def test_key_and_high_cardinality_columns_get_no_values():
    text = server._obj_cols_text(_wide_object(), question="")
    for chunk in text.split(", "):
        if chunk.startswith(("order_id(", "batch_no(", "actual_quantity(")):
            assert "取值" not in chunk


def test_missing_attribute_names_fall_back_to_in_db_dictionary():
    obj = _wide_object()
    for a in obj["attrs"]:
        a["cn"] = ""                                         # 模型抽取的属性常常没有中文名
    text = server._obj_cols_text(obj, limit=4, question="各工单的状态分布")
    assert text.split(", ")[0].startswith("status(工单状态;取值:")      # 中文名取自 dim_column_metadata


def test_column_whose_value_is_named_in_question_ranks_first():
    # 问题只说出取值「已完成」、没说「状态」:取值所在的列也要排第一(「缺勤」是出勤结果的取值,同理)
    obj = _wide_object()
    for a in obj["attrs"]:
        a["cn"] = ""
    text = server._obj_cols_text(obj, limit=3, question="2025 年已完成的有多少?")
    assert text.split(", ")[0].startswith("status(")


def test_related_lookup_table_named_in_question_is_recalled():
    """命中表经编码列引用的码表优先进上下文:前 8 张命中表被设备类表占满、设备主表的邻居关键词得分更高时,
    停机记录 reason_code(只有 8 个取值)指向的「停机原因」表仍要进来,否则模型只能猜原因编码。"""
    import json
    import os
    hits = [("dim_equipment", "设备"), ("fact_downtime", "停机记录"), ("DWS_EQUIPMENT_DAILY", "设备日汇总"),
            ("fact_equipment_runtime", "设备运行记录"), ("dim_equipment_type", "设备类型"), ("fact_alarm", "设备告警"),
            ("fact_maintenance_plan", "设备维护计划"), ("fact_maintenance_record", "设备维护记录"),
            ("fact_maintenance_wo", "设备维护工单")]
    plain = [("dim_workshop", "车间"), ("dim_fixed_asset", "固定资产"), ("dim_spare_part", "备件"),
             ("dim_production_line", "产线"), ("dim_downtime_reason", "停机原因")]
    busy = [{"col": c} for c in ("equipment_id", "run_hours", "downtime_min")]   # 这些表各命中 3 个英文扩展词,分数高于停机原因表
    objs = [{"name": t, "table": t, "cn": cn, "attrs": busy if (t, cn) in hits[2:] else [{"col": "equipment_id"}]}
            for t, cn in hits + plain]                                     # 设备主表的邻居也各命中 1 个词
    objs[-1]["attrs"] = [{"col": "reason_id"}, {"col": "reason_name"}]
    rel = lambda a, b: {"source_concept": a, "target_concept": b, "status": "verified"}   # noqa: E731
    rels = [rel("dim_equipment", t) for t, _ in plain[:4]] + [rel("fact_downtime", "dim_downtime_reason")]
    rels[-1]["evidence"] = {"child_key": "reason_code", "parent_key": "reason_id"}
    path = os.path.join(server.WORK, "built_ctxrank.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"objects": objs, "relations": rels}, f, ensure_ascii=False)
    try:
        ctx = server.build_context("设备故障导致的停机总时长是多少小时?", graph_keys=["built_ctxrank"])
    finally:
        os.remove(path)
    assert "表 dim_downtime_reason(" in ctx


def test_glossary_term_naming_a_table_outranks_generic_word_hits():
    """术语词典把问句里的「安全事件」直接对应到 fact_incident:这张表要排在只命中「事件」「event」等泛词的表前面。
    业务库的表中文名常写「事故」,与问句用语不同,只靠中文名匹配召回不到。"""
    import json
    import os
    noisy = [("fact_production_order", "生产事件单"), ("fact_quality_inspection", "质量事件"), ("fact_alarm", "设备事件"),
             ("fact_downtime", "停机事件"), ("fact_maintenance_wo", "维护事件"), ("fact_maintenance_record", "维护记录事件"),
             ("fact_equipment_runtime", "运行事件"), ("fact_maintenance_plan", "计划事件")]
    objs = [{"name": t, "table": t, "cn": cn, "attrs": [{"col": "event_type"}, {"col": "safety_flag"}]} for t, cn in noisy]
    objs.append({"name": "fact_incident", "table": "fact_incident", "cn": "事故", "attrs": [{"col": "severity"}]})
    path = os.path.join(server.WORK, "built_ctxterm.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"objects": objs, "relations": []}, f, ensure_ascii=False)
    try:
        ctx = server.build_context("各类安全事件(按严重程度)各发生了多少起?", graph_keys=["built_ctxterm"])
    finally:
        os.remove(path)
    assert "表 fact_incident(" in ctx


def test_values_and_names_come_from_the_graph_source_database(make_sqlite):
    """本体来自登记的 SQLite 数据源时,问数在该库上执行:取值与字段中文名也要从该库读,
    不能读主库里同名表的取值(两个库常有同名表,取值不同)。"""
    path = make_sqlite({
        "fact_production_order": ("order_id INTEGER, status TEXT", [(1, "RELEASED"), (2, "CLOSED")]),
        "dim_column_metadata": ("table_name TEXT, column_name TEXT, column_name_cn TEXT",
                                [("fact_production_order", "status", "单据状态")]),
    }, fname="source.db")
    token = server._QA_DB.set(path)
    try:
        text = server._obj_cols_text({"table": "fact_production_order", "attrs": [{"col": "status"}]})
    finally:
        server._QA_DB.reset(token)
    assert text == "status(单据状态;取值:CLOSED/RELEASED)"


def test_table_holding_a_code_named_in_question_ranks_first():
    """问句按编号问(「PA66-GF30 的平均成交单价」):编号是产品表名称列里的取值,没有词指向产品表。
    库里查到该取值的表要排在前面,否则物料表等同样命中「PA66」的表抢先,模型按物料关联销售订单而被拦下。"""
    tr: dict = {}
    server.build_context("PA66-GF30 的平均成交单价是多少?", trace=tr)
    tables = [o["table"] for o in tr["objects"]]
    assert tables.index("dim_product") < tables.index("dim_material")
    assert any("PA66-GF30" in h for o in tr["objects"] if o["table"] == "dim_product" for h in o.get("hits") or [])
