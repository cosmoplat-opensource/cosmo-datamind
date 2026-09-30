#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MDD:由模型生成文档图(DR-066)。

模型是一等工件,图只是它的投影:
- docs/pipelines/ontology_build.yaml  → 构建流水线流程图
- specs/models/relation_lifecycle.json → 关系状态机
- specs/models/context_map.json        → 限界上下文地图

用法: python3 scripts/gen_models.py [--check]
--check 只比对不写入;tests/meta/test_models.py 用它保证 docs/models/MODELS.md 与模型同步。
只用标准库(YAML 用正则读取,不引入解析器依赖)。
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "models", "MODELS.md")


def pipeline_model():
    text = open(os.path.join(ROOT, "docs", "pipelines", "ontology_build.yaml"), encoding="utf-8").read()
    nodes = []
    for block in re.split(r"\n(?=  - id: )", text.split("\nnodes:", 1)[1].split("\nedges:", 1)[0]):
        m = re.search(r"- id: (\w+)", block)
        if not m:
            continue
        typ = re.search(r"type: (\w+)", block)
        steps = re.search(r"sse_steps: \[([^\]]*)\]", block)
        nodes.append({"id": m.group(1), "type": typ.group(1) if typ else "",
                      "sse_steps": [x.strip() for x in steps.group(1).split(",") if x.strip()] if steps else []})
    edges = re.findall(r"^  - \[(\w+), (\w+)\]", text.split("\nedges:", 1)[1], re.M)
    return nodes, edges


def _pipeline_mermaid():
    nodes, edges = pipeline_model()
    shape = {"llm": ("[/", "/]"), "llm_observer": ("[/", "/]"), "human": ("([", "])"), "output": ("[(", ")]")}
    lines = ["flowchart TD"]
    for n in nodes:
        a, b = shape.get(n["type"], ("[", "]"))
        steps = ("<br/><small>" + " · ".join(n["sse_steps"]) + "</small>") if n["sse_steps"] else ""
        lines.append(f'    {n["id"]}{a}"{n["id"]}{steps}"{b}')
    lines += [f"    {x} --> {y}" for x, y in edges]
    return "\n".join(lines)


def _lifecycle_mermaid():
    model = json.load(open(os.path.join(ROOT, "specs", "models", "relation_lifecycle.json"), encoding="utf-8"))
    lines = ["stateDiagram-v2", f"    [*] --> {model['initial']}"]
    for t in model["transitions"]:
        lines.append(f"    {t['from']} --> {t['to']}: {t['actor']}")
    return "\n".join(lines)


def _context_mermaid():
    model = json.load(open(os.path.join(ROOT, "specs", "models", "context_map.json"), encoding="utf-8"))
    lines = ["flowchart LR"]
    for cid, c in model["contexts"].items():
        lines.append(f'    {cid}["{c["name"]}<br/><small>{len(c["modules"])} 个模块</small>"]')
    for cid, c in model["contexts"].items():
        for dep in c["depends_on"]:
            if dep != "*":
                lines.append(f"    {cid} --> {dep}")
    return "\n".join(lines)


def render():
    ctx = json.load(open(os.path.join(ROOT, "specs", "models", "context_map.json"), encoding="utf-8"))
    rows = "\n".join(f"| {c['name']} | `{cid}` | {'、'.join(c['modules'])} | {'、'.join(c['depends_on']) or '—'} |"
                     for cid, c in ctx["contexts"].items())
    return f"""# 模型投影(自动生成,勿手改)

> 由 `python3 scripts/gen_models.py` 从模型生成。改模型后重新生成;`tests/meta/test_models.py` 会检查本文件是否同步。

## 构建流水线(docs/pipelines/ontology_build.yaml)

每个节点下的小字是它对外推送的步骤名(SSE),与构建代码一一对应。

```mermaid
{_pipeline_mermaid()}
```

## 关系状态机(specs/models/relation_lifecycle.json)

```mermaid
{_lifecycle_mermaid()}
```

## 限界上下文(specs/models/context_map.json)

```mermaid
{_context_mermaid()}
```

| 上下文 | 标识 | 模块 | 允许依赖 |
|---|---|---|---|
{rows}
"""


def main(argv):
    text = render()
    if "--check" in argv:
        current = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
        if current != text:
            print("docs/models/MODELS.md 与模型不同步,请运行 python3 scripts/gen_models.py", file=sys.stderr)
            return 1
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("written", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
