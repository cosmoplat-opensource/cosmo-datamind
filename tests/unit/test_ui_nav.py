# -*- coding: utf-8 -*-
"""侧边栏信息架构与字号层级的静态校验(DR-047)。

来源:用户在 20260814 的界面标注提出两条意见——
  ①「菜单的样式有点乱」(箭头指向分组标题「智能问数」与菜单项「动作中心」);
  ②「目录比菜单的字体还小,不清晰」。

这些是**结构性**问题,可直接对 `ui/index.html` 做静态断言,不必起浏览器:
分组归属、字号层级都写在源码里,起服务只会让回归变慢。
"""
import re
import pathlib

UI_DIR = pathlib.Path(__file__).resolve().parents[2] / "ui"
UI = (UI_DIR / "index.html").read_text(encoding="utf-8")
CATALOG = (UI_DIR / "modules" / "catalog.js").read_text(encoding="utf-8")
RESPONSIVE = (UI_DIR / "styles" / "responsive.css").read_text(encoding="utf-8")


def _css_px(selector_re, prop="font-size"):
    """从内联 <style> 里取某规则的像素值(取首个匹配)。"""
    m = re.search(selector_re + r"\{[^}]*\}", UI)
    if not m:
        return None
    m2 = re.search(prop + r":\s*([0-9.]+)px", m.group(0))
    return float(m2.group(1)) if m2 else None


def _groups():
    """解析侧边栏:返回 [(组名, [data-p, ...]), ...] 与顶层项。"""
    aside = UI[UI.index('<aside>'):UI.index('</aside>')]
    out = []
    rest = aside
    for gm in re.finditer(r'<div class="nav-group">(.*?)</div></div>', aside, re.S):
        blk = gm.group(1)
        name = re.search(r'class="gh"[^>]*>([^<]+)', blk)
        items = re.findall(r'class="nav"\s+data-p="([a-z_]+)"', blk)
        out.append(((name.group(1).strip() if name else "?"), items))
        rest = rest.replace(gm.group(0), "")     # 摘掉分组块,余下才是顶层项
    top = re.findall(r'class="nav(?: on)?" data-p="([a-z_]+)"', rest)
    return out, top


class TestFontHierarchy:
    def test_content_table_not_smaller_than_nav(self):
        """内容区表格字号不得小于导航字号 —— 否则主内容比侧栏还难读(用户意见②)。"""
        nav = _css_px(r"\.nav")
        table = _css_px(r"table")
        assert nav and table, (nav, table)
        assert table >= nav - 0.5, f"表格 {table}px 明显小于菜单 {nav}px,内容与导航的视觉层级倒挂"

    def test_group_header_and_arrow_readable(self):
        """分组标题与折叠箭头要看得见:箭头过小会让人不知道分组可折叠(用户意见①)。"""
        gh = _css_px(r"\.nav-group \.gh")
        ar = _css_px(r"\.nav-group \.gh \.ar")
        assert gh and gh >= 12, f"分组标题 {gh}px 过小"
        assert ar and ar >= 11, f"折叠箭头 {ar}px 过小,几乎不可见"

    def test_group_separated_from_previous_items(self):
        """分组之间要有明确间距,否则上一组的项与下一组标题黏在一起,层级读不出。"""
        m = re.search(r"\.nav-group\{[^}]*\}", UI)
        assert m, "缺 .nav-group 规则"
        mt = re.search(r"margin-top:\s*([0-9.]+)px", m.group(0))
        assert mt and float(mt.group(1)) >= 8, f"分组上间距 {mt.group(1) if mt else '0'}px 太小"


class TestTableDensity:
    def test_cell_padding_raises_row_height_not_column_width(self):
        """加大行高提升可读性,但**水平** padding 不可跟着加——
        侧栏表格容器只有 ~320px,每多 2px 水平内边距就 ×列数 地把末列挤出可视区
        (实测:8px→10px 使表宽 332 > 容器 320,「列」需横向滚动才看得到)。"""
        m = re.search(r"th,td\{([^}]*)\}", UI)
        assert m, "缺 th,td 规则"
        pad = re.search(r"padding:\s*([0-9.]+)px\s+([0-9.]+)px", m.group(1))
        assert pad, "th,td 未声明 padding"
        v, h = float(pad.group(1)), float(pad.group(2))
        assert v >= 8, f"垂直内边距 {v}px 偏小,行高不够舒展"
        assert h <= 8, f"水平内边距 {h}px 过大,窄容器里会把末列挤出可视区"


