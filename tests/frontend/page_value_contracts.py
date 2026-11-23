"""Read the active workspace through real public APIs and check what its pages communicate."""
import re

import pytest
from playwright.sync_api import expect
from rdflib import Graph, OWL, RDF


@pytest.fixture
def page(browser):
    with browser.new_context(viewport={"width": 1440, "height": 900}) as ctx:
        yield ctx.new_page()


def visit(page, site, key):
    page.goto(f"{site}/?source=demo#{key}")
    page.wait_for_function("APP_DATA.ready")
    expect(page.locator(".page.on")).to_have_id("p_" + key)


def read(page, site, endpoint):
    response = page.request.get(site + "/api/" + endpoint, headers={"X-DataMind-Source": "demo"})
    assert response.ok, (endpoint, response.status)
    return response.json()


def test_home_chart_periods_and_series_match_overview_rows(page, site):
    visit(page, site, "home")
    data = read(page, site, "overview")
    for selector, key, columns in [("#c_trend", "trend", ("rev", "gp")), ("#c_prod", "prod", ("qty",))]:
        chart = page.locator(selector)
        expect(chart.locator("canvas").first).to_be_visible()
        result = chart.evaluate("""el => {
            const opt = echarts.getInstanceByDom(el).getOption();
            return [opt.xAxis[0].data, opt.series.map(s=>s.data)];
        }""")
        assert result == [[r["m"] for r in data[key]], [[r[c] for r in data[key]] for c in columns]]


@pytest.mark.parametrize("key,selector", [("build", "#bc_src_n"), ("conn", "#cn_n")])
def test_available_source_counts_agree_with_registry(page, site, key, selector):
    visit(page, site, key)
    sources = read(page, site, "build/sources")["sources"]
    expect(page.locator(selector)).to_have_text(f"({len(sources)})")


@pytest.mark.parametrize("key,selector", [
    ("graph", "#g_sel"), ("review", "#rv_sel"), ("ontquality", "#oq_sel"), ("claw", "#claw_graph")
])
def test_graph_choices_refer_to_the_same_registered_ids(page, site, key, selector):
    visit(page, site, key)
    expected = {g["id"] for g in read(page, site, "graphs")}
    select = page.locator(selector)
    expect(select.locator("option[value=demo]")).to_be_attached()
    assert {v for v in select.locator("option").evaluate_all("opts=>opts.map(o=>o.value)") if v} == expected
    select.select_option("demo")
    if key == "graph":
        data = read(page, site, "graph/demo")
        expect(page.locator("#g_info")).to_contain_text(f'{len(data["nodes"])} 节点 · {len(data["edges"])} 关系')
    elif key == "review":
        counts = read(page, site, "ont/review?graph=demo")["counts"]
        for title, k in [("待审", "pending"), ("已通过", "approved"), ("已否决", "rejected")]:
            expect(page.locator("#rv_kpis")).to_contain_text(f"{title} {counts[k]}")
    elif key == "ontquality":
        score = read(page, site, "ont/completeness/demo")["score"]
        expect(page.locator("#oq_kpis")).to_contain_text(f"{score}%")


def test_sparql_class_count_matches_reparsed_download(page, site):
    visit(page, site, "sparql")
    key = read(page, site, "data/context")["default_graph"]
    response = page.request.get(f"{site}/api/graph/{key}/export.ttl")
    assert response.ok
    classes = set(Graph().parse(data=response.body(), format="turtle").subjects(RDF.type, OWL.Class))
    page.locator("#sq_q").fill("SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE { ?s a <http://www.w3.org/2002/07/owl#Class> }")
    page.locator("#p_sparql").get_by_role("button", name="执行").click()
    # SPARQL defaults to the active graph. The export must use the same graph.
    expect(page.locator("#sq_out td")).to_have_text(str(len(classes)))


def test_metric_names_and_quality_counts_match_the_active_graph(page, site):
    visit(page, site, "metrics")
    page.locator("#m_sel").select_option("demo")
    metrics = read(page, site, "metrics?graph=demo")
    for layer in ("atomic", "derived", "composite"):
        if metrics.get(layer):
            expect(page.locator("#m_body")).to_contain_text(metrics[layer][0]["name"])
    visit(page, site, "quality")
    quality = read(page, site, "quality")
    expected = [quality["scanned"], quality["passed"], quality["levels"].get("warn", 0), quality["levels"].get("error", 0)]
    expect(page.locator("#quality_kpis .n")).to_have_text([str(v) for v in expected])


