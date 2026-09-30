# -*- coding: utf-8 -*-
"""行为场景的步骤定义(BDD,DR-066)。

每个步骤只做一件事,通过 world(字典)在步骤间传递状态。步骤调用的都是生产代码的真实入口
(构建体检、代码契约、数据裁决、交叉复核、口径校验、问数与构建接口),不另写一套模拟逻辑。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import build_prep
import code_contracts
import concept_hints
import observability
import ontology_domain
from tests.bdd.gherkin import step

ROOT = Path(__file__).resolve().parents[2]
ENTITIES = ["pm_mo_base", "co_item", "co_area", "co_group", "wip_detail", "pack_sn_info", "wip_repair",
            "product_connect_log", "double_code_log", "co_route", "pm_project", "co_bom"]


def _server():
    import server
    return server


def _tables(world):
    return world.setdefault("tables", {})


def _db(world):
    if "db" not in world:
        world["db"] = world["make_sqlite"](_tables(world))
    return world["db"]


def _zip(text, name="mapper/X.xml"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, text)
    return buf.getvalue()


def _cross_check():
    path = ROOT / "skills_seed" / "ontology-cross-check" / "scripts" / "cross_check.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("_bdd_cross_check", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 构建体检 ─────────────────────────────────────────────────────────

@step(r"一个 (?P<n>\d+) 个厂区、两种命名约定的湖仓,每个厂区各有一份相同的 (?P<m>\d+) 个业务实体")
def lakehouse(world, n, m):
    world["factories"] = [(f"f{i + 1}", "t" if i < int(n) // 2 else "tb") for i in range(int(n))]
    for f, conv in world["factories"]:
        for e in ENTITIES[: int(m)]:
            _tables(world)[f"ods_{f}_{conv}_{e}"] = (f"id TEXT, insert_time TEXT, dt TEXT, {e}_key TEXT, {e}_name TEXT", [])
    _tables(world)["dwd_pm_mo_base"] = ("id TEXT, insert_time TEXT, dt TEXT, pm_mo_base_key TEXT, pm_mo_base_name TEXT", [])
    _tables(world)["dwd_f1_pm_mo_base_offline"] = ("id TEXT, insert_time TEXT, dt TEXT, pm_mo_base_key TEXT", [])


@step(r"另有 (?P<types>.+) 三种检测类型的明细表")
def test_types(world, types):
    for sub in types.split("、"):
        for f, _ in world["factories"]:
            _tables(world)[f"dwd_{f}_{sub}_factor_detail"] = (f"id TEXT, dt TEXT, project_id TEXT, {sub}_value TEXT", [])


@step(r"业务库的设备类型目录为:")
def device_types(world, table):
    rows = [(i, r[0], r[1], int(r[2])) for i, r in enumerate(table[1:], 1)]
    _tables(world)["meta_device_type"] = ("id INTEGER, dev_type_code TEXT, dev_type_name TEXT, is_del INTEGER", rows)


@step(r'设备实例表里有 (?P<a>\d+) 台 "(?P<ca>\w+)" 和 (?P<b>\d+) 台 "(?P<cb>\w+)"')
def devices(world, a, ca, b, cb):
    rows = [(i, f"D{i}", ca) for i in range(int(a))] + [(1000 + i, f"V{i}", cb) for i in range(int(b))]
    _tables(world)["inst_device"] = ("id INTEGER, dev_no TEXT, mete_dev_type_code TEXT", rows)


@step(r"做构建体检")
def run_preflight(world):
    db = _db(world)
    world["report"] = build_prep.check(db)
    world["hints"] = concept_hints.collect(db)


@step(r'场景画像包含 "(?P<name>[^"]+)"')
def scenario_includes(world, name):
    assert name in [k["name"] for k in world["report"]["scenario"]], world["report"]["scenario"]


@step(r'表族 "(?P<core>\w+)" 的代表表是 "(?P<rep>\w+)"')
def family_rep(world, core, rep):
    fam = {f["core"]: f for f in world["hints"]["families"]}
    assert fam[core]["representative"] == rep, fam.get(core)


@step(r'"(?P<a>\w+)" 与 "(?P<b>\w+)" 分属不同表族')
def distinct_families(world, a, b):
    cores = {f["core"] for f in world["hints"]["families"]}
    assert {a, b} <= cores and a != b


@step(r"分轮计划只放各表族的代表表")
def plan_reps_only(world):
    planned = {t for r in world["report"]["plan"] for t in r["tables"]}
    riders = {m for r in world["report"]["plan"] for ms in (r.get("family_members") or {}).values() for m in ms}
    assert riders and not planned & riders


@step(r'设备实例表按类别拆出 "(?P<na>[^"]+)" (?P<a>\d+) 台与 "(?P<nb>[^"]+)" (?P<b>\d+) 台')
def category_split(world, na, a, nb, b):
    split = next(s for s in world["hints"]["category_splits"] if s["table"] == "inst_device")
    got = {c["name"]: c["rows"] for c in split["categories"]}
    assert got == {na: int(a), nb: int(b)}, got


# ── 代码契约与裁决 ───────────────────────────────────────────────────

@step(r"湖仓里过站明细的 area_sn 全都能在区域表的 ca_id 中找到")
def lake_wip(world):
    _tables(world)["dim_co_area"] = ("ca_id TEXT, ca_name TEXT", [(f"A{i}", f"工位{i}") for i in range(20)])
    _tables(world)["dwd_f1_wip_detail"] = ("wd_sn TEXT, area_sn TEXT", [(f"S{i}", f"A{i % 20}") for i in range(200)])


@step(r"上传的后端代码包里有:")
def uploaded_code(world, doc):
    world["code"] = doc


@step(r"数据裁决「过站发生于区域」")
def adjudicate(world):
    server = _server()
    work = world["tmp"] / "work"
    work.mkdir(exist_ok=True)
    world["monkeypatch"].setattr(server, "WORK", str(work))
    if world.get("code"):
        (work / "uploads_code.zip").write_bytes(_zip(world["code"]))
    db = _db(world)
    extracted = {"objects": [{"name": "过站", "table": "dwd_f1_wip_detail", "kind": "event"},
                             {"name": "区域", "table": "dim_co_area", "kind": "object"}],
                 "relations": [{"source": "过站", "target": "区域", "verb": "发生于"}]}
    ev = server._gather_evidence(db)
    world["rel"] = server._adjudicate_ir(db, "场景", extracted, ev)["relations"][0]


@step(r'这条关系为 "(?P<status>\w+)"')
def relation_status(world, status):
    assert world["rel"]["status"] == status, world["rel"].get("note")


@step(r'证据里记下代码契约 "(?P<code>[^"]+)"')
def relation_contract(world, code):
    assert world["rel"]["evidence"]["contract"]["code"] == code


@step(r"读取上传的资料")
def read_upload(world):
    world["text"] = code_contracts.read_zip(_zip(world["code"], "src/app.xml"))
    world["contracts"] = code_contracts.extract([("代码.zip", world["text"])])


@step(r'读到的文本不含 "(?P<a>[^"]+)" 与 "(?P<b>[^"]+)"')
def text_scrubbed(world, a, b):
    assert a not in world["text"] and b not in world["text"], world["text"]


@step(r'字段 "(?P<col>\w+)" 的中文含义是 "(?P<label>[^"]+)"')
def field_label(world, col, label):
    got = {x["column"]: x["label"] for x in world["contracts"]["labels"]}
    assert got.get(col) == label, got


@step(r"开发者评注不作为字段含义")
def no_dev_comments(world):
    assert not any("为什么" in x["label"] for x in world["contracts"]["labels"])


# ── 有效行 ───────────────────────────────────────────────────────────

@step(r'区域表里工厂 "(?P<f1>\w+)" 与 "(?P<f2>\w+)" 各有编号 A0 到 A9 的区域')
def tenant_areas(world, f1, f2):
    _tables(world)["area"] = ("ca_id TEXT, ca_name TEXT, factory_code TEXT",
                              [(f"A{i}", f"{f}工位{i}", f) for f in (f1, f2) for i in range(10)])


@step(r'过站明细全部属于工厂 "(?P<f>\w+)"')
def tenant_wip(world, f):
    _tables(world)["wip"] = ("sn TEXT, area_sn TEXT, factory_code TEXT", [(f"S{i}", f"A{i % 10}", f) for i in range(50)])


@step(r"交叉复核候选关系「过站 → 区域」")
def cross_check_candidate(world):
    xc = _cross_check()
    ir = {"objects": [{"name": "wip", "table": "wip"}, {"name": "area", "table": "area"}],
          "relations": [{"source_concept": "wip", "target_concept": "area", "status": "candidate",
                         "evidence": {"child_key": "area_sn", "parent_key": "ca_id", "overlap": 100.0,
                                      "parent_unique": False, "name_ok": True}}]}
    rows, _ = xc.check(ir, xc.Db(_db(world)))
    world.update(xc=xc, cc_ir=ir, cc_rows=rows)


@step(r'复核结论为 "(?P<verdict>\w+)",规则为 "(?P<rule>\w+)"')
def cross_check_verdict(world, verdict, rule):
    row = world["cc_rows"][0]
    assert row["verdict"] == verdict and row["channels"]["valid_rows"]["rule"] == rule, row


@step(r'应用复核后关系从 "(?P<a>\w+)" 变为 "(?P<b>\w+)"')
def cross_check_apply(world, a, b):
    out = world["xc"].apply(json.loads(json.dumps(world["cc_ir"])), world["cc_rows"])
    assert world["cc_ir"]["relations"][0]["status"] == a and out["relations"][0]["status"] == b
    assert ontology_domain.allowed(a, b, "cross_check"), "迁移不在关系生命周期模型里"


def _scoped_ir(rule, **extra):
    names = {"tenant_scoped": ("wip", "area", "area_sn", "ca_id"), "soft_delete": ("device", "device_type", "type_code", "type_code")}
    c, p, ck, pk = names[rule]
    return {"objects": [{"name": c, "table": c}, {"name": p, "table": p}],
            "relations": [{"source_concept": c, "target_concept": p, "status": "verified",
                           "evidence": {"child_key": ck, "parent_key": pk,
                                        "valid_rows": {"state": "support", "rule": rule, **extra}}}]}


@step(r'本体里「过站 → 区域」按 "(?P<tc>\w+)" 分租户成立')
def tenant_ontology(world, tc):
    world["ir"] = _scoped_ir("tenant_scoped", tenant_column=tc)


@step(r"本体里「设备 → 设备类型」在排除已删除行后成立")
def soft_delete_ontology(world):
    world["ir"] = _scoped_ir("soft_delete", column="is_del")


@step(r'问数生成的 SQL 为 "(?P<sql>.+)"')
def gate(world, sql):
    world["gate"] = _server()._validate_sql_ontology(sql, world["ir"], strict=True)


@step(r'口径校验拦截,原因提到 "(?P<word>[^"]+)"')
def gate_blocks(world, word):
    ok, why = world["gate"]
    assert not ok and word in why, why


@step(r"口径校验放行")
def gate_passes(world):
    assert world["gate"][0], world["gate"][1]


@step(r'给模型的 JOIN 提示包含 "(?P<text>[^"]+)"')
def join_hint(world, text):
    tables = [o["table"] for o in world["ir"]["objects"]]
    assert any(text in h for h in _server()._join_hints(world["ir"], tables))


# ── 问数与观测 ───────────────────────────────────────────────────────

def _register_source(world, name, relation_evidence, tenant=False):
    server = _server()
    work = world["tmp"] / "work"
    work.mkdir(exist_ok=True)
    mp = world["monkeypatch"]
    mp.setattr(server, "WORK", str(work))
    mp.setattr(server, "_BUILD_CONN_F", str(work / "build_connections.json"))
    cols = "ca_id TEXT, ca_name TEXT" + (", factory_code TEXT" if tenant else "")
    _tables(world)["dim_co_area"] = (cols, [(f"A{i}", f"工位{i}") + (("F1",) if tenant else ()) for i in range(20)])
    wcols = "wd_sn TEXT, area_sn TEXT" + (", factory_code TEXT" if tenant else "")
    _tables(world)["dwd_f1_wip_detail"] = (wcols, [(f"S{i}", f"A{i % 20}") + (("F1",) if tenant else ()) for i in range(200)])
    db = _db(world)
    (work / "build_connections.json").write_text(json.dumps([{"id": "conn_bdd", "name": name, "kind": "sqlite",
                                                              "path": db}], ensure_ascii=False), encoding="utf-8")
    ir = {"objects": [{"name": "过站", "table": "dwd_f1_wip_detail"}, {"name": "区域", "table": "dim_co_area"}],
          "relations": [{"source_concept": "过站", "target_concept": "区域", "verb": "发生于", "status": "verified",
                         "evidence": relation_evidence}],
          "build_manifest": {"source": {"id": "conn_bdd", "name": name}}}
    (work / "built_bdd01.json").write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")
    server._GRAPH_ROW_CACHE.clear()
    server._QA_CACHE.clear()
    world["graph"] = "built_bdd01"


@step(r'登记了抽样库 "(?P<name>[^"]+)",并用它建了本体「过站 → 区域」')
def source_ontology(world, name):
    _register_source(world, name, {"child_key": "area_sn", "parent_key": "ca_id"})


@step(r'本体里「过站 → 区域」按 "(?P<tc>\w+)" 分租户成立,且已登记为抽样库本体')
def tenant_source_ontology(world, tc):
    _register_source(world, "湖仓抽样", {"child_key": "area_sn", "parent_key": "ca_id",
                                         "valid_rows": {"state": "support", "rule": "tenant_scoped", "tenant_column": tc}},
                     tenant=True)


def _ask(world, q, sql):
    server = _server()
    mp = world["monkeypatch"]
    mp.setattr(server, "agent_sql_plan", lambda *a, **k: {"analyses": [{"title": "过站排行", "sql": sql}]})
    mp.setattr(server, "_match_qa_skill", lambda _q: None)
    mp.setattr(server, "narrative_llm", lambda *a, **k: "ok")
    world["question"] = q
    world["resp"] = server.app.test_client().post("/api/chat", json={
        "q": q, "graphs": [world["graph"]], "nocache": True}).get_json()


@step(r'问 "(?P<q>[^"]+)",模型给出按区域关联的 SQL')
def ask_join(world, q):
    _ask(world, q, "SELECT a.ca_name AS 区域, COUNT(*) AS n FROM dwd_f1_wip_detail w "
                   "JOIN dim_co_area a ON a.ca_id = w.area_sn GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 1")


@step(r'问 "(?P<q>[^"]+)",模型给出不带工厂条件的 SQL')
def ask_without_tenant(world, q):
    ask_join(world, q)


@step(r'回答第一行为 "(?P<v>[^"]+)" 共 (?P<n>\d+) 条')
def first_row(world, v, n):
    rows = world["resp"]["results"][0]["data"]["rows"]
    assert rows[0] == {"区域": v, "n": int(n)}, rows


@step(r'加载本体的步骤注明 "(?P<name>[^"]+)"')
def load_step(world, name):
    assert name in world["resp"]["steps"][0]["info"]


def _counter(name, **labels):
    return sum(c["value"] for c in observability.snapshot()["counters"]
               if c["name"] == name and all(c["labels"].get(k) == v for k, v in labels.items()))


@step(r'指标 "(?P<name>[\w.]+)" 在原因 "(?P<reason>\w+)" 下至少为 (?P<n>\d+)')
def counter_by_reason(world, name, reason, n):
    assert _counter(name, reason=reason) >= int(n), observability.snapshot()["counters"]


@step(r'指标 "(?P<name>[\w.]+)" 至少为 (?P<n>\d+)')
def counter_at_least(world, name, n):
    assert _counter(name) >= int(n), observability.snapshot()["counters"]


@step(r"最近的问数追踪有结束事件,且不含问句原文")
def qa_trace(world):
    events = observability.recent(kind="qa")
    assert any(e["event"] == "finish" for e in events)
    assert world["question"] not in json.dumps(events, ensure_ascii=False)


@step(r'登记了抽样库 "(?P<name>[^"]+)",模型不可用')
def source_without_llm(world, name):
    _register_source(world, name, {"child_key": "area_sn", "parent_key": "ca_id"})
    server = _server()
    mp = world["monkeypatch"]
    mp.setattr(server, "_llm_extract_ontology", lambda *a, **k: None)
    mp.setattr(server, "_build_intent", lambda *a, **k: {})
    mp.setattr(server, "_build_summary", lambda *a, **k: "摘要")


@step(r"发起一次构建")
def start_build(world):
    body = _server().app.test_client().post("/api/build/inquire", json={
        "q": "过站本体", "source": "conn_bdd", "name": "场景构建", "skills": ["ontology-semi-auto"]}).get_data(as_text=True)
    events = [json.loads(x[6:]) for x in body.splitlines() if x.startswith("data: ")]
    world["build_done"] = next(e for e in events if e.get("type") == "done")


@step(r"追踪里有这次构建的开始、步骤与结束事件")
def build_trace(world):
    events = observability.recent(kind="build")
    last = next(e for e in reversed(events) if e["event"] == "finish")
    mine = [e for e in events if e["run"] == last["run"]]
    assert {"start", "step", "finish"} <= {e["event"] for e in mine}
    assert last["status"] == "ok" and last["objects"] == world["build_done"]["stats"]["objects"]


# 避免未使用导入告警:sqlite3 供步骤扩展时直接查库使用
_ = sqlite3
