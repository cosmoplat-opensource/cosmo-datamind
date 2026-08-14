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
# 被测地址:优先 DATAMIND_URL,否则由服务端同一套 DATAMIND_HOST/PORT 组合而来
# ——不写死 IP 字面量,免得它与 server 的实际监听配置各自漂移。
BASE = os.environ.get("DATAMIND_URL") or "http://%s:%s" % (
    os.environ.get("DATAMIND_HOST") or "localhost", os.environ.get("DATAMIND_PORT") or "8092")
PAGES = ["home","graph","metrics","catalog","quality","glossary","sqldev","sparql",
         "chat","review","build","library","actioncenter","qaeval","assistant",
         "enginecfg","conn","viz","apis","jobs","sysadmin","rules","layers",
         "ontquality","skills","agents","claw"]

R = {"pass": [], "fail": []}
ALL_ERRORS = []          # 全程累计的页面错误(只增不清),收尾统一汇总
def ok(name, _extra=""):  R["pass"].append(name); print(f"  ✓ {name}")
def bad(name, why=""): R["fail"].append(f"{name} :: {why}"); print(f"  ✗ {name} :: {why}")

async def main():
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        pg = await (await b.new_context(viewport={"width":1440,"height":950})).new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)[:160]))

        FAILED = object()      # 求值失败哨兵:与「合法的假值」区分开

        async def ev(js, dv=None):
            """页面求值守卫:异常或返回 None 时给默认值——加载异常按 fail 走,不让脚本崩溃或静默漏报"""
            try:
                v = await pg.evaluate(js)
                return dv if v is None else v
            except Exception as e:
                errors.append("evaluate:" + str(e)[:120]); return dv

        async def ev_strict(js):
            """安全断言专用:求值失败返回 FAILED 哨兵,绝不退化成看似通过的假值"""
            try:
                v = await pg.evaluate(js)
                return FAILED if v is None else v
            except Exception as e:
                errors.append("evaluate:" + str(e)[:120]); return FAILED
        pg.on("console", lambda m: errors.append("console:"+m.text[:160]) if m.type=="error" else None)

        await pg.goto(BASE, wait_until="domcontentloaded")
        await pg.wait_for_timeout(2500)

        def mark():
            """取一次 errors 基线 —— 全局唯一入口,避免同时存在
            『go() 返回基线』与『手写 len(errors)』两套写法。"""
            return len(errors)

        def new_errs(base):
            """只取基线之后新增的错误——errors 全程只追加不清空,
            前置页面的真实报错得以保留在最终汇总里,不被后续步骤掩盖。"""
            return [e for e in errors[base:] if "favicon" not in e]

        # ── 阶段一:26 页逐页走查 ──────────────────────────────
        print("== 阶段一:全页面渲染 ==")
        for p in PAGES:
            base = mark()
            await ev(f"location.hash='#{p}'")
            await pg.wait_for_timeout(1600)
            vis = await ev(f"(d=>d&&getComputedStyle(d).display!=='none')(document.getElementById('p_{p}'))")
            txt = await ev(f"(d=>d?d.innerText.trim().length:0)(document.getElementById('p_{p}'))")
            errs = new_errs(base)
            if vis and (txt or 0) > 30 and not errs: ok(f"页 {p}(文本 {txt} 字)")
            else: bad(f"页 {p}", f"vis={vis} text={txt} errs={errs[:2]}")

        # ── 阶段二:子 UI 与交互 ──────────────────────────────
        print("== 阶段二:子 UI 交互 ==")
        async def go(p, ms=1500):
            """仅负责切页;需要检查错误的调用点自行 mark() 取基线"""
            await ev(f"location.hash='#{p}'"); await pg.wait_for_timeout(ms)

        # home:KPI 数字
        await go("home")
        k = await ev("document.querySelectorAll('#p_home .kpi .n,#p_home .kpi b').length", -1)
        if not isinstance(k, int) or k < 0: bad("home KPI 卡", f"求值失败(k={k!r})")
        elif k >= 3: ok(f"home KPI 卡({k})")
        else: bad("home KPI 卡", f"仅 {k} 个")

        # graph:图谱画布 + 图谱切换下拉 + 节点详情(经 JS 触发)
        await go("graph", 2500)
        canvas = await ev("!!document.querySelector('#p_graph canvas')", False)
        opts = await ev("(s=>s?s.options.length:0)(document.querySelector('#p_graph select'))", 0)
        (ok if canvas and opts>=2 else bad)(f"graph 画布+{opts}图谱源")
        # metrics:行点击出详情
        await go("metrics")
        rows = await ev("document.querySelectorAll('#p_metrics tr').length", 0)
        if rows>3:
            base = mark()
            clicked = await ev("(r=>{if(!r)return false;r.click();return true})"
                               "(document.querySelectorAll('#p_metrics tbody tr, #p_metrics tr')[1])", False)
            await pg.wait_for_timeout(900)
            errs = new_errs(base)
            if clicked and not errs: ok(f"metrics {rows}行+行点击")
            else: bad("metrics 行点击", f"clicked={clicked} errs={errs[:1]}")
        else: bad("metrics 行数", str(rows))
        # catalog:表清单+预览
        await go("catalog")
        rows = await ev("document.querySelectorAll('#p_catalog tr').length", 0)
        (ok if rows>50 else bad)(f"catalog 表清单({rows}行)")
        # 末列须在可视区内:抬字号会撑宽首列(长表名 nowrap),把「列」挤出容器需横向滚动才看得到
        _lastcol = await ev("""(()=>{const t=document.querySelector('#p_catalog table');
            if(!t)return null;const box=t.closest('div');const last=[...t.querySelectorAll('th')].pop();
            if(!last)return null;
            return JSON.stringify({c:last.innerText.trim(),
              vis:last.getBoundingClientRect().right<=box.getBoundingClientRect().right+1});})()""", '')
        (ok if _lastcol and '"vis":true' in _lastcol else bad)("catalog 表格末列未被挤出可视区", str(_lastcol))
        # quality:分层对账告警在列
        await go("quality", 2200)
        t = await ev("document.getElementById('p_quality').innerText", '')
        (ok if "分层对账" in t else bad)("quality 含分层对账告警", t[:60])
        # glossary:搜索过滤
        await go("glossary")
        n0 = await ev("document.querySelectorAll('#p_glossary tr').length", 0)
        await pg.fill("#gl_q", "直通")
        await pg.wait_for_timeout(700)
        n1 = await ev("document.querySelectorAll('#p_glossary tr').length", 0)
        (ok if 0<n1<n0 else bad)(f"glossary 过滤 {n0}→{n1}")
        # sqldev:执行只读 SQL
        await go("sqldev")
        await pg.fill("#p_sqldev textarea", "SELECT COUNT(*) AS n FROM dim_customer")
        await pg.click("#p_sqldev button:has-text('运行')")
        await pg.wait_for_timeout(1800)
        t = await ev("document.getElementById('p_sqldev').innerText", '')
        (ok if ("n" in t and any(c.isdigit() for c in t)) else bad)("sqldev 运行出结果")
        # sparql
        await go("sparql")
        await pg.click("#p_sparql button:has-text('执行')")
        await pg.wait_for_timeout(2500)
        t = await ev("document.getElementById('p_sparql').innerText", '')
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
        t = await ev("document.getElementById('p_review').innerText", '')
        (ok if ("待审" in t or "已通过" in t) else bad)("review 评审台", t[:50])
        # build:构建页 + 技能对比弹窗开合 + 技能管理列表
        await go("build", 2000)
        has = await ev("!!document.getElementById('skc_modal')")
        if has:
            await ev("skcOpen&&skcOpen()")
            await pg.wait_for_timeout(1200)
            vis = await ev("document.getElementById('skc_modal').style.display!=='none'")
            await ev("document.getElementById('skc_modal').style.display='none'")
            (ok if vis else bad)("build 技能对比弹窗开合")
        skl = await ev("document.querySelectorAll('#bc_skills div,#bc_skills label,#bc_skills input').length", 0)
        (ok if skl>0 else bad)(f"build 技能清单({skl})")
        # 布局塌陷是"元素都在、就是没法看"的一类故障,计数断言抓不到:全局
        # input{width:100%} 曾把勾选框撑到 224px,把同排文本挤成 0 宽,描述逐字竖排
        # (单张技能卡高 557px)。用几何量守住:勾选框按内容定宽、文本有可读宽度。
        geo = await ev("""(()=>{const l=document.querySelector('#bc_skills .bc-skcard');
            if(!l)return null;const cb=l.querySelector('input[type=checkbox]');
            const tx=l.querySelector('div');
            return {cb:cb?cb.getBoundingClientRect().width:-1,
                    tx:tx?tx.getBoundingClientRect().width:-1,
                    h:l.getBoundingClientRect().height};})()""", None)
        (ok if geo and geo["cb"]<=24 else bad)("build 技能勾选框未被全局 input 宽度撑开",
                                               f"{geo}")
        (ok if geo and geo["tx"]>=120 else bad)("build 技能描述有可读宽度(未被挤成竖排)",
                                                f"{geo}")
        (ok if geo and geo["h"]<=160 else bad)("build 单张技能卡高度正常", f"{geo}")
        # library:已构建本体
        await go("library")
        t = await ev("document.getElementById('p_library').innerText", '')
        (ok if ("built_" in t or "本体" in t) else bad)("library 本体库")
        # actioncenter:KPI+类型表+类型编辑弹窗
        await go("actioncenter", 2000)
        t = await ev("document.getElementById('p_actioncenter').innerText", '')
        (ok if ("待批" in t or "已执行" in t) else bad)("actioncenter KPI")
        m = await ev("!!document.getElementById('at_modal')")
        if m:
            await ev("atEdit&&atEdit('freeze_batch')")
            await pg.wait_for_timeout(900)
            vis = await ev("(d=>d&&d.style.display!=='none')(document.getElementById('at_modal'))")
            await ev("document.getElementById('at_modal').style.display='none'")
            (ok if vis else bad)("actioncenter 类型编辑弹窗")
        # qaeval:缓存 KPI 三组
        await go("qaeval", 2000)
        t = await ev("document.getElementById('p_qaeval').innerText", '')
        (ok if ("8/8" in t or "尚未跑过" in t) else bad)("qaeval KPI 或空态引导", t[:60])
        # assistant:角色卡+待办
        await go("assistant", 2000)
        t = await ev("document.getElementById('p_assistant').innerText", '')
        (ok if ("生产主管" in t or "质量工程师" in t) else bad)("assistant 角色卡")
        # enginecfg:掩码 key
        await go("enginecfg", 2000)
        t = await ev("document.getElementById('p_enginecfg').innerText", '')
        (ok if ("运行时" in t or "runtime" in t.lower()) else bad)("enginecfg 渲染")
        leak = await ev_strict("[...document.querySelectorAll('#p_enginecfg input')]"
                               ".filter(i=>/KEY/i.test(i.id||'')).some(i=>i.value.length>8&&!i.value.includes('*'))")
        if leak is FAILED: bad("enginecfg 无明文 key", "求值失败,安全断言不可退化为通过")
        elif leak: bad("enginecfg 无明文 key", "检出未掩码的 key 输入框")
        else: ok("enginecfg 无明文 key(空或掩码)")
        # conn:连接列表
        await go("conn")
        t = await ev("document.getElementById('p_conn').innerText", '')
        (ok if ("示例" in t or "sqlite" in t.lower()) else bad)("conn 连接列表")
        # viz:跑一个默认图
        await go("viz")
        btn = await ev("[...document.querySelectorAll('#p_viz button')].map(b=>b.innerText).slice(0,6)", [])
        if any("生成" in x or "运行" in x or "出图" in x for x in btn):
            await pg.click("#p_viz button:has-text('生成'), #p_viz button:has-text('运行'), #p_viz button:has-text('出图')")
            await pg.wait_for_timeout(2500)
            c = await ev("!!document.querySelector('#p_viz canvas')", False)
            (ok if c else bad)("viz 出图(canvas)")
        else: bad("viz 无运行按钮", str(btn))
        # apis:接口目录
        await go("apis")
        rows = await ev("document.querySelectorAll('#p_apis tr').length", 0)
        (ok if rows>50 else bad)(f"apis 接口目录({rows}行)")
        # jobs / sysadmin / rules / layers / ontquality / skills / agents
        await go("jobs"); ok("jobs 渲染") if (await ev("document.getElementById('p_jobs').innerText.length", 0))>10 else bad("jobs","empty")
        await go("sysadmin", 2000)
        t = await ev("document.getElementById('p_sysadmin').innerText", '')
        (ok if ("health" in t.lower() or "ok" in t.lower() or "环境" in t) else bad)("sysadmin 健康")
        await go("ontquality", 2500)
        t = await ev("document.getElementById('p_ontquality').innerText", '')
        (ok if ("100" in t or "完备" in t) else bad)("ontquality 记分卡", t[:50])
        await go("skills")
        t = await ev("document.getElementById('p_skills').innerText", '')
        (ok if "ontology" in t else bad)("skills 技能中心", t[:50])
        # claw:本体对话 + 审计面板(DR-027)
        await go("claw", 2500)
        t = await ev("document.getElementById('p_claw').innerText", '')
        (ok if ("本体对话" in t or "会话" in t) else bad)("claw 对话页渲染")
        aud = await ev("document.getElementById('claw_audit').innerText", '')
        (ok if "变更" in aud else bad)("claw 审计面板出数", aud[:40])
        (ok if ("需复核" in aud or "来源" in aud) else bad)("claw 审计含来源/风险维度")
        await go("agents", 2000)
        n = await ev("document.querySelectorAll('#ag_list tr').length", 0)
        (ok if n>=2 else bad)(f"agents 列表({n-1}行)")
        await pg.fill("#ag_q", "不存在的名字xx")
        await pg.wait_for_timeout(500)
        n2 = await ev("document.querySelectorAll('#ag_list tr').length", 0)
        (ok if n2<n else bad)(f"agents 过滤 {n-1}→{n2-1} 行")

        # ══ 全站布局体检:把"元素都在、就是没法看"这类故障变成可判定的几何量 ══
        # 起因是技能编排面板 —— 全局 input{width:100%} 把勾选框撑到 224px,同排文本
        # 被挤成 0 宽、逐字竖排;而当时的计数断言 25 个元素全绿。
        SQUEEZE = """(()=>{const bad=[];
          document.querySelectorAll('.page').forEach(pg=>{
            if(getComputedStyle(pg).display==='none')return;
            pg.querySelectorAll('*').forEach(e=>{
              const r=e.getBoundingClientRect();
              // 只按高度判可见:宽度恰为 0 正是要抓的塌陷形态,
              // 若把 width<=0 也当作"不可见"跳过,最严重的那种反而漏检(实测漏过)
              if(r.height<=0)return;
              const cs=getComputedStyle(e);
              if(cs.visibility==='hidden'||cs.display==='none')return;
              const own=[...e.childNodes].filter(n=>n.nodeType===3)
                        .map(n=>n.textContent.trim()).join('');
              const lh=parseFloat(cs.lineHeight)||parseFloat(cs.fontSize)*1.4||18;
              if(own.length>=4&&r.width<Math.max(parseFloat(cs.fontSize)*2.2,8)&&r.height>lh*3)
                bad.push(e.tagName+'.'+(e.className||'').toString().slice(0,20)+
                         ' w='+r.width.toFixed(0)+' h='+r.height.toFixed(0)+
                         ' 「'+own.slice(0,12)+'」');});});
          return bad;})()"""
        sq_all = []
        for _pn in ("home","build","chat","graph","review","quality","metrics",
                    "catalog","skills","enginecfg","actioncenter","library"):
            await go(_pn, 1500)
            sq_all += await ev(SQUEEZE, [])
        (ok if not sq_all else bad)("全站无文本被挤成竖排", "; ".join(sq_all[:3]))

        # 单元格统一 260px 截断,超长内容靠悬停补 title 才看得到全文;
        # 若哪天 title 不再补上,结论就会被截在半句话上而无从查看。
        await go("quality", 2200)
        cut = await ev("""(()=>{const c=[...document.querySelectorAll('#p_quality td')]
            .filter(e=>e.scrollWidth-e.clientWidth>2);return c.length?1:0;})()""", 0)
        if cut:
            await pg.hover("#p_quality td:below(:text('详情'))" if False else
                           "#p_quality table td")
            got = await ev("""(()=>{const c=[...document.querySelectorAll('#p_quality td')]
                .filter(e=>e.scrollWidth-e.clientWidth>2);
                return c.every(e=>{e.dispatchEvent(new MouseEvent('mouseover',{bubbles:true}));
                                   return (e.title||'').length>0;});})()""", False)
            (ok if got else bad)("被截断的单元格悬停后可见全文(补 title)")
        else:
            ok("质量页当前无被截断单元格(无需补 title)")

        ALL_ERRORS.extend(e for e in errors if "favicon" not in e)
        await b.close()
    print(f"\n===== UI 走查:{len(R['pass'])} 通过 / {len(R['fail'])} 失败 =====")
    for f in R["fail"]: print("  ✗", f)
    if ALL_ERRORS:
        print(f"  ── 全程累计页面错误 {len(ALL_ERRORS)} 条(含已归因项,供排查)──")
        for e in ALL_ERRORS[:10]: print("    ·", e)
    sys.exit(1 if R["fail"] else 0)

asyncio.run(main())
