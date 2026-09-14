"""Unmocked deployed-service acceptance; writes only to one uniquely named new graph.

No routes are intercepted. Build and QA each run at most once. Engine configuration,
existing graphs, skills, feedback and business rows are never edited by this script.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote, urlparse
import uuid


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.browser_runtime import close_browser, managed_playwright  # noqa: E402


PAGES = ["home", "catalog", "graph", "metrics", "build", "review", "ontquality",
         "chat", "claw", "enginecfg", "conn", "quality", "glossary", "sqldev",
         "sparql", "library", "actioncenter", "qaeval", "assistant", "viz", "apis",
         "jobs", "sysadmin", "rules", "layers", "skills", "agents"]


def parse_sse(text):
    events = []
    for frame in re.split(r"\r?\n\r?\n", text):
        data = "\n".join(line[5:].removeprefix(" ") for line in frame.splitlines()
                         if line.startswith("data:"))
        if data:
            event = json.loads(data)
            if not isinstance(event, dict):
                raise ValueError("SSE payload is not an object")
            events.append(event)
    return events


def runtime_evidence(events):
    steps = [event for event in events if event.get("type") == "step"]
    fallback = [step for step in steps if any(word in str(step.get("info", ""))
                for word in ("离线", "兜底", "回退", "规则模板", "未配置", "不可用"))]
    done = next((event for event in reversed(events) if event.get("type") == "done"), {})
    method = done.get("method")
    if method:
        mode = "offline_fallback" if any(word in method for word in ("离线", "兜底", "规则")) else "reported_online"
    elif fallback:
        mode = "fallback_steps_present"
    elif any(step.get("step") == "skill_reuse" for step in steps):
        mode = "saved_skill_reuse"
    else:
        mode = "inspect_recorded_steps"
    return {"method": method, "mode": mode, "fallback_steps": fallback,
            "runtime_steps": [step for step in steps if any(token in step.get("step", "")
                              for token in ("llm", "agent", "plan", "narrative", "construct"))]}


class Acceptance:
    def __init__(self, args):
        self.args = args
        self.output = args.output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.start = time.monotonic()
        self.graph_key = None
        self.preexisting = set()
        self.errors = []
        self.requests = []
        self.report = {"url": args.url, "prefix": args.prefix, "mocked_routes": False,
                       "read_only": args.read_only, "checks": [], "build_calls": 0, "qa_calls": 0,
                       "artifact_policy": "delete own graph at end" if args.cleanup else "retain own graph for audit"}

    def save(self, name, value):
        (self.output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def checkpoint(self):
        self.report.update(seconds=round(time.monotonic() - self.start, 2),
                           graph_key=self.graph_key, page_errors=self.errors, post_requests=self.requests)
        self.save("report.json", self.report)

    def check(self, name, condition, evidence=None, required=False):
        self.report["checks"].append({"name": name, "ok": bool(condition), "evidence": evidence})
        print(f"{'PASS' if condition else 'FAIL'} {name}" + (f": {evidence}" if evidence is not None else ""), flush=True)
        self.checkpoint()
        if required and not condition:
            raise AssertionError(name)

    def observe_request(self, request):
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return
        path = urlparse(request.url).path
        try:
            body = request.post_data_json or {}
        except Exception:
            body = {}
        self.requests.append({"method": request.method, "path": path,
                              "graph": body.get("graph"), "graphs": body.get("graphs"),
                              "name": body.get("name"), "base_graph": body.get("base_graph"),
                              "op": (body.get("op") or {}).get("op")})

    async def get(self, context, path):
        response = await context.request.get(self.args.url + path, timeout=30000)
        if not response.ok:
            raise AssertionError(f"GET {path}: HTTP {response.status}")
        return await response.json()

    async def goto(self, page, name):
        await page.locator(f'.nav[data-p="{name}"]').click()
        await page.locator(f"#p_{name}.on").wait_for(timeout=15000)
        await page.wait_for_timeout(450)

    async def stream_from_click(self, page, path, selector, timeout, label):
        field = "build_calls" if label == "build" else "qa_calls"
        self.check(f"{label} single invocation", self.report[field] == 0, required=True)
        self.report[field] += 1
        async with page.expect_response(lambda response: urlparse(response.url).path == path
                                        and response.request.method == "POST", timeout=30000) as pending:
            await page.locator(selector).click()
        response = await pending.value
        self.check(f"{label} real HTTP success", response.ok, response.status, required=True)
        self.check(f"{label} SSE content type", "text/event-stream" in response.headers.get("content-type", ""), required=True)
        # Chrome can evict long SSE bodies from its Network domain. Observe a
        # native Response clone while the real UI consumes the unchanged stream.
        await page.wait_for_function("path=>window.__acceptance_streams[path]?.finished", arg=path, timeout=timeout * 1000)
        capture = await page.evaluate("path=>window.__acceptance_streams[path]", path)
        text = capture["text"]
        (self.output / f"{label}.sse").write_text(text, encoding="utf-8")
        events = parse_sse(text)
        self.save(f"{label}-events.json", events)
        done = [event for event in events if event.get("type") == "done"]
        errors = [event for event in events if event.get("type") == "error"]
        self.check(f"{label} one terminal result", len(done) == 1 and not errors,
                   {"done_count": len(done), "errors": errors}, required=True)
        self.report[f"{label}_runtime"] = runtime_evidence(events)
        self.checkpoint()
        return done[0]

    async def review_and_restore(self, page, context):
        key = self.graph_key
        before = await self.get(context, "/api/ont/review?graph=" + quote(key))
        edits = await self.get(context, "/api/ont/edits?graph=" + quote(key))
        self.check("new graph has no preexisting edits", not edits.get("ops"), required=True)
        self.save("review-before.json", before)
        await self.goto(page, "review")
        await page.locator("#rv_sel").select_option(key)
        await page.wait_for_function("key=>RV_KEY===key", arg=key)
        await page.wait_for_timeout(250)
        relation = next((row for row in before.get("rows", []) if row.get("status") != "rejected"), None)
        obj = next((row for row in before.get("objects", []) if row.get("candidate")), None)
        self.check("new graph provides a reviewable candidate", bool(relation or obj), required=True)
        if relation:
            row = page.locator("#rv_body tr").filter(has_text=relation["s"] + " → " + relation["t"]).first
            await row.get_by_role("button", name="通过", exact=True).click()
        else:
            row = page.locator("#rv_objs tr").filter(has_text=obj["id"]).first
            await row.get_by_role("button", name="确认", exact=True).click()
        await page.locator("#rvm_who").fill(self.args.prefix[:40])
        await page.locator("#rvm_comment").fill("真实部署验收：只验证人审与撤销机制，随后恢复原状态。")
        async with page.expect_response(lambda response: urlparse(response.url).path == "/api/ont/apply"
                                        and response.request.method == "POST") as pending:
            await page.locator("#rvm_ok").click()
        response = await pending.value
        applied = await response.json()
        self.check("human review accepted", response.ok and applied.get("ok"), applied, required=True)
        current = await self.get(context, "/api/ont/edits?graph=" + quote(key))
        ops = current.get("ops") or []
        self.check("only this run's signed review was added", len(ops) == 1 and ops[-1].get("reviewer") == self.args.prefix[:40], required=True)
        after = await self.get(context, "/api/ont/review?graph=" + quote(key))
        self.save("review-after.json", after)
        self.check("human review changes visible state", before != after, required=True)
        await page.screenshot(path=str(self.output / "review-approved.png"), full_page=True)
        async with page.expect_response(lambda response: urlparse(response.url).path == "/api/ont/undo"
                                        and response.request.method == "POST") as pending:
            await page.locator("#p_review").get_by_role("button", name="撤销上一步", exact=True).click()
        response = await pending.value
        undone = await response.json()
        self.check("review undo accepted", response.ok and undone.get("ok"), undone, required=True)
        restored = await self.get(context, "/api/ont/review?graph=" + quote(key))
        self.save("review-restored.json", restored)
        self.check("review restored exact original state", restored == before, required=True)
        self.report["review_restored"] = True
        self.checkpoint()

    async def workflow(self, page, context):
        health = await self.get(context, "/api/health")
        db = await self.get(context, "/api/db/check")
        runtimes = await self.get(context, "/api/ont/runtimes")
        self.report["runtimes"] = runtimes
        self.check("deployed API and database ready", health.get("ok") and db.get("ok"), required=True)
        before = await self.get(context, "/api/build/built")
        self.preexisting = {graph["key"] for graph in before}
        if self.args.resume_graph:
            owned = next((g for g in before if g["key"] == self.args.resume_graph), None)
            self.check("resume owns a matching acceptance artifact", owned and owned.get("name") == self.args.prefix, required=True)
            self.preexisting.discard(self.args.resume_graph)
            self.report["resumed_existing_build"] = self.args.resume_graph
        else:
            self.check("acceptance name is unique", all(not str(graph.get("name", "")).startswith(self.args.prefix) for graph in before), required=True)
        await page.goto(self.args.url + "/", wait_until="domcontentloaded")
        for name in PAGES:
            errors_before = len(self.errors)
            await self.goto(page, name)
            text = (await page.locator(f"#p_{name}").inner_text()).strip()
            self.check(f"page {name}", len(text) > 30 and len(self.errors) == errors_before,
                       {"text_length": len(text), "new_errors": self.errors[errors_before:]})
        self.check("no initial browser script errors", not self.errors, list(self.errors), required=True)
        if self.args.read_only:
            return
        sources = await self.get(context, "/api/build/sources")
        source = next((item for item in sources.get("sources", []) if item.get("id") == self.args.source), None)
        self.check("requested source ready", source and source.get("ready"), self.args.source, required=True)
        if self.args.resume_graph:
            key = self.args.resume_graph
            history = await self.get(context, "/api/build/history/" + quote(key))
            graph = await self.get(context, "/api/graph/" + quote(key))
            done = {"graph_key": key, "name": history["name"],
                    "method": (history.get("rounds") or [{}])[-1].get("method"),
                    "stats": {"objects": len(graph["nodes"]), "links": len(graph["edges"])}}
            self.report["build_runtime"] = {"method": done["method"], "source": "persisted build history; not a new call"}
        else:
            await self.goto(page, "build")
            await page.get_by_role("button", name="+ 新建对话", exact=True).click()
            await page.evaluate("source=>bcSelectSrc(source)", self.args.source)
            await page.locator("#bc_name").fill(self.args.prefix)
            await page.locator("#bc_q").fill(self.args.build_query)
            await page.locator("#bc_cq").fill(self.args.cq)
            done = await self.stream_from_click(page, "/api/build/inquire", "#bc_send", self.args.build_timeout, "build")
        key = done.get("graph_key")
        self.check("build creates a new owned graph", isinstance(key, str) and key.startswith("built_")
                   and key not in self.preexisting and str(done.get("name", "")).startswith(self.args.prefix),
                   {"graph_key": key, "name": done.get("name")}, required=True)
        self.graph_key = key
        history = await self.get(context, "/api/build/history/" + quote(key))
        self.check("persisted artifact has acceptance name", history.get("name", "").startswith(self.args.prefix), required=True)
        self.save("build-history.json", history)
        self.save("artifact-ready.json", {"graph_key": key, "name": done["name"], "method": done.get("method"), "url": self.args.url})
        print("ARTIFACT_CREATED " + json.dumps({"graph_key": key, "method": done.get("method"), "output": str(self.output)}, ensure_ascii=False), flush=True)
        if self.args.resume_graph:
            await page.evaluate("key=>bcView(key)", key)
        else:
            await page.locator(".bc-result").wait_for(timeout=15000)
            self.check("completed build card visible", await page.locator(".bc-result").count() == 1, required=True)
            await page.locator(".bc-result").get_by_role("button", name="在本体图谱中查看 ↗", exact=True).click()
        await page.wait_for_function("key=>GRAPH_CUR.key===key&&document.querySelector('#g_canvas canvas')", arg=key, timeout=30000)
        graph = await self.get(context, "/api/graph/" + quote(key))
        self.save("graph.json", graph)
        self.check("graph API and build statistics agree", len(graph.get("nodes", [])) == done.get("stats", {}).get("objects")
                   and len(graph.get("edges", [])) == done.get("stats", {}).get("links"), required=True)
        await page.screenshot(path=str(self.output / "graph-desktop.png"), full_page=True)
        await self.review_and_restore(page, context)
        print("ARTIFACT_READY_FOR_AUDIT " + key, flush=True)
        await page.evaluate("key=>bcView(key)", key)
        await page.wait_for_function("key=>GRAPH_CUR.key===key", arg=key)
        self.check("new graph offers scoped QA", await page.locator("#g_ask").is_enabled(), required=True)
        await page.locator("#g_ask").click()
        await page.wait_for_function("key=>DQ_DS.graphs.length===1&&DQ_DS.graphs[0]===key", arg=key)
        tables = {table for node in graph.get("nodes", []) for table in node.get("tables", []) if table}
        available = {row["name"] for row in await self.get(context, "/api/tables")}
        allowed = sorted(tables & available)
        table = self.args.table or ("dim_customer" if "dim_customer" in allowed else (allowed[0] if allowed else ""))
        self.check("QA table belongs to new graph and query connection", table in allowed, table, required=True)
        await page.locator("#dq_src_chip").click()
        await page.locator("#dstab_table").click()
        await page.locator("#ds_tsearch").fill(table)
        await page.locator("#ds_notable").uncheck()
        await page.locator("#ds_tgrid .ds-card").filter(has=page.locator(".nm", has_text=re.compile("^" + re.escape(table) + "$"))).click()
        await page.locator("#dq_dsmodal").get_by_role("button", name="确定", exact=True).click()
        question = self.args.question or f"{table}有多少条记录？"
        await page.locator("#chat_q").fill(question)
        qa = await self.stream_from_click(page, "/api/chat/stream", "#chat_btn", self.args.qa_timeout, "qa")
        anchor = qa.get("anchor") or {}
        self.check("QA anchors only the new graph", (anchor.get("ontology") or {}).get("keys") == [key]
                   and not anchor.get("fallback"), anchor.get("ontology"), required=True)
        self.check("QA bypasses cache and produces executed results", qa.get("cached") is False and bool(qa.get("results")), required=True)
        self.report["qa_question"] = question
        self.report["qa_table"] = table
        self.report["qa_session"] = qa.get("session")
        self.save("qa-done.json", qa)
        await page.wait_for_function("!document.querySelector('#chat_btn').disabled", timeout=15000)
        self.check("QA card retains execution evidence", await page.locator(".dq-card .dq-exec").count() > 0, required=True)
        await page.wait_for_timeout(1200)  # wait for chart animation before the evidence screenshot
        await page.screenshot(path=str(self.output / "qa-desktop.png"), full_page=True)
        for name in ("build", "graph", "chat"):
            await self.goto(page, name)
            await page.set_viewport_size({"width": 390, "height": 900})
            await page.wait_for_timeout(250)
            geometry = await page.evaluate("({width:innerWidth,scroll:document.documentElement.scrollWidth})")
            self.check(f"mobile {name} no page overflow", geometry["scroll"] <= geometry["width"] + 1, geometry)
            await page.screenshot(path=str(self.output / f"{name}-390.png"), full_page=True)
            await page.set_viewport_size({"width": 1440, "height": 1000})

    async def cleanup(self, context):
        if not self.args.cleanup or not self.graph_key:
            return
        key = self.graph_key
        history = await self.get(context, "/api/build/history/" + quote(key))
        self.check("cleanup revalidates artifact ownership", key not in self.preexisting
                   and history.get("name", "").startswith(self.args.prefix), required=True)
        response = await context.request.post(self.args.url + "/api/build/delete", data={"key": key}, timeout=30000)
        result = await response.json()
        remaining = await self.get(context, "/api/build/built")
        self.check("own graph artifact removed", response.ok and result.get("ok")
                   and all(graph["key"] != key for graph in remaining), required=True)
        self.report["cleanup_note"] = "Graph JSON removed; service audit/usage records may intentionally remain."

    async def run(self):
        async with managed_playwright() as pw:
            executable = next((value for value in [os.environ.get("DATAMIND_BROWSER_EXECUTABLE"),
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium"]
                if value and Path(value).is_file()), None)
            browser = await pw.chromium.launch(executable_path=executable)
            context = await browser.new_context(viewport={"width": 1440, "height": 1000})
            await context.add_init_script("""(() => {
              window.__acceptance_streams = {};
              const nativeFetch = window.fetch;
              window.fetch = async function(...args) {
                const response = await nativeFetch.apply(this, args);
                const path = new URL(typeof args[0] === 'string' ? args[0] : args[0].url, location.href).pathname;
                if (['/api/build/inquire','/api/chat/stream'].includes(path)) {
                  const state = {text:'', finished:false};
                  window.__acceptance_streams[path] = state;
                  const reader = response.clone().body.getReader(), decoder = new TextDecoder();
                  (async () => {try {
                    while (true) {
                      const {done,value} = await reader.read(); if(done)break;
                      state.text += decoder.decode(value,{stream:true});
                      if(state.text.length > 16000000)throw Error('acceptance evidence limit');
                    }
                    state.text += decoder.decode();
                  } catch(error) {state.capture_error=String(error);}
                  finally {state.finished=true; reader.releaseLock();}})();
                }
                return response;
              };
            })();""")
            page = await context.new_page()
            page.on("pageerror", lambda error: self.errors.append(str(error)))
            page.on("request", self.observe_request)
            try:
                await self.workflow(page, context)
            except Exception as error:
                self.check("workflow completed", False, f"{type(error).__name__}: {error}")
                try:
                    await page.screenshot(path=str(self.output / "failure.png"), full_page=True, timeout=10000)
                except Exception:
                    pass
            finally:
                try:
                    await self.cleanup(context)
                except Exception as error:
                    self.check("cleanup completed", False, f"{type(error).__name__}: {error}")
                self.check("no browser script errors", not self.errors, list(self.errors))
                self.checkpoint()
                await close_browser(browser)
        failed = [check for check in self.report["checks"] if not check["ok"]]
        self.report["passed"] = not failed
        self.checkpoint()
        print(json.dumps({"passed": not failed, "graph_key": self.graph_key, "report": str(self.output / "report.json")}, ensure_ascii=False), flush=True)
        return 1 if failed else 0


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Explicit deployed service origin, e.g. http://127.0.0.1:8092")
    parser.add_argument("--output", type=Path, default=ROOT / ".check-results/live-deployment")
    parser.add_argument("--prefix", default="验收_live_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:5])
    parser.add_argument("--source", default="demo")
    parser.add_argument("--table", help="One table bound by the new graph; prefers dim_customer when available")
    parser.add_argument("--build-query", default="基于当前数据源识别真实业务对象及关系，重点覆盖客户、订单、产品和金额字段；用可复核证据验证关系，不编造缺失的表或字段。")
    parser.add_argument("--cq", default="订单与客户之间有哪些可验证的关联？")
    parser.add_argument("--question", help="Defaults to a count of one explicitly selected bound table")
    parser.add_argument("--build-timeout", type=float, default=600)
    parser.add_argument("--qa-timeout", type=float, default=300)
    parser.add_argument("--read-only", action="store_true", help="Only readiness and page checks; no POST requests")
    parser.add_argument("--cleanup", action="store_true", help="Delete only this run's graph after verifying ownership; default retains it for audit")
    parser.add_argument("--resume-graph", help="Continue checks on this run's retained named graph without building again")
    args = parser.parse_args()
    args.url = args.url.rstrip("/")
    parsed = urlparse(args.url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
        parser.error("--url must be an explicit HTTP(S) origin without path/query/fragment")
    if not args.prefix.startswith("验收_") or len(args.prefix) > 55:
        parser.error("--prefix must start with 验收_ and contain at most 55 characters")
    return args


if __name__ == "__main__":
    sys.exit(asyncio.run(Acceptance(arguments()).run()))
