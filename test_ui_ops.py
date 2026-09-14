#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI 逐步实操(可选,需 playwright):像人一样点按钮、填表单、切引擎。

与 test_ui.py 的分工:
  test_ui.py    广度——26 页渲染 + 子 UI 交互,快
  test_ui_ops.py 深度——单条业务动线走到底(切引擎→建/看本体→对话改本体→审计→问数)

    pip install playwright && playwright install chromium
    python3 server.py &
    python3 test_ui_ops.py            # DATAMIND_URL 可覆盖地址

重点覆盖引擎互换:在引擎设置页点「设为当前」，在已配置的运行时之间
往返，验证后端 current 真正变更、UI 标记随之转移，并在结束时恢复测试前的运行时。
"""
# 等待上限按引擎实测时延取(GLM 单轮问数 ~70s,含推理开销),而非按理想值
import asyncio, os, sys
from tests.browser_runtime import close_browser, managed_playwright

# 被测地址:优先 DATAMIND_URL,否则由服务端同一套 DATAMIND_HOST/PORT 组合而来
# ——不写死 IP 字面量,免得它与 server 的实际监听配置各自漂移。
B = os.environ.get("DATAMIND_URL") or "http://%s:%s" % (
    os.environ.get("DATAMIND_HOST") or "localhost", os.environ.get("DATAMIND_PORT") or "8092")
R = {"p": [], "f": [], "s": []}
def ok(n, extra=""):  R["p"].append(n); print(f"  ✓ {n}" + (f"  {extra}" if extra else ""))
def bad(n, why=""):   R["f"].append(f"{n} :: {why}"); print(f"  ✗ {n} :: {why}")
# 跳过:该断言依赖**本机未配置**的外部能力(如 OpenAI 兼容端点需 DATAMIND_LLM_BASE/_KEY)。
# 记为 skip 而非 fail —— 未配端点是部署选择,不是代码缺陷;但必须显式列出,不许静默消失。
def skip(n, why=""):  R["s"].append(f"{n} :: {why}"); print(f"  ⊘ {n} :: {why}(环境未配,跳过)")


async def launch_browser(pw):
    """优先复用本机浏览器；未找到时回落 Playwright 自带 Chromium。"""
    candidates = [os.environ.get("DATAMIND_BROWSER_EXECUTABLE"),
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"]
    executable = next((path for path in candidates if path and os.path.isfile(path)), None)
    return await pw.chromium.launch(executable_path=executable) if executable else await pw.chromium.launch()

async def main():
    async with managed_playwright() as pw:
        br = await launch_browser(pw)
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
        await pg.locator("#eg_runtimes h3").wait_for(timeout=10000)
        cards = await pg.eval_on_selector_all("#eg_runtimes .step",
                                              "els=>els.map(e=>e.innerText.split('\\n')[0])")
        print(f"      运行时卡: {cards}")
        _rt = await (await pg.request.get(B + '/api/ont/runtimes')).json()
        available_runtimes = _rt.get("runtimes")
        if not isinstance(available_runtimes, list):
            raise AssertionError(f"运行时接口格式错误: {_rt}")
        card_ids = await pg.locator("#eg_runtimes [data-runtime]").evaluate_all("els=>els.map(e=>e.dataset.runtime)")
        (ok if sorted(card_ids) == sorted(available_runtimes) else bad)(
            f"引擎卡与已注册运行时一致({len(cards)}个)", f"UI={card_ids}, API={available_runtimes}")
        initial_rt = _rt.get("current")
        initial_ready = initial_rt in available_runtimes
        HAS_OPENAI = "openai" in (_rt.get("runtimes") or [])
        (ok if any("OpenAI" in c for c in cards) else (bad if HAS_OPENAI else
         (lambda n: skip(n, "未配 DATAMIND_LLM_BASE/_KEY")))) ("含 OpenAI 兼容端点卡(GLM)")
        current_cards = await pg.locator("#eg_runtimes [data-runtime]").evaluate_all(
            "els=>els.filter(e=>e.innerText.includes('当前引擎')).map(e=>e.dataset.runtime)")
        if initial_ready:
            (ok if current_cards == [initial_rt] else bad)("标出已注册的当前引擎", str(current_cards))
        else:
            empty_text = await pg.inner_text("#eg_runtimes")
            (ok if not current_cards and "未注册" in empty_text and "退回内置模板" in empty_text else bad)(
                "未注册当前引擎明确显示模板回退", empty_text[:140])

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

        print("\n【步骤2】运行时切换及未注册状态保护")
        # Missing engines are a supported deployment mode, verified by rejection/state checks.
        # Mutation is only attempted when the original runtime can be restored afterwards.
        target = next((name for name in ["claude-code", *available_runtimes]
                       if initial_ready and name in available_runtimes and name != initial_rt), None)
        if target:
            async with pg.expect_response(lambda response: response.url.endswith('/api/engine/config') and response.request.method == 'POST'):
                await pg.locator(f'#eg_runtimes [data-runtime="{target}"]').get_by_role("button", name="设为当前", exact=True).click()
            await pg.wait_for_function("target=>EG&&EG.driver===target", arg=target)
            c = await cur_rt()
            (ok if c == target else bad)("切换已注册引擎后端生效", f"target={target}, current={c}")
            marked = await pg.locator(f'#eg_runtimes [data-runtime="{target}"]').inner_text()
            (ok if "当前引擎" in marked else bad)("UI「当前引擎」标记随之转移")
        else:
            invalid = "__ui_unregistered_runtime__"
            async with pg.expect_response(lambda response: response.url.endswith('/api/engine/config') and response.request.method == 'POST') as rejected_info:
                await pg.evaluate("name=>egSwitch(name)", invalid)
            rejected = await rejected_info.value
            payload = await rejected.json()
            (ok if rejected.status == 400 and payload.get("error") else bad)(
                "未注册运行时切换被明确拒绝", f"HTTP {rejected.status}")
            (ok if await cur_rt() == initial_rt else bad)("拒绝后当前引擎保持不变")

        # ══ 步骤 3:Claude Code → GLM,并点测试连通 ══
        print("\n【步骤3】UI 点「设为当前」: Claude Code → GLM(openai)")
        if not HAS_OPENAI:
            skip("切回 GLM 后端生效", "未配 OpenAI 兼容端点")
            skip("GLM「测试连通」出结果", "未配 OpenAI 兼容端点")
        else:
            if initial_ready:
                st = await switch_to("OpenAI"); await pg.wait_for_timeout(2600)
                c = await cur_rt()
                (ok if c == "openai" else bad)("切回 GLM 后端生效", f"{st} → current={c}")
            else:
                (ok if await cur_rt() == initial_rt else bad)("连通测试保留未注册的初始运行时配置")
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
        # 用当前配置的 Key 前缀比对,不把真实 Key 片段写进仓库:
        # 断言本身若带着真 Key 的头几位,开源出去就是一次泄漏
        _kpre = (os.environ.get("DATAMIND_LLM_KEY") or "")[:8]
        (ok if not _kpre or _kpre not in ktxt else bad)("页面文本不含真实 Key 片段")

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
        if built:
            await pg.select_option("#claw_graph", built)
            selected = await pg.input_value("#claw_graph")
            (ok if selected == built else bad)(f"本体对话绑定构建产物 {built}", selected)
        await pg.fill("#claw_who", "测试评审员")
        await pg.fill("#claw_q", "本体里有哪些对象?")
        await pg.click("#claw_btn")
        try:
            await pg.wait_for_function(
                "(()=>{const b=document.getElementById('claw_btn'),"
                "l=document.getElementById('claw_log');return b&&!b.disabled&&l&&"
                "l.querySelectorAll('.msg-ai').length>0&&"
                "!l.innerText.includes('智能引擎处理中…')})()", timeout=300000)
            log = await pg.inner_text("#claw_log")
            ok("对话发送并收到回复", log.replace("\n", " ")[:60])
        except Exception:
            bad("对话回复超时", "300s 内无内容")
        await pg.click("#p_claw button:has-text('刷新审计')")
        await pg.wait_for_timeout(1800)
        aud = await pg.inner_text("#claw_audit")
        (ok if "变更" in aud else bad)("审计面板出数", aud.replace("\n", " ")[:60])
        (ok if "来源" in aud else bad)("审计含来源维度")

        # ══ 步骤 7:深度问数 —— 真实输入并等结果 ══
        print(f"\n【步骤7】深度问数(当前引擎 {await cur_rt()}):输入问句→执行→看步骤")
        await goto("chat", 2500)
        box = "#p_chat textarea, #p_chat input[type=text]"
        n0 = await pg.evaluate("document.querySelectorAll('#p_chat .dq-card').length")
        await pg.fill(box, "客户维度表有多少条记录?")
        await pg.click("#p_chat button:has-text('发送'), #p_chat button:has-text('提问')")
        try:
            await pg.wait_for_function(
                f"document.querySelectorAll('#p_chat .dq-card').length>{n0}"
                "&&!document.getElementById('chat_btn').disabled", timeout=420000)
            # 执行记录出结果后是折叠的,innerText 读不到——先展开再断言
            await pg.evaluate("document.querySelectorAll('#p_chat details').forEach(d=>d.open=true)")
            await pg.wait_for_timeout(300)
            t = await pg.inner_text("#p_chat")
            ok("问数出结果", t.replace("\n", " ")[-70:])
            (ok if ("意图" in t) else bad)("执行记录含双盲意图步骤")
            # DR-032 锚定可视化改版后叫「本体如何约束本次分析」:四步链路 + 可展开的技术证据
            (ok if ("本体如何约束本次分析" in t) else bad)("对话框内出现本体约束区块")
            nsvg = await pg.evaluate(
                "document.querySelectorAll('#p_chat .dq-card svg rect').length")
            (ok if nsvg > 0 else bad)("锚定图渲染出对象节点", f"{nsvg} 个方框")
            nline = await pg.evaluate(
                "document.querySelectorAll('#p_chat .dq-card svg line').length")
            ok("锚定图关系连线", f"{nline} 条")
            (ok if ("候选连接依据" in t or nline == 0) else bad)("有关系时给出候选连接依据")
        except Exception as _e:
            bad("问数超时", f"420s {_e}")

        # ══ 步骤 7b:选中本体图谱后再问 —— 验证锚定确实换了本体(DR-033)══
        print("\n【步骤7b】数据源里点选自建本体 → 锚定本体应随之切换")
        await pg.click("#dq_src_chip")
        await pg.wait_for_timeout(900)
        cards = await pg.query_selector_all("#dq_ds_body .ds-card")
        picked = None
        for c in cards:
            gid = await c.get_attribute("data-g")
            nm = await (await c.query_selector(".nm")).inner_text()
            if gid and gid.startswith("built_"):
                picked = (gid, nm); await c.click(); break
        (ok if picked else bad)("数据源弹窗可点选本体图谱", str(picked))
        if not picked:          # 拿不到图谱就别往下崩,后续断言全部依赖它
            bad("步骤7b 中止", "未取到可选的自建本体图谱")
            picked = ("", "")
        await pg.click("#dq_dsmodal button:has-text('确定')")
        await pg.wait_for_timeout(600)
        sel = await pg.evaluate("DQ_DS.graphs")
        (ok if picked[0] and sel == [picked[0]] else bad)("选中状态已回写", str(sel))
        n1 = await pg.evaluate("document.querySelectorAll('#p_chat .dq-card').length")
        await pg.fill(box, "各客户的销售订单金额排名")
        await pg.click("#p_chat button:has-text('发送'), #p_chat button:has-text('提问')")
        try:
            await pg.wait_for_function(
                f"document.querySelectorAll('#p_chat .dq-card').length>{n1}"
                "&&!document.getElementById('chat_btn').disabled", timeout=420000)
            await pg.evaluate("document.querySelectorAll('#p_chat details').forEach(d=>d.open=true)")
            await pg.wait_for_timeout(400)
            t2 = await pg.inner_text("#p_chat .dq-card:last-of-type")
            # 锚定的必须是刚选中的那套本体,而不是永远的示例本体
            (ok if picked[1][:8] in t2 else bad)(
                "锚定本体名 = 选中的图谱", f"{picked[1][:12]} | 卡片: {t2[:120]}")
            (ok if "示例企业数据本体" not in t2 else bad)("未回落到示例本体")
            (ok if "锚定本体" in t2 else bad)("执行记录含『锚定本体』步骤")
            # 改版后的链路是固定四步:分析问题→本体范围→进入规划→SQL 实际执行
            _steps = [x for x in ("分析问题", "本体范围", "进入规划", "SQL 实际执行") if x in t2]
            (ok if len(_steps) == 4 else bad)(
                "约束链路四步可见(分析→范围→规划→实际执行)", "命中:" + "、".join(_steps))
            nrect = await pg.evaluate(
                "document.querySelectorAll('#p_chat .dq-card:last-of-type svg rect').length")
            (ok if nrect > 0 else bad)("选中本体后仍画出锚定子图", f"{nrect} 框")
            # 可见性:锚定必须在视口内 —— 埋在气泡里会被自动滚动顶出屏幕(实测 top=-380)
            vis = await pg.evaluate(
                "(()=>{const b=document.getElementById('dq_ancbar');"
                "if(!b||b.style.display==='none')return null;const r=b.getBoundingClientRect();"
                "return JSON.stringify({t:Math.round(r.top),inview:r.top>=0&&r.top<window.innerHeight});})()")
            (ok if vis and '"inview":true' in vis else bad)("常驻锚定条在视口内", str(vis))
            bt = await pg.inner_text("#dq_ancbar")
            (ok if picked[1][:8] in bt else bad)("锚定条显示锚定本体名", bt[:40])
            # 改版后的常驻条:规划候选 / SQL 实际使用 / JOIN 三枚数字牌
            (ok if ("规划候选" in bt and "SQL 实际使用" in bt) else bad)(
                "锚定条显示规划候选与实际使用", bt[:60])
            ok("锚定条反馈流程标记", "有 JOIN 计数" if "JOIN" in bt else "无 JOIN(合法)")
            await pg.click("#dq_ancbar button:has-text('查看约束说明')")
            await pg.wait_for_timeout(600)
            nb = await pg.evaluate("document.querySelectorAll('#dq_ancbar svg rect').length")
            (ok if nb > 0 else bad)("约束说明内含关系图(技术证据)", f"{nb} 框")
            # 「选了本体的哪一块」:跳图谱页定位锚定子集,其余淡出
            await pg.click("#dq_ancbar button:has-text('在图谱中定位')")
            await pg.wait_for_timeout(4000)
            hb = await pg.inner_text("#g_hlbar")
            (ok if "问数锚定视图" in hb else bad)("图谱页出现锚定高亮提示", hb[:70])
            import re as _re
            m = _re.search(r"的\s*(\d+)/(\d+)\s*个对象", hb)
            # 小本体可能被整体召回(N==M),那是正确结果;要守的是 0<N<=M
            (ok if m and 0 < int(m.group(1)) <= int(m.group(2)) else bad)(
                "高亮数量有据且不超过本体规模", m.group(0) if m else hb[:50])
            if m and m.group(1) == m.group(2):
                ok("本体被整体召回", "%s —— 小本体的正常结果,看不出淡出对比" % m.group(0))
            (ok if "清除高亮" in hb else bad)("高亮可清除")
        except Exception as _e:
            bad("选中本体后问数或断言失败", f"{_e}")

        # ══ 步骤 7c:点示例必须真跑,不能秒回缓存 ══
        print("\n【步骤7c】新对话点示例:应真跑一遍,而不是给缓存记录")
        await goto("chat", 1500)                      # 7b 结尾跳去了本体图谱页,先回来
        await pg.click("#p_chat .dq-tab .c")          # 新对话
        await pg.wait_for_timeout(800)
        conv0 = await pg.evaluate("dqAllConv().length")
        (ok if await pg.evaluate("DQ_CONV") is None else bad)("点新对话后会话已重置", "DQ_CONV=null")
        eg = await pg.query_selector("#p_chat .dq-hero .eg")
        if not eg:
            # 改版后:会话范围锚定了具体本体时,欢迎页刻意不出通用示例(与该本体多半不匹配)。
            scoped = await pg.evaluate("(typeof DQ_DS!=='undefined')&&!!(DQ_DS&&(DQ_DS.graphs||[]).length)")
            (ok if scoped else bad)(
                "新对话示例的出现与会话范围一致",
                "已锚定本体,按设计不出通用示例" if scoped else "无范围却也无示例")
        else:
            egq = await eg.inner_text()
            n2 = await pg.evaluate("document.querySelectorAll('#p_chat .dq-card').length")
            await eg.click()
            try:
                await pg.wait_for_function(
                    f"document.querySelectorAll('#p_chat .dq-card').length>{n2}"
                    "&&!document.getElementById('chat_btn').disabled", timeout=420000)
                await pg.wait_for_timeout(400)
                t3 = await pg.inner_text("#p_chat .dq-card:last-of-type")
                (ok if "缓存·秒回" not in t3 else bad)(
                    f"点示例「{egq[:12]}」是真跑非缓存", t3.split("\n")[2] if len(t3.split("\n")) > 2 else "")
                # 手动输入同一问题也必须真跑:秒回的旧答案与历史对话里的记录无法区分
                n3 = await pg.evaluate("document.querySelectorAll('#p_chat .dq-card').length")
                await pg.fill(box, egq)
                await pg.click("#p_chat button:has-text('发送'), #p_chat button:has-text('提问')")
                await pg.wait_for_function(
                    f"document.querySelectorAll('#p_chat .dq-card').length>{n3}"
                    "&&!document.getElementById('chat_btn').disabled", timeout=420000)
                await pg.wait_for_timeout(300)
                t4 = await pg.inner_text("#p_chat .dq-card:last-of-type")
                (ok if "缓存·秒回" not in t4 else bad)("重复提问同样真跑,不给缓存答案")
                (ok if "本体如何约束本次分析" in t3 else bad)("示例问数同样给出本体约束区块")
                # 「新对话 + 点示例」应当自成一条流程,并落进历史对话
                conv1 = await pg.evaluate("dqAllConv().length")
                (ok if conv1 == conv0 + 1 else bad)("示例问数自建一条新会话", f"{conv0}→{conv1}")
                titles = await pg.evaluate("dqAllConv().map(c=>c.title)")
                (ok if titles and egq[:8] in titles[0] else bad)(
                    "新会话标题取自示例问题", str(titles[:2]))
            except Exception as _e:
                bad("示例问数超时", f"420s {_e}")

        # ══ 步骤 7d:继续构建 —— 点了之后界面必须看得出「这轮会并入哪张图」══
        # 背景:此前点「继续构建」只在顶部加一条细线,对话区仍是「新建本体」的欢迎页与
        # 示例,名称框还留着上一个新建名——用户无从判断自己是否真的在继续构建。
        print("\n【步骤7d】继续构建的模式可见性")
        await goto("build", 2200)
        clicked = await pg.evaluate("""()=>{const rs=[...document.querySelectorAll('#bc_built .bc-srow')];
          const a=rs[0]&&[...rs[0].querySelectorAll('a')].find(x=>x.textContent.trim()==='继续构建');
          if(!a)return null; a.click();
          return rs[0].querySelector('.nm')?.textContent.trim()||'';}""")
        if not clicked:
            skip("继续构建入口", "无已构建本体可迭代")
        else:
            await pg.wait_for_timeout(2200)
            base = await pg.evaluate("BC.base")
            btn = (await pg.inner_text("#bc_send")).strip()
            dest = await pg.inner_text("#bc_cur_dest")
            body = await pg.inner_text("#bc_log")
            nm_dis = await pg.eval_on_selector("#bc_name", "e=>e.disabled")
            ph = await pg.eval_on_selector("#bc_q", "e=>e.placeholder")
            (ok if base else bad)("继续构建设定底本", base or "BC.base 为空")
            (ok if "继续构建" in btn else bad)("构建按钮改为「继续构建」", btn)
            (ok if "并入" in dest else bad)("底部标明产出去向", dest)
            # 最关键的一条:对话区本身要换成迭代语境,而不是继续显示「新建本体」的欢迎页
            (ok if "继续构建" in body and clicked in body else
             bad)("对话区换为底本上下文", body[:60].replace("\n", " "))
            _merge_copy_ok = "并入" in body and "不被覆盖" in body and "删除既有" in body
            (ok if _merge_copy_ok else bad)(
                "说明合并语义(并入/不覆盖/不删除既有)",
                "" if _merge_copy_ok else "缺合并语义说明")
            (ok if nm_dis else bad)("迭代时名称框禁用", f"disabled={nm_dis}")
            (ok if "本轮" in ph else bad)("诉求框提示改为增量口径", ph[:40])
            await pg.evaluate("bcIterClear()")
            await pg.wait_for_timeout(500)
            after = (await pg.inner_text("#bc_send")).strip()
            (ok if "继续构建" not in after and not await pg.evaluate("BC.base") else
             bad)("退出迭代模式可复原", after)

        # ══ 步骤 8:规则页(决策层)—— 只读查看 ══
        print("\n【步骤8】其余关键页可用性")
        for p, _key in (("rules", "构成规则"), ("review", "评审"), ("actioncenter", "动作"),
                       ("ontquality", "完备"), ("qaeval", "评测")):
            b0 = len(errs)
            await goto(p, 1800)
            t = await pg.inner_text(f"#p_{p}")
            e = [x for x in errs[b0:] if "favicon" not in x]
            (ok if len(t) > 30 and not e else bad)(f"{p} 页可用", f"{len(t)}字 err={e[:1]}")

        if await cur_rt() != initial_rt:
            restored = await pg.request.post(B + '/api/engine/config', data={"driver": initial_rt})
            (ok if restored.ok and await cur_rt() == initial_rt else bad)("恢复测试前运行时", initial_rt)
        else:
            ok("测试前运行时保持不变", initial_rt)
        await close_browser(br)
    print(f"\n===== UI 实操:{len(R['p'])} 通过 / {len(R['f'])} 失败"
          + (f" / {len(R['s'])} 跳过" if R["s"] else "") + " =====")
    for f in R["f"]: print("  ✗", f)
    for s_ in R["s"]: print("  ⊘", s_)   # 跳过项照列,避免「环境未配」把覆盖面悄悄缩水
    sys.exit(1 if R["f"] else 0)

asyncio.run(main())
