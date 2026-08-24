# -*- coding: utf-8 -*-
"""深度问数链路显式编排文档(docs/pipelines/deep_qa.yaml)与源码的一致性自检。

该 YAML 不参与运行,是给评审对照代码看的。文档最容易在重构后悄悄失真——
本测试把每个 `code:` 锚点回打到源码:文件必须存在、符号必须还在。
改了函数名而忘了改文档,这里当场变红。

刻意不引入 yaml 解析器:pyyaml 未在 requirements 中声明,
测试依赖它会让「按 requirements 装完就能全绿」这条承诺失效。
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "pipelines" / "deep_qa.yaml"

# 锚点里出现的自由文字(中文说明、注释语)不参与符号校验
_SYMBOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def _anchors():
    text = DOC.read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r'^\s*code:\s*"?([^"\n]+)"?\s*$', text, re.M):
        out.append(m.group(1).strip())
    return out


def test_doc_exists_and_has_anchors():
    assert DOC.exists(), "链路说明文档缺失"
    assert len(_anchors()) >= 10, "锚点过少,文档可能被截断"


def test_every_code_anchor_resolves():
    """`file.py:symbol` 形式的锚点:文件须存在,符号须仍在该文件里。"""
    missing = []
    for a in _anchors():
        if ":" not in a:
            continue
        fname, _, rest = a.partition(":")
        fname = fname.strip()
        if not fname.endswith(".py"):
            continue
        f = ROOT / fname
        if not f.exists():
            missing.append(f"文件不存在: {a}")
            continue
        src = f.read_text(encoding="utf-8", errors="replace")
        for sym in re.split(r"[\s/+()]+", rest):
            sym = sym.strip().strip(",'\"")
            if not sym or not _SYMBOL.match(sym) or len(sym) < 4:
                continue
            base = sym.split(".")[-1]
            if base not in src:
                missing.append(f"符号已不在 {fname}: {sym}(锚点 {a})")
    assert not missing, "链路文档与源码漂移:\n  " + "\n  ".join(missing)


def test_gates_documented():
    """三道执行前关卡必须都在文档里——这是评审最关心的部分,不允许漏写。"""
    text = DOC.read_text(encoding="utf-8")
    for kw in ("只读", "口径拦截", "双盲意图检测"):
        assert kw in text, f"关卡「{kw}」未在链路文档中说明"
