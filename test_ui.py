#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cosmo DataMind 全 UI 走查(可选,需 playwright):
     pip install playwright && playwright install chromium
     python3 server.py &   # 先起服务(DATAMIND_URL 可覆盖地址)
     python3 test_ui.py
   覆盖:26 页逐页渲染(激活可见/内容非空/零 pageerror)+ 子 UI 交互
   (弹窗开合/表单执行/详情点击/筛选过滤/图表渲染/技能复用问数)。"""
import asyncio, json, sys
from playwright.async_api import async_playwright

import os
BASE = os.environ.get("DATAMIND_URL", "http://127.0.0.1:8092")
PAGES = ["home","graph","metrics","catalog","quality","glossary","sqldev","sparql",
         "chat","review","build","library","actioncenter","qaeval","assistant",
         "enginecfg","conn","viz","apis","jobs","sysadmin","rules","layers",
         "ontquality","skills","agents"]

R = {"pass": [], "fail": []}
def ok(name, _extra=""):  R["pass"].append(name); print(f"  ✓ {name}")
def bad(name, why=""): R["fail"].append(f"{name} :: {why}"); print(f"  ✗ {name} :: {why}")

async def main():
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        pg = await (await b.new_context(viewport={"width":1440,"height":950})).new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)[:160]))
        pg.on("console", lambda m: errors.append("console:"+m.text[:160]) if m.type=="error" else None)

        await pg.goto(BASE, wait_until="domcontentloaded")
        await pg.wait_for_timeout(2500)

        # ── 阶段一:26 页逐页走查 ──────────────────────────────
        print("== 阶段一:全页面渲染 ==")
        for p in PAGES:
            errors.clear()
            await pg.evaluate(f"location.hash='#{p}'")
            await pg.wait_for_timeout(1600)
            vis = await pg.evaluate(f"(d=>d&&getComputedStyle(d).display!=='none')(document.getElementById('p_{p}'))")
            txt = await pg.evaluate(f"(d=>d?d.innerText.trim().length:0)(document.getElementById('p_{p}'))")
            errs = [e for e in errors if "favicon" not in e]
            if vis and txt > 30 and not errs: ok(f"页 {p}(文本 {txt} 字)")
            else: bad(f"页 {p}", f"vis={vis} text={txt} errs={errs[:2]}")

        # ── 阶段二:子 UI 与交互 ──────────────────────────────
        print("== 阶段二:子 UI 交互 ==")
        async def go(p, ms=1500):
            errors.clear(); await pg.evaluate(f"location.hash='#{p}'"); await pg.wait_for_timeout(ms)

        # home:KPI 数字
        await go("home")
        k = await pg.evaluate("document.querySelectorAll('#p_home .kpi .n,#p_home .kpi b').length")
        (ok if k>=3 else bad)(f"home KPI 卡({k})") if isinstance(k,int) else None

        # graph:图谱画布 + 图谱切换下拉 + 节点详情(经 JS 触发)
        await go("graph", 2500)
        canvas = await pg.evaluate("!!document.querySelector('#p_graph canvas')")
        opts = await pg.evaluate("(s=>s?s.options.length:0)(document.querySelector('#p_graph select'))")
        (ok if canvas and opts>=2 else bad)(f"graph 画布+{opts}图谱源")
        # metrics:行点击出详情
        await go("metrics")
        rows = await pg.evaluate("document.querySelectorAll('#p_metrics tr').length")
        if rows>3:
            await pg.evaluate("document.querySelectorAll('#p_metrics tbody tr, #p_metrics tr')[1].click()")
            await pg.wait_for_timeout(900)
            ok(f"metrics {rows}行+行点击")
        else: bad("metrics 行数", str(rows))
        # catalog:表清单+预览
        await go("catalog")
        rows = await pg.evaluate("document.querySelectorAll('#p_catalog tr').length")
        (ok if rows>50 else bad)(f"catalog 表清单({rows}行)")
        # quality:分层对账告警在列
        await go("quality", 2200)
        t = await pg.evaluate("document.getElementById('p_quality').innerText")
        (ok if "分层对账" in t else bad)("quality 含分层对账告警", t[:60])
        # glossary:搜索过滤
        await go("glossary")
        n0 = await pg.evaluate("document.querySelectorAll('#p_glossary tr').length")
        await pg.fill("#gl_q", "直通")
        await pg.wait_for_timeout(700)
        n1 = await pg.evaluate("document.querySelectorAll('#p_glossary tr').length")
        (ok if 0<n1<n0 else bad)(f"glossary 过滤 {n0}→{n1}")
        # sqldev:执行只读 SQL
        await go("sqldev")
        await pg.fill("#p_sqldev textarea", "SELECT COUNT(*) AS n FROM dim_customer")
        await pg.click("#p_sqldev button:has-text('运行')")
        await pg.wait_for_timeout(1800)
        t = await pg.evaluate("document.getElementById('p_sqldev').innerText")
        (ok if ("n" in t and any(c.isdigit() for c in t)) else bad)("sqldev 运行出结果")
        # sparql
        await go("sparql")
        await pg.click("#p_sparql button:has-text('执行')")
        await pg.wait_for_timeout(2500)
        t = await pg.evaluate("document.getElementById('p_sparql').innerText")
        (ok if ("http" in t or "结果" in t) else bad)("sparql 执行", t[:60])
        # chat:命中沉淀技能(不出网,秒回)
        await go("chat")
        q = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "workdir", "qa_skills.json")))[0]["question"]
        await pg.fill("#p_chat textarea, #p_chat input[type=text]", q)
        await pg.click("#p_chat button:has-text('发送'), #p_chat button:has-text('提问')")
        try:
            await pg.wait_for_function("document.getElementById('p_chat').innerText.includes('沉淀')||document.getElementById('p_chat').innerText.length>800", timeout=30000)
            ok("chat 沉淀技能复用作答")
        except Exception: bad("chat 作答超时", "")
        # review:三态计数
        await go("review", 2000)
        t = await pg.evaluate("document.getElementById('p_review').innerText")
        (ok if ("待审" in t or "已通过" in t) else bad)("review 评审台", t[:50])
        # build:构建页 + 技能对比弹窗开合 + 技能管理列表
        await go("build", 2000)
        has = await pg.evaluate("!!document.getElementById('skc_modal')")
        if has:
            await pg.evaluate("skcOpen&&skcOpen()")
            await pg.wait_for_timeout(1200)
            vis = await pg.evaluate("document.getElementById('skc_modal').style.display!=='none'")
            await pg.evaluate("document.getElementById('skc_modal').style.display='none'")
            (ok if vis else bad)("build 技能对比弹窗开合")
        skl = await pg.evaluate("document.querySelectorAll('#bc_skills div,#bc_skills label,#bc_skills input').length")
        (ok if skl>0 else bad)(f"build 技能清单({skl})")
        # library:已构建本体
        await go("library")
        t = await pg.evaluate("document.getElementById('p_library').innerText")
        (ok if ("built_" in t or "本体" in t) else bad)("library 本体库")
        # actioncenter:KPI+类型表+类型编辑弹窗
        await go("actioncenter", 2000)
        t = await pg.evaluate("document.getElementById('p_actioncenter').innerText")
        (ok if ("待批" in t or "已执行" in t) else bad)("actioncenter KPI")
        m = await pg.evaluate("!!document.getElementById('at_modal')")
        if m:
            await pg.evaluate("atEdit&&atEdit('freeze_batch')")
            await pg.wait_for_timeout(900)
            vis = await pg.evaluate("(d=>d&&d.style.display!=='none')(document.getElementById('at_modal'))")
            await pg.evaluate("document.getElementById('at_modal').style.display='none'")
            (ok if vis else bad)("actioncenter 类型编辑弹窗")
        # qaeval:缓存 KPI 三组
        await go("qaeval", 2000)
        t = await pg.evaluate("document.getElementById('p_qaeval').innerText")
        (ok if ("8/8" in t or "尚未跑过" in t) else bad)("qaeval KPI 或空态引导", t[:60])
        # assistant:角色卡+待办
        await go("assistant", 2000)
        t = await pg.evaluate("document.getElementById('p_assistant').innerText")
        (ok if ("生产主管" in t or "质量工程师" in t) else bad)("assistant 角色卡")
        # enginecfg:掩码 key
        await go("enginecfg", 2000)
        t = await pg.evaluate("document.getElementById('p_enginecfg').innerText")
        (ok if ("运行时" in t or "runtime" in t.lower()) else bad)("enginecfg 渲染")
        leak = await pg.evaluate("[...document.querySelectorAll('#p_enginecfg input')].filter(i=>/KEY/i.test(i.id||'')).some(i=>i.value.length>8&&!i.value.includes('*'))")
        (ok if not leak else bad)("enginecfg 无明文 key(空或掩码)")
        # conn:连接列表
        await go("conn")
        t = await pg.evaluate("document.getElementById('p_conn').innerText")
        (ok if ("示例" in t or "sqlite" in t.lower()) else bad)("conn 连接列表")
        # viz:跑一个默认图
        await go("viz")
        btn = await pg.evaluate("[...document.querySelectorAll('#p_viz button')].map(b=>b.innerText).slice(0,6)")
        if any("生成" in x or "运行" in x or "出图" in x for x in btn):
            await pg.click("#p_viz button:has-text('生成'), #p_viz button:has-text('运行'), #p_viz button:has-text('出图')")
            await pg.wait_for_timeout(2500)
            c = await pg.evaluate("!!document.querySelector('#p_viz canvas')")
            (ok if c else bad)("viz 出图(canvas)")
        else: bad("viz 无运行按钮", str(btn))
        # apis:接口目录
        await go("apis")
        rows = await pg.evaluate("document.querySelectorAll('#p_apis tr').length")
        (ok if rows>50 else bad)(f"apis 接口目录({rows}行)")
        # jobs / sysadmin / rules / layers / ontquality / skills / agents
        await go("jobs"); ok("jobs 渲染") if (await pg.evaluate("document.getElementById('p_jobs').innerText.length"))>10 else bad("jobs","empty")
        await go("sysadmin", 2000)
        t = await pg.evaluate("document.getElementById('p_sysadmin').innerText")
        (ok if ("health" in t.lower() or "ok" in t.lower() or "环境" in t) else bad)("sysadmin 健康")
        await go("ontquality", 2500)
        t = await pg.evaluate("document.getElementById('p_ontquality').innerText")
        (ok if ("100" in t or "完备" in t) else bad)("ontquality 记分卡", t[:50])
        await go("skills")
        t = await pg.evaluate("document.getElementById('p_skills').innerText")
        (ok if "ontology" in t else bad)("skills 技能中心", t[:50])
        await go("agents", 2000)
        n = await pg.evaluate("document.querySelectorAll('#ag_list tr').length")
        (ok if n>=2 else bad)(f"agents 列表({n-1}行)")
        await pg.fill("#ag_q", "不存在的名字xx")
        await pg.wait_for_timeout(500)
        n2 = await pg.evaluate("document.querySelectorAll('#ag_list tr').length")
        (ok if n2<n else bad)(f"agents 过滤 {n-1}→{n2-1} 行")

        await b.close()
    print(f"\n===== UI 走查:{len(R['pass'])} 通过 / {len(R['fail'])} 失败 =====")
    for f in R["fail"]: print("  ✗", f)
    sys.exit(1 if R["fail"] else 0)

asyncio.run(main())
