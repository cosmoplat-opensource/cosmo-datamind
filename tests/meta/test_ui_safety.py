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