def test_registry_rows_can_be_found_in_catalog_and_glossary(page, site):
    visit(page, site, "catalog")
    table = next(t for t in read(page, site, "tables") if t["name"] == "dim_customer")
    page.locator("#cat_q").fill(table["name"])
    row = page.locator("#tbl_list tr").filter(has=page.get_by_text(table["name"], exact=True))
    expect(row.locator("td").nth(1)).to_have_text(str(table["rows"]))
    expect(row.locator("td").nth(2)).to_have_text(str(table["cols"]))
    visit(page, site, "glossary")
    terms = read(page, site, "glossary")["terms"]
    term = next(t for t in terms if t["en"] and t["cn"])
    page.locator("#gl_q").fill(term["en"])
    expect(page.locator("#gl_list")).to_contain_text(term["cn"])


def test_current_engine_and_action_type_labels_match_configuration(page, site):
    visit(page, site, "enginecfg")
    config = read(page, site, "engine/config")
    expect(page.locator("#eg_runtimes")).to_contain_text(config["driver"])
    visit(page, site, "actioncenter")
    types = read(page, site, "actions")["types"]
    for item in types:
        label = page.locator("#ac_types .step").filter(has=page.get_by_text(item["cn"], exact=True))
        expect(label).to_contain_text("高风险" if item["risk"] == "high" else "低风险")


def test_robotics_history_and_task_counts_match_records(page, site):
    visit(page, site, "jobs")
    jobs = read(page, site, "domain-runs?operation=build_ontology&limit=20&offset=0")["items"]
    if jobs:
        for job in jobs:
            expect(page.locator("#ra_jobs")).to_contain_text(job["run_id"])
    else:
        expect(page.locator("#ra_jobs")).to_contain_text("暂无构建记录")
    visit(page, site, "robotics-task")
    tasks = read(page, site, "robotics/tasks?limit=30&offset=0&q=")
    expect(page.locator(".rta-count")).to_have_text(f'已显示 {len(tasks["items"])} / {tasks["total"]} 条')


def test_incomplete_robotics_deep_link_returns_to_the_asset_library(page, site):
    visit(page, site, "robotics-view")
    expect(page.locator("#rv_host")).to_contain_text("缺少")
    page.locator("#rv_host").get_by_text(re.compile("返回本体目录")).click()
    expect(page.locator("#p_library")).to_be_visible()
    assets = read(page, site, "ontology-assets")["items"]
    assert assets
    expect(page.locator("#ra_host")).to_contain_text(assets[0]["name"])


def test_question_modes_and_evaluation_agree_with_visible_outputs(page, site):
    visit(page, site, "chat")
    for control, region in [("instant", "instant"), ("report", "report"), ("deep", "deep")]:
        page.locator("#mode_" + control).click()
        for panel in ["instant", "report", "deep"]:
            assert page.locator("#dqv_" + panel).is_visible() == (panel == region)
    visit(page, site, "qaeval")
    data = read(page, site, "eval/results")
    if data.get("items"):
        expect(page.locator("#qe_body")).to_contain_text(data["items"][0]["q"])
    else:
        expect(page.locator("#qe_body")).to_contain_text("尚未运行评测")


@pytest.mark.parametrize("button,parser", [("TTL", "turtle"), ("JSON-LD", "json-ld"), ("RDF/XML", "xml")])
def test_download_buttons_export_the_selected_graphs_class_labels(page, site, button, parser):
    from rdflib import RDFS

    visit(page, site, "graph")
    select = page.locator("#g_sel")
    available = read(page, site, "graphs")
    key = next(g["id"] for g in available if g["id"] != "demo" and g.get("nodes", 1))
    select.select_option(key)
    data = read(page, site, "graph/" + key)
    labels = {n["name"] for n in data["nodes"]}
    assert labels
    with page.expect_download() as event:
        page.locator("#p_graph").get_by_role("button", name=button, exact=True).click()
    file = event.value
    assert file.suggested_filename.startswith(key + ".")
    graph = Graph().parse(data=file.path().read_bytes(), format=parser)
    downloaded = {str(label) for cls in graph.subjects(RDF.type, OWL.Class) for label in graph.objects(cls, RDFS.label)}
    assert downloaded == labels
