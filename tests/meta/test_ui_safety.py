# -*- coding: utf-8 -*-
"""界面源码的静态检查:转义、失败态、样式变量、遗留的开发备注。

这些问题在浏览器用例里不容易稳定复现(要构造带引号的表名、让接口返回错误),
所以直接约束源码写法。
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
INDEX = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
MODULES = {p.name: p.read_text(encoding="utf-8") for p in (ROOT / "ui" / "modules").glob("*.js")}
ALL = {"index.html": INDEX, **MODULES}


def test_inline_handlers_do_not_put_html_escaped_text_inside_js_strings():
    """esc() 产生的 &#39; 在属性里先被还原再交给 JS,引号仍能截断字符串;内联处理器的参数一律用 jsAttr。"""
    bad = {name: re.findall(r"\w+\('\$\{esc\([^)]*\)\}'", src) for name, src in ALL.items()}
    assert not {k: v for k, v in bad.items() if v}


def test_server_strings_are_escaped_before_reaching_innerhtml():
    assert "<h3>${esc(n)} · 字段</h3>" in MODULES["catalog.js"]
    assert "(rd.sources||[]).map(esc).join('、')" in INDEX
    assert "esc(SB[rd.status]||rd.status)" in INDEX
    assert ".map(([v,n])=>esc(v)+'×'+n)" in INDEX and ".map(([k,n])=>esc(KNM(k))+':'+n)" in INDEX


def test_failed_requests_are_not_rendered_as_empty_success():
    assert re.search(r"async function qualityLoad\(\)\{const d=await J\('/api/quality'\);\s*if\(d\.error\)", INDEX)
    assert re.search(r"async function glossaryLoad\(\)\{const d=await J\('/api/glossary'\);\s*if\(d\.error\)", INDEX)
    upload = INDEX[INDEX.index("async function dqUpload"):][:1200]
    assert "if(d.error)" in upload and "catch(e)" not in upload
    assert "const JL=" in INDEX                      # 列表接口失败时给出提示并按空列表处理
    for call in ("J('/api/graphs')", "J('/api/tables')"):
        assert call not in INDEX and call not in MODULES["catalog.js"]


def test_unknown_confidence_does_not_break_the_diagnosis_card():
    assert "CB[c.confidence][0]" not in INDEX


def test_style_variables_and_grids_match_their_use():
    root = re.search(r":root\{[^}]*\}", INDEX).group(0)
    for var in set(re.findall(r"var\((--[\w-]+)\)", INDEX)):
        assert var + ":" in root, var
    kpis = INDEX[INDEX.index("启用类型") - 200:INDEX.index("执行失败</div>")]
    assert kpis.count('class="kpi"') == 5 and "repeat(5,1fr)" in kpis


def test_no_developer_notes_or_unfinished_features_in_page_copy():
    for text in ("v0726a", "对齐平台『", "agy(Google", "本地原型", "需后端注册", "dqSkillUpload"):
        assert text not in INDEX, text


def test_unknown_action_outcome_is_not_shown_as_success():
    toast = INDEX[INDEX.index("function acToast"):][:600]
    assert "d.status==='unknown'" in toast and "结果未知" in toast


# ── 可访问性(DR-132):弹窗、键盘操作、请求期间的按钮、最小字号 ─────────────────────────────

UI_FILES = {**ALL, **{p.name: p.read_text(encoding="utf-8") for p in (ROOT / "ui" / "styles").glob("*.css")}}


def test_modals_are_dialogs_that_close_on_escape_and_keep_focus_inside():
    block = INDEX[INDEX.index("function modalA11y"):][:2600]
    for needle in ("'role','dialog'", "'aria-modal','true'", "e.key==='Escape'", "e.key==='Tab'", "last.focus()"):
        assert needle in block, needle
    assert "modalA11y();" in INDEX


