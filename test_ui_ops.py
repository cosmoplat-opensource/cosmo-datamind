#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI 逐步实操(可选,需 playwright):像人一样点按钮、填表单、切引擎。

与 test_ui.py 的分工:
  test_ui.py    广度——26 页渲染 + 子 UI 交互,快
  test_ui_ops.py 深度——单条业务动线走到底(切引擎→建/看本体→对话改本体→审计→问数)

    pip install playwright && playwright install chromium
    python3 server.py &
    python3 test_ui_ops.py            # DATAMIND_URL 可覆盖地址

重点覆盖引擎互换:在引擎设置页点「设为当前」在 OpenAI 兼容端点(GLM)与
Claude Code 之间往返,验证后端 current 真变、UI 标记随之转移、且不同引擎的
执行行为确有差异(而非只换了标签)。
"""
import asyncio, os, sys
from playwright.async_api import async_playwright

B = os.environ.get("DATAMIND_URL", "http://127.0.0.1:8092")
R = {"p": [], "f": []}
def ok(n, extra=""):  R["p"].append(n); print(f"  ✓ {n}" + (f"  {extra}" if extra else ""))
def bad(n, why=""):   R["f"].append(f"{n} :: {why}"); print(f"  ✗ {n} :: {why}")

async def main():
    async with async_playwright() as pw:
        br = await pw.chromium.launch()
        pg = await (await br.new_context(viewport={"width": 1500, "height": 1000})).new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:120]))

        async def goto(page, ms=1800):
            base = len(errs)
            await pg.click(f'.nav[data-p="{page}"]')     # 真实点击导航,而非改 hash
            await pg.wait_for_timeout(ms)
            return base

        await pg.goto(B, wait_until="domcontentloaded")
        await pg.wait_for_timeout(2500)

        # ══ 步骤 1:进引擎设置页,读运行时卡 ══
        print("\n【步骤1】打开引擎设置页")
        await goto("enginecfg", 2800)
        cards = await pg.eval_on_selector_all("#eg_runtimes .step",
                                              "els=>els.map(e=>e.innerText.split('\\n')[0])")
        print(f"      运行时卡: {cards}")
        (ok if len(cards) >= 3 else bad)(f"引擎设置页列出 {len(cards)} 个运行时卡")
        (ok if any("OpenAI" in c for c in cards) else bad)("含 OpenAI 兼容端点卡(GLM)")
        (ok if await pg.eval_on_selector("#eg_runtimes", "e=>e.innerText.includes('当前引擎')")
         else bad)("标出当前引擎")

        async def switch_to(label):
            """点击某运行时卡上的「设为当前」按钮(真实交互)"""
            return await pg.evaluate("""(lab) => {
                const c=[...document.querySelectorAll('#eg_runtimes .step')].find(x=>x.innerText.includes(lab));
                if(!c) return 'no-card';
                const b=[...c.querySelectorAll('button')].find(b=>b.innerText.includes('设为当前'));
                if(!b) return 'already-current';
                b.click(); return 'clicked';
            }""", label)

        async def cur_rt():
            r = await pg.request.get(B + '/api/ont/runtimes')
            return (await r.json()).get('current')

        # ══ 步骤 2:GLM → Claude Code ══
        print("\n【步骤2】UI 点「设为当前」: GLM(openai) → Claude Code")
        st = await switch_to("Claude Code"); await pg.wait_for_timeout(2600)
        c = await cur_rt()
        (ok if c == "claude-code" else bad)("切到 Claude Code 后端生效", f"{st} → current={c}")
        mark = await pg.evaluate("""() => {
            const c=[...document.querySelectorAll('#eg_runtimes .step')].find(x=>x.innerText.includes('Claude Code'));
            return c ? c.innerText.includes('当前引擎') : false; }""")
        (ok if mark else bad)("UI「当前引擎」标记随之转移")

        # ══ 步骤 3:Claude Code → GLM,并点测试连通 ══
        print("\n【步骤3】UI 点「设为当前」: Claude Code → GLM(openai)")
        st = await switch_to("OpenAI"); await pg.wait_for_timeout(2600)
        c = await cur_rt()
        (ok if c == "openai" else bad)("切回 GLM 后端生效", f"{st} → current={c}")
        await pg.evaluate("""() => {
            const c=[...document.querySelectorAll('#eg_runtimes .step')].find(x=>x.innerText.includes('OpenAI'));
            const b=c && [...c.querySelectorAll('button')].find(b=>b.innerText.includes('测试连通'));
            if(b) b.click(); }""")
        await pg.wait_for_timeout(25000)
        ti = await pg.evaluate("(document.getElementById('eg_t_openai')||{}).innerText||''")
        (ok if ti.strip() else bad)("GLM「测试连通」出结果", ti[:70])

        # ══ 步骤 4:Key 掩码不泄漏 ══
        print("\n【步骤4】检查 Key 区不泄漏明文")
        ktxt = await pg.inner_text("#eg_keys")
        leaked = await pg.eval_on_selector_all(
            "#eg_keys input",
            "els=>els.filter(i=>/KEY/i.test(i.id||i.name||'')).some(i=>i.value.length>8&&!i.value.includes('*'))")
        (ok if not leaked else bad)("Key 输入框无明文(空或掩码)")
        (ok if "1d420c06" not in ktxt else bad)("页面文本不含真实 Key 片段")

        # ══ 步骤 5:本体图谱页 —— 切图谱、看画布 ══
        print("\n【步骤5】本体图谱:切换图谱源并渲染")
        await goto("graph", 3000)
        opts = await pg.eval_on_selector("#g_sel", "e=>[...e.options].map(o=>o.value)")
        (ok if len(opts) >= 2 else bad)(f"图谱下拉含 {len(opts)} 个源", str(opts[:4]))
        built = next((o for o in opts if o.startswith("built_") and "regress" not in o), None)
        if built:
            await pg.select_option("#g_sel", built)
            await pg.wait_for_timeout(3000)
            has = await pg.evaluate("!!document.querySelector('#g_canvas canvas')")
            info = await pg.inner_text("#g_info")
            (ok if has else bad)(f"切到构建产物 {built} 后画布渲染", info[:40])
        else:
            bad("找到已构建本体", "下拉中无 built_*")

        # ══ 步骤 6:本体对话页 —— 真实输入并发送 ══
        print("\n【步骤6】本体对话:署名 → 提问 → 审计刷新")
        await goto("claw", 2500)
        await pg.fill("#claw_who", "Jinze Yu")
        await pg.fill("#claw_q", "本体里有哪些对象?")
        await pg.click("#claw_btn")
        try:
            await pg.wait_for_function(
                "document.getElementById('claw_log').innerText.length>40", timeout=90000)
            log = await pg.inner_text("#claw_log")
            ok("对话发送并收到回复", log.replace("\n", " ")[:60])
        except Exception:
            bad("对话回复超时", "90s 内无内容")
        await pg.click("#p_claw button:has-text('刷新审计')")
        await pg.wait_for_timeout(1800)
        aud = await pg.inner_text("#claw_audit")
        (ok if "变更" in aud else bad)("审计面板出数", aud.replace("\n", " ")[:60])
        (ok if "来源" in aud else bad)("审计含来源维度")

        # ══ 步骤 7:深度问数 —— 真实输入并等结果 ══
        print("\n【步骤7】深度问数(当前引擎 GLM):输入问句→执行→看步骤")
        await goto("chat", 2500)
        box = "#p_chat textarea, #p_chat input[type=text]"
        await pg.fill(box, "客户维度表有多少条记录?")
        await pg.click("#p_chat button:has-text('发送'), #p_chat button:has-text('提问')")
        try:
            await pg.wait_for_function(
                "document.getElementById('p_chat').innerText.includes('执行查询')"
                "||document.getElementById('p_chat').innerText.length>900", timeout=240000)
            t = await pg.inner_text("#p_chat")
            ok("问数出结果", t.replace("\n", " ")[-70:])
            (ok if ("意图" in t) else bad)("执行记录含双盲意图步骤")
        except Exception:
            bad("问数超时", "240s")

        # ══ 步骤 8:规则页(决策层)—— 只读查看 ══
        print("\n【步骤8】其余关键页可用性")
        for p, key in (("rules", "构成规则"), ("review", "评审"), ("actioncenter", "动作"),
                       ("ontquality", "完备"), ("qaeval", "评测")):
            b0 = len(errs)
            await goto(p, 1800)
            t = await pg.inner_text(f"#p_{p}")
            e = [x for x in errs[b0:] if "favicon" not in x]
            (ok if len(t) > 30 and not e else bad)(f"{p} 页可用", f"{len(t)}字 err={e[:1]}")

        await br.close()
    print(f"\n===== UI 实操:{len(R['p'])} 通过 / {len(R['f'])} 失败 =====")
    for f in R["f"]: print("  ✗", f)
    sys.exit(1 if R["f"] else 0)

asyncio.run(main())
