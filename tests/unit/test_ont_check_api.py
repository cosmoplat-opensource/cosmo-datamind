# -*- coding: utf-8 -*-
"""对外的 SQL 本体检查接口(DR-134):外部智能体在执行前提交一条 SQL,得到与问数同一套校验的结论和依据。

只检查、不执行;结论与 /api/chat 执行前的本体约束校验一致。"""
import server

H = {"X-DataMind-Source": "demo"}


def _check(sql, graphs=("demo",), **extra):
    return server.app.test_client().post("/api/ont/check", headers=H, json={"sql": sql, "graphs": list(graphs), **extra})


def test_a_join_on_a_verified_relation_passes_and_names_the_relation():
    r = _check("SELECT d.dept_name, COUNT(*) FROM dim_employee e JOIN dim_department d ON e.dept_id = d.dept_id GROUP BY 1")
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] is True and body["reason"] == ""
    assert body["tables"] == ["dim_department", "dim_employee"] and body["parsed"] is True
    assert body["joins"] == [{"left": "dim_employee.dept_id", "right": "dim_department.dept_id", "relation": "verified"}]


def test_an_unverified_join_and_a_fan_out_are_rejected_with_the_reason():
    body = _check("SELECT e.emp_id FROM dim_employee e JOIN dim_department d ON e.emp_id = d.region_id").get_json()
    assert body["ok"] is False and "JOIN 键" in body["reason"]
    assert body["joins"] == [{"left": "dim_employee.emp_id", "right": "dim_department.region_id", "relation": None}]
    body = _check("SELECT SUM(d.dept_id) FROM dim_employee e JOIN dim_department d ON e.dept_id = d.dept_id").get_json()
    assert body["ok"] is False and "扇出风险" in body["reason"]


def test_missing_tables_columns_and_writes_are_reported_without_running_anything():
    assert "不存在" in _check("SELECT nope FROM dim_employee").get_json()["reason"]
    body = _check("DELETE FROM dim_employee").get_json()
    assert body["ok"] is False and "只读" in body["reason"]


def test_the_selected_tables_narrow_the_scope():
    body = _check("SELECT COUNT(*) FROM dim_employee", tables=["dim_department"]).get_json()
    assert body["ok"] is False and "dim_employee" in body["reason"]


def test_bad_requests_are_rejected():
    c = server.app.test_client()
    assert c.post("/api/ont/check", headers=H, json={"graphs": ["demo"]}).status_code == 400
    assert c.post("/api/ont/check", headers=H, json={"sql": "SELECT 1", "graphs": ["../x"]}).status_code == 400
    assert c.post("/api/ont/check", headers=H, json={"sql": "x" * 20001, "graphs": ["demo"]}).status_code == 400


def test_without_graphs_or_a_source_header_the_default_ontology_is_used():
    # MCP 工具 check_sql 不带来源头、也可以不带 graphs:应按当前数据库的默认本体检查,而不是返回 422
    r = server.app.test_client().post("/api/ont/check", json={"sql": "SELECT COUNT(*) FROM dim_employee"})
    assert r.status_code == 200 and r.get_json()["ok"] is True and r.get_json()["graphs"] == ["demo"]


def test_the_strongest_status_wins_when_several_relations_share_a_key_pair():
    def ir(*statuses):
        return {"objects": [{"id": "a", "table": "ta"}, {"id": "b", "table": "tb"}],
                "relations": [{"source_concept": "a", "target_concept": "b", "status": s,
                               "evidence": {"child_key": "k", "parent_key": "k"}} for s in statuses]}
    key = ("ta", "k", "tb", "k")
    assert server._relation_status_index(ir("candidate", "rejected"))[key] == "candidate"
    assert server._relation_status_index(ir("asserted", "verified"))[key] == "verified"
    assert server._relation_status_index(ir("rejected", "asserted"))[key] == "asserted"
