# -*- coding: utf-8 -*-
"""界面文案不得中英混用。

面向业务方的文字里夹英文术语,读者要么看不懂,要么得自己猜——CQ、verified、
Skill 都属此类。字段值(status="verified")与代码标识符照旧用英文,只约束
**渲染到页面的文案**。

约束的是新增,不是重写历史:白名单收录确属专有名词或业界通名的词。往白名单
里加词时请想清楚——业务方是否真的认得它。
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
UI = ROOT / "ui" / "index.html"

# 必须用中文表达的术语:它们都有公认且业务方看得懂的中文名
MUST_TRANSLATE = {
    "CQ": "验收问题",
    "verified": "已验证",
    "candidate": "待取证",
    "asserted": "人工断言",
    "inferred": "推理得出",
    "LLM": "大语言模型",
    "Skill": "技能",
    "Agent": "智能体",
}

# 专有名词与业界通名:译了反而不好找、不好查,保留原文
ALLOWED = {
    "BFO", "IOF", "IRI", "OWL", "RDF", "SHACL", "SPARQL", "JSON-LD", "TTL",
    "SQL", "SQLite", "MySQL", "Doris", "PostgreSQL", "PG", "DSN", "API", "HTTP",
    "CSV", "TSV", "PDF", "TXT", "Excel", "JSON", "XML", "MCP", "SSE", "ID",
    "Cosmo", "DataMind", "GLM", "DeepSeek", "Qwen", "OpenAI", "vLLM", "Hermes",
    "Jaccard", "ROC", "Enter", "Shift", "zip", "Key",
}

# 渲染文案的提取位置:文本节点、placeholder、title
_TEXT = re.compile(r">([^<>]{0,200}?)<")
_ATTR = re.compile(r'(?:placeholder|title)="([^"]{0,200})"')
# 模板变量 ${...} 里是代码,不是文案。内层可能带引号与嵌套花括号
# (如 ${x.candidate===false?'…':'…'}),故允许跨越一层嵌套,不在首个 } 处截断。
_TPL = re.compile(r"\$\{(?:[^{}]|\{[^{}]*\})*\}?")
_CJK = re.compile(r"[一-鿿]")


# 状态译名映射表:`{verified:'已验证', ...}` 正是把英文字段值译成中文的地方,
# 它本身必须出现英文键。整段剔除,否则会把「翻译动作」本身判成「未翻译」。
_MAPPING = re.compile(r"(?:const\s+\w+\s*=\s*)?\{[^{}]*?['\"]?\w+['\"]?\s*:\s*(?:\[[^\]]*\]|['\"][^'\"]*['\"])"
                      r"(?:\s*,\s*['\"]?\w+['\"]?\s*:\s*(?:\[[^\]]*\]|['\"][^'\"]*['\"]))+\s*\}")


def _rendered_texts():
    src = UI.read_text(encoding="utf-8")
    out = []
    for pat in (_TEXT, _ATTR):
        for m in pat.finditer(src):
            t = _TPL.sub(" ", m.group(1))          # 去掉模板变量再判
            t = _MAPPING.sub(" ", t)               # 去掉状态译名表
            if _CJK.search(t):                     # 只查中文语境下的夹带
                out.append(t)
    return out


@pytest.mark.parametrize("term,zh", sorted(MUST_TRANSLATE.items()))
def test_ui_uses_chinese_instead_of_jargon(term, zh):
    """中文文案里不得出现该英文术语——它有通行中文名。"""
    word = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])")
    bad = [t.strip()[:90] for t in _rendered_texts() if word.search(t)]
    assert not bad, (
        f"界面文案里仍有「{term}」,应改用「{zh}」:\n  " + "\n  ".join(bad[:5]))


def test_allowlist_terms_are_actually_used_somewhere():
    """白名单不该无限膨胀:全都用不到的词及时删,免得成为放行任何英文的借口。"""
    src = UI.read_text(encoding="utf-8")
    unused = sorted(w for w in ALLOWED if w not in src)
    assert len(unused) <= len(ALLOWED) // 2, (
        "白名单中过半词条在界面里根本不出现,应清理:" + "、".join(unused))


def test_quality_result_has_chinese_names():
    """验收结果 pass/review/fail 是字段值,展示必须走中文映射。"""
    import build_quality
    assert build_quality.result_text("pass") == "通过"
    assert build_quality.result_text("review") == "待复核"
    assert build_quality.result_text("fail") == "不通过"
    src = UI.read_text(encoding="utf-8")
    assert "qrText(" in src, "界面应经 qrText 统一转换验收结果,而非直出字段值"