def test_clickable_rows_and_icons_are_reachable_by_keyboard():
    sel = re.search(r"const A11Y_SEL=`([^`]+)`", INDEX).group(1)
    for needle in (".pill:not(#rt_pill)", ".dq-modal .mt", ".dq-modal-hd .x", 'span[title="关闭"]', ".dq-att .x", ".vz-card .x",
                   ".cn-row .x", "tr[onclick]", "#cat_tags .tag"):
        assert needle in sel, needle
    assert "a11yLater" in INDEX                       # 动态渲染的内容也要补上


def test_the_clicked_button_is_disabled_while_its_request_is_in_flight():
    helper = INDEX[INDEX.index("const J=async"):][:1500]
    assert "aria-busy" in helper and "disabled=true" in helper and "finally" in helper


def test_no_text_is_smaller_than_12_pixels():
    small = {name: sorted(set(re.findall(r"font-size:\s*(?:[0-9]|1[01])(?:\.\d+)?px", src))) for name, src in UI_FILES.items()}
    assert not {k: v for k, v in small.items() if v}
    svg = {name: sorted(set(re.findall(r'font-size="(?:[0-9]|1[01])(?:\.\d+)?"', src))) for name, src in UI_FILES.items()}
    assert not {k: v for k, v in svg.items() if v}


def test_file_upload_inputs_and_controls_have_names_and_edges_have_a_legend():
    """DR-133:附件上传用键盘能打开;没有 label 的输入框补上可读名称;关系状态的线型有图例。"""
    assert 'id="dq_file"' in INDEX and 'id="dq_file" multiple accept=".csv,.tsv,.txt,.xlsx,.xls" style="display:none"' not in INDEX
    assert 'class="vh"' in INDEX and ".vh{" in INDEX and "label:focus-within" in INDEX
    scan = INDEX[INDEX.index("function a11yScan"):][:1400]
    assert "input,select,textarea" in scan and "placeholder" in scan and "aria-label" in scan
    legend = INDEX[INDEX.index("$('#g_legend').innerHTML"):][:900]
    assert "EST[" in legend and "<svg" in legend
    assert INDEX.count('<span class="dot ${dot}" aria-hidden="true"></span>') == 2


def test_attachment_chips_replay_polling_and_feedback_wording():
    """附件的行数只在是数字时加「行」;批量回放的轮询失败时显示错误而不是 undefined;反馈去向用评审页上的名称。"""
    chips = INDEX[INDEX.index("function dqRenderAtts"):][:400]
    assert "typeof a.rows==='number'" in chips and "按期间展开的长表,'" not in INDEX
    poll = INDEX[INDEX.index("async function rvFbRegression"):][:900]
    assert "j.error||!j.result" in poll
    assert "本体迭代候选队列" not in INDEX and "问数反馈待处理列表" in INDEX


def test_paired_replay_controls_are_present():
    """DR-140:单条「修改前后对比」只在记录了版本且保存了答案时出现;批量配对回放显示差值、区间与未配对原因。"""
    assert "rvFbReplay(${jsAttr(x.id)},this,true)" in INDEX and "x.versions&&x.versions.length" in INDEX
    assert "rvFbRegression(this,true)" in INDEX and "p.verdict" in INDEX and "未配对" in INDEX


# ── PR #1 审查修复:机器人与本体生命周期界面 ─────────────────────────────────────────────────────

def test_request_ids_do_not_require_a_secure_context():
    """crypto.randomUUID 只在 HTTPS 或 localhost 下存在;经内网 IP 用 HTTP 打开时,写操作不能因此报错。"""
    direct = {n: s.count("crypto.randomUUID(") for n, s in MODULES.items() if n != "request-id.js" and "crypto.randomUUID(" in s}
    assert not direct, direct
    helper = MODULES["request-id.js"]
    assert "getRandomValues" in helper and "randomUUID" in helper
    i = INDEX.index("modules/request-id.js")
    assert all(i < INDEX.index("modules/" + m) for m in ("robotics-application.js", "ontology-lifecycle.js", "robotics-task.js",
                                                         "robotics-design.js", "robotics-build.js", "robotics-review.js"))


