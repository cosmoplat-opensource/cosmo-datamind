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