class TestCatalogAndResponsiveBehavior:
    def test_catalog_search_includes_chinese_annotation(self):
        """输入框承诺可搜中文注释，过滤表达式必须真的使用接口的 cn 字段。"""
        fn = re.search(r"function catRender\(\).*?\nasync function tbl", CATALOG, re.S)
        assert fn and "t.cn" in fn.group(0), "目录只按物理表名搜索，中文注释占位文案与行为不一致"

    def test_mobile_grid_children_can_shrink(self):
        """宽表位于 grid 时，轨道和子项都要允许收缩，否则整页会被内容撑宽。"""
        media = re.search(r"@media \(max-width:820px\)\{(.*?)\n\}", RESPONSIVE, re.S)
        assert media, "缺移动端样式"
        css = media.group(1)
        assert "minmax(0,1fr)" in css and ".grid2>*{min-width:0}" in css
        assert ".kpis{grid-template-columns:repeat(2,minmax(0,1fr))!important}" in css

    def test_desktop_catalog_wide_table_cannot_expand_grid_track(self):
        assert ".grid2>*{min-width:0}" in UI
        catalog = UI[UI.index('id="p_catalog"'):UI.index('id="p_conn"')]
        assert "grid-template-columns:360px minmax(0,1fr)" in catalog

    def test_mobile_wide_tables_and_graph_toolbar_are_contained(self):
        media = re.search(r"@media \(max-width:820px\)\{(.*?)\n\}", RESPONSIVE, re.S)
        css = media.group(1) if media else ""
        assert "#eg_keys,#claw_audit{overflow-x:auto}" in css
        assert "#claw_edits{overflow-wrap:anywhere}" in css
        assert ".g-toolbar-group" in css and "flex-wrap:wrap" in css
        assert "<div class=\"scroll\"><table" in UI[UI.index("async function clawAudit"):]

    def test_frontend_domains_are_external_modules(self):
        assert len(UI.splitlines()) < 2600
        assert '/assets/modules/catalog.js' in UI
        assert '/assets/styles/responsive.css' in UI
        assert "function catRender" not in UI
        assert "@media (max-width:820px)" not in UI


class TestNavGrouping:
    def test_every_page_reachable_and_count_stable(self):
        """重排分组不得丢页:27 个页面模块必须仍可从侧栏抵达。"""
        groups, top = _groups()
        allp = top + [p for _, ps in groups for p in ps]
        assert len(allp) == 27, f"页面数 {len(allp)} ≠ 27"
        assert len(set(allp)) == len(allp), "存在重复的 data-p"

    def test_ontology_pages_in_one_group(self):
        """本体相关页面必须归在同一组 —— 此前分散在「数据建模」与「本体治理」两处。"""
        groups, _ = _groups()
        want = {"graph", "build", "claw", "review", "ontquality", "library", "sparql"}
        hit = [g for g, ps in groups if want & set(ps)]
        assert len(hit) == 1, f"本体页分散在 {hit} 多个分组"
        g, ps = next((g, ps) for g, ps in groups if want & set(ps))
        assert want <= set(ps), f"「{g}」组缺本体页: {want - set(ps)}"

    def test_action_center_not_under_ontology_group(self):
        """「动作中心」是业务动作执行,不属于本体分组(用户标注箭头所指)。"""
        groups, _ = _groups()
        for g, ps in groups:
            if "actioncenter" in ps:
                assert "graph" not in ps and "本体" not in g, f"动作中心仍挂在「{g}」下"
                return
        raise AssertionError("actioncenter 不在任何分组中")

    def test_glossary_in_governance(self):
        """术语管理是治理资产,不该埋在问数组里。"""
        groups, _ = _groups()
        g = next(g for g, ps in groups if "glossary" in ps)
        assert "治理" in g, f"术语管理在「{g}」组,应归数据治理"