def test_request_id_helper_falls_back_without_random_uuid():
    import shutil
    import subprocess
    if not shutil.which("node"):
        import pytest
        pytest.skip("没有 node")
    js = (ROOT / "ui" / "modules" / "request-id.js").read_text(encoding="utf-8")
    probe = js + "\nconst ids=new Set();for(let i=0;i<50;i++)ids.add(globalThis.dmRequestId());" \
                 "console.log(ids.size, [...ids].every(x=>/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(x)))"
    env_js = "globalThis.window=globalThis;const c=globalThis.crypto;" \
             "Object.defineProperty(globalThis,'crypto',{value:{getRandomValues:a=>c.getRandomValues(a)},configurable:true});"
    out = subprocess.run(["node", "-e", env_js + probe], capture_output=True, text=True, timeout=30)
    assert out.stdout.split() == ["50", "true"], out.stderr


def test_ontology_library_reports_failures_and_deletes_through_one_confirmation():
    lib = INDEX[INDEX.index("async function library()"):][:900]
    assert "d.error" in lib and "${esc(o.objects" in lib and "${esc(o.links" in lib
    assert "if(confirm('删除 '" not in lib                     # 生命周期对话框已有确认,不再先弹原生确认框
    dele = INDEX[INDEX.index("async function libDel("):][:700]
    assert "try{" in dele and "catch(" in dele and "confirm(" in dele   # 迁移前的旧路径仍要确认一次


def test_lifecycle_dialog_is_keyboard_safe_and_closes_on_navigation():
    life = MODULES["ontology-lifecycle.js"]
    assert "'Escape'" in life and "'Tab'" in life and "close" in life
    close = INDEX[INDEX.index("function closeDialogs()"):][:400]
    assert "OntologyLifecycle" in close


def test_chat_tab_uses_the_menu_name():
    assert "智能问数" not in INDEX


def test_robotics_graph_kinds_are_not_told_apart_by_colour_alone():
    views = MODULES["robotics-views.js"]
    assert not any(w in views for w in ("蓝色", "紫色", "青色"))
    assert "lineDash" in views and "fontSize:10" not in views


def test_every_browser_test_finds_the_local_browser_the_same_way():
    """浏览器测试统一经 tests/browser_runtime.browser_executable() 找本机浏览器(含 macOS 的 Chrome),
    各自维护一份候选清单时,新加的测试漏了 macOS 路径,只能在装了 Playwright 自带 Chromium 的机器上跑。"""
    import tests.browser_runtime as rt
    assert "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" in rt.BROWSER_CANDIDATES
    files = [*(ROOT / "tests").rglob("*.py"), ROOT / "test_ui.py", ROOT / "test_ui_ops.py"]
    own_lists = [str(p.relative_to(ROOT)) for p in files
                 if p.name not in ("browser_runtime.py", "test_ui_safety.py") and "chromium.launch" in p.read_text(encoding="utf-8")
                 and "/usr/bin/google-chrome" in p.read_text(encoding="utf-8")]
    assert not own_lists, own_lists


def test_recycle_bin_opens_fixed_versions_through_the_deleted_history_view():
    """已删除本体的版本详情只在 ?state=deleted 的历史视图里返回;回收站里的「查看固定版本」要带上这个状态。"""
    assets = MODULES["robotics-assets.js"]
    link = assets[assets.index("const versionLink"):][:300]
    assert "state=deleted" in link
    deleted_card = assets[assets.index("item.lifecycle?.state==='deleted'&&item.version_id"):][:200]
    assert "versionLink(item.ontology_id,item.version_id,true)" in deleted_card
    opener = assets[assets.index("async openVersion("):][:400]
    assert "state=deleted" in opener


def test_startup_does_not_route_again_after_a_hash_navigation():
    """首屏的异步初始化完成时,如果用户(或页面跳转)已经通过 hash 进入了某页,不能再路由一次:
    那会重新挂载当前页面,清掉刚选中的节点和查找结果(机器人图谱的查找在 macOS 上因此失败)。"""
    init = INDEX[INDEX.index("(async()=>{if(await appSourceInit())"):][:260]
    assert "ROUTED_BY_HASH" in init
    listener = INDEX[INDEX.index("addEventListener('hashchange'"):][:200]
    assert "ROUTED_BY_HASH=true" in listener
