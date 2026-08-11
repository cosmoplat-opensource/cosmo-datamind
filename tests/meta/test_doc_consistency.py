# -*- coding: utf-8 -*-
"""文档-代码一致性自检(DR-045)。

审计发现:他们为「本体」造了 drift_check/compat_check,自己的 ARCHITECTURE 路由/断言
计数却已漂移(文档称 119/531,实为 121/535)。本测试把真实计数与规范值对齐——
代码一变即红,逼你同步更新文档与此常量。这是「文档漂移」的确定性校验。
"""
import re
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]

# 规范值(单一事实源)。改动路由/断言数时,必须同步改这里 + ARCHITECTURE.md/specs/map.md。
EXPECT_ROUTES = 121
EXPECT_ASSERTIONS = 535


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
    src = (ROOT / "test_all.py").read_text(encoding="utf-8")
    return src.count("chk(")


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


def test_architecture_doc_counts_not_stale():
    """ARCHITECTURE.md 若写了具体路由/断言数,必须与规范值一致(抓已知的 119/531 漂移)。"""
    doc = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    stale = []
    for m in re.finditer(r"(\d{2,4})\s*(?:条)?\s*(?:后端)?路由", doc):
        if int(m.group(1)) != EXPECT_ROUTES:
            stale.append(f"路由数 {m.group(1)}(应 {EXPECT_ROUTES})")
    for m in re.finditer(r"(\d{2,4})\s*(?:条|个)?\s*(?:运行时)?断言", doc):
        if int(m.group(1)) != EXPECT_ASSERTIONS:
            stale.append(f"断言数 {m.group(1)}(应 {EXPECT_ASSERTIONS})")
    assert not stale, "ARCHITECTURE.md 计数已漂移: " + "; ".join(stale)
