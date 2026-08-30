# -*- coding: utf-8 -*-
"""文档-代码一致性自检(DR-045)。

审计发现:他们为「本体」造了 drift_check/compat_check,自己的 ARCHITECTURE 路由/断言
计数却曾漂移(文档称 119/531,实为 122/535)。本测试把真实计数与规范值对齐——
代码一变即红,逼你同步更新文档与此常量。这是「文档漂移」的确定性校验。
"""
import re
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]

# 规范值(单一事实源)。改动路由/断言数时,必须同步改这里 + ARCHITECTURE.md/specs/map.md。
EXPECT_ROUTES = 125
EXPECT_ASSERTIONS = 553


def _server_route_count():
    """路由总数 = server.py 的 @app.* + 各 blueprint(bp_*.py)的 @bp_*.*。
    IR-011 起路由按簇迁往 blueprint,总数不变、位置改变——故按全体来源计数。"""
    total = 0
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    total += len(re.findall(r"^@app\.(?:route|get|post)", src, re.M))
    for bp in sorted(ROOT.glob("bp_*.py")):
        total += len(re.findall(r"^@bp_\w+\.(?:route|get|post)",
                                bp.read_text(encoding="utf-8"), re.M))
    return total


def _assertion_count():
    """断言调用点 = chk( 与 chk_engine( 之和。

    chk_engine 是「需上游引擎、缺失时条件跳过」的断言,同样是覆盖面的一部分;
    只数 chk( 会让这类断言在计数上凭空消失,把「改成条件跳过」误报成「删了断言」。
    """
    src = (ROOT / "test_all.py").read_text(encoding="utf-8")
    return src.count("chk(") + src.count("chk_engine(")


def test_route_count_matches_canonical():
    actual = _server_route_count()
    assert actual == EXPECT_ROUTES, (
        f"server.py 路由数 {actual} ≠ 规范 {EXPECT_ROUTES}。"
        f"新增/删除路由时同步更新 EXPECT_ROUTES 与 ARCHITECTURE.md/specs/map.md")


def test_assertion_count_matches_canonical():
    actual = _assertion_count()
    assert actual == EXPECT_ASSERTIONS, (
        f"test_all.py 断言数 {actual} ≠ 规范 {EXPECT_ASSERTIONS}。"
        f"增删断言时同步更新 EXPECT_ASSERTIONS 与文档计数")


def _stale_counts(doc, *, skip_route_re=None):
    """在文档里找与规范值不符的路由数/集成断言数。
    只校验「集成断言」口径:UI 套件断言数(test_ui/test_ui_ops)与单测数各有其值,
    通过排除模式跳过,避免误报。"""
    stale = []
    for m in re.finditer(r"(\d{2,4})\s*(?:条)?\s*(?:后端)?路由", doc):
        seg = doc[max(0, m.start() - 40):m.start()]
        if skip_route_re and re.search(skip_route_re, seg):
            continue                      # 分项计数(如 server 116 + bp 5)不参与总数校验
        if int(m.group(1)) != EXPECT_ROUTES:
            stale.append(f"路由数 {m.group(1)}(应 {EXPECT_ROUTES})")
    for m in re.finditer(r"(\d{2,4})\s*(?:条|个)?\s*(?:集成)?断言", doc):
        seg = doc[max(0, m.start() - 30):m.start()]
        if re.search(r"test_ui|走查|实操|单测|playwright", seg):
            continue                      # UI/单测层断言数不与集成断言数比对
        if int(m.group(1)) != EXPECT_ASSERTIONS:
            stale.append(f"断言数 {m.group(1)}(应 {EXPECT_ASSERTIONS})")
    return stale


def test_architecture_doc_counts_not_stale():
    """ARCHITECTURE.md 若写了具体路由/断言数,必须与规范值一致(抓已知的 119/531 漂移)。"""
    doc = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    # 架构图里按文件分项列出(server 116 + bp_engine 5),这些不是总数
    stale = _stale_counts(doc, skip_route_re=r"server\.py|bp_\w+\.py|\+")
    assert not stale, "ARCHITECTURE.md 计数已漂移: " + "; ".join(stale)


def test_readme_counts_not_stale():
    """README 是用户第一入口,其断言数同样必须与规范值一致
    (曾停在 531 而实际 535——此前 meta 测试只盯 ARCHITECTURE,漏了 README)。"""
    doc = (ROOT / "README.md").read_text(encoding="utf-8")
    stale = _stale_counts(doc, skip_route_re=r"server\.py|bp_\w+\.py|\+")
    assert not stale, "README.md 计数已漂移: " + "; ".join(stale)
