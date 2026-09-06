#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 Apache Ossie 官方 schema 校验本仓导出(DR-055)。

用法:python3 scripts/validate_ossie_export.py <graph.json> [--kind semantic_model|ontology] [--out file.yaml]
需要 jsonschema(可选 pyyaml 做 YAML 读回自检);缺失时明确报错,不假装通过。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import osi_export  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ir")
    ap.add_argument("--kind", choices=("semantic_model", "ontology"), default="semantic_model")
    ap.add_argument("--out")
    a = ap.parse_args()
    ir = json.load(open(a.ir, encoding="utf-8"))
    doc = osi_export.ontology(ir) if a.kind == "ontology" else osi_export.semantic_model(ir)
    text = osi_export.emit(doc) + "\n"
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
    errors = osi_export.validate(doc, a.kind)
    if errors is None:
        print("未校验:缺少 jsonschema(pip install jsonschema)"); return 2
    try:
        import yaml
        back = yaml.safe_load(text)
        if back != doc:
            print("YAML 读回与文档不一致(发射器缺陷)"); return 1
    except ImportError:
        print("提示:未安装 pyyaml,跳过 YAML 读回自检")
    if errors:
        print(f"校验失败({len(errors)}):"); [print("  -", e[:200]) for e in errors[:30]]; return 1
    print(f"校验通过:{a.kind} · schema {osi_export.SPEC_VERSION}"); return 0


if __name__ == "__main__":
    raise SystemExit(main())
