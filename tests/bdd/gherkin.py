# -*- coding: utf-8 -*-
"""最小中文 Gherkin 执行器(BDD,DR-066)。

场景文件在 specs/features/*.feature,用中文关键字:
  功能 / 场景 / 假如 / 当 / 那么 / 而且 / 并且 / 但是;标签以 @ 开头(如 @REQ-EVI-01)。
步骤后可跟文档串(三个双引号包围的多行文本)或数据表(| a | b |)。

不引入 pytest-bdd:只需解析与按正则分派,几十行就够,且不改变「按 requirements 装完就能全绿」的承诺。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

KEYWORDS = ("假如", "当", "那么", "而且", "并且", "但是")
_STEPS: list = []


@dataclass
class Step:
    keyword: str
    text: str
    doc: str | None = None
    table: list | None = None


@dataclass
class Scenario:
    feature: str
    name: str
    tags: list
    steps: list = field(default_factory=list)
    path: str = ""
    line: int = 0


def step(pattern):
    """注册步骤:正则整句匹配(不含关键字),命名分组作为关键字参数传入。"""
    rx = re.compile(r"^" + pattern + r"$")

    def deco(fn):
        _STEPS.append((rx, fn))
        return fn
    return deco


def parse(path):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    feature, tags, out, cur, in_desc = "", [], [], None, False
    i = 0
    while i < len(lines):
        raw = lines[i]
        s = raw.strip()
        i += 1
        if not s or s.startswith("#"):
            continue
        if s.startswith("@"):
            tags += s.split()
            continue
        if s.startswith("功能:") or s.startswith("功能:"):
            feature = s.split(":", 1)[-1].split(":", 1)[-1].strip()
            tags, cur, in_desc = [], None, True
            continue
        if s.startswith("场景:") or s.startswith("场景:"):
            in_desc = False
            cur = Scenario(feature, s.split(":", 1)[-1].split(":", 1)[-1].strip(), tags, path=str(path), line=i)
            out.append(cur)
            tags = []
            continue
        if s.startswith('"""') and cur and cur.steps:
            buf = []
            while i < len(lines) and not lines[i].strip().startswith('"""'):
                buf.append(lines[i])
                i += 1
            i += 1
            indent = min((len(x) - len(x.lstrip()) for x in buf if x.strip()), default=0)
            cur.steps[-1].doc = "\n".join(x[indent:] for x in buf)
            continue
        if s.startswith("|") and cur and cur.steps:
            row = [c.strip() for c in s.strip("|").split("|")]
            cur.steps[-1].table = (cur.steps[-1].table or []) + [row]
            continue
        kw = next((k for k in KEYWORDS if s.startswith(k + " ") or s.startswith(k + " ")), None)
        if kw and cur is not None:
            cur.steps.append(Step(kw, s[len(kw):].strip()))
            continue
        if in_desc:                      # 功能标题之后、第一个场景之前是说明文字
            continue
        raise ValueError(f"{path}:{i} 无法解析:{s}")
    return out


def run(scenario, world):
    for st in scenario.steps:
        for rx, fn in _STEPS:
            m = rx.match(st.text)
            if m:
                kwargs = m.groupdict()
                if st.doc is not None:
                    kwargs["doc"] = st.doc
                if st.table is not None:
                    kwargs["table"] = st.table
                fn(world, **kwargs)
                break
        else:
            raise AssertionError(f"未定义的步骤({scenario.path}:{scenario.line}):{st.keyword} {st.text}\n"
                                 f"在 tests/bdd/steps.py 里用 @step(r'...') 补上")
