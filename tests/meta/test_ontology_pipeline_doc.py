# -*- coding: utf-8 -*-
"""半自动本体构建显式流水线的源码锚点与关键门自检。"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "pipelines" / "ontology_build.yaml"
_SYMBOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def _anchors():
    text = DOC.read_text(encoding="utf-8")
    return [m.group(1).strip() for m in re.finditer(r'^\s*code:\s*"?([^"\n]+)"?\s*$', text, re.M)]


def test_pipeline_has_full_build_and_review_chain():
    assert DOC.exists()
    assert len(_anchors()) >= 15
    text = DOC.read_text(encoding="utf-8")
    for phrase in ("LLM 只提议", "verified", "方向反证", "能力问题", "pass/review/fail", "asserted"):
        assert phrase in text


def test_every_pipeline_code_anchor_resolves():
    missing = []
    for anchor in _anchors():
        fname, sep, rest = anchor.partition(":")
        if not sep or not fname.endswith(".py"):
            continue
        path = ROOT / fname
        if not path.exists():
            missing.append(f"文件不存在:{anchor}"); continue
        source = path.read_text(encoding="utf-8", errors="replace")
        for symbol in re.split(r"[\s/+()]+", rest):
            symbol = symbol.strip().strip(",'\"")
            if _SYMBOL.fullmatch(symbol) and len(symbol) >= 4 and symbol.split(".")[-1] not in source:
                missing.append(f"符号不存在:{anchor} → {symbol}")
    assert not missing, "\n".join(missing)
