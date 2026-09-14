#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 回复的 JSON 抽取与本体提议形状校验(确定性,不调模型)。

背景:构建链路此前在多处各自 `re.search(r"\\{[\\s\\S]*\\}")` + `json.loads`——
从首个 `{` 贪婪匹配到末个 `}`。回复里若 JSON 前后还有带花括号的文字(模型常见:
先说明一句、再附代码示例、最后补注释),取到的范围就错了;尾逗号这类模型高频
小毛病则直接解析失败。而调用方对解析失败只有一条路:换下一个引擎或整体回退
数据驱动——一次可修复的解析失败被当成引擎故障,白白浪费一次几分钟的长推理。

本模块把「从回复文本里取一个 JSON 对象」与「本体提议的形状契约」收成单一事实源:
  - extract(text):字符串感知的花括号配平扫描(字符串字面量里的花括号与转义
    引号不参与配平),自每个 `{` 起逐个候选片段尝试:先原样解析,失败后做最小
    修复(尾逗号)再试;返回第一个能解析的顶层对象,仍不行返回 None——调用方
    如实换引擎/回退,与旧路径同语义。
  - validate_proposal:objects/relations 必须是 dict 列表,对象必须有非空 name,
    关系两端必须为非空字符串;越界元素剔除并计数,不让一个坏元素拖垮整份提议,
    也不让超大提议挤爆下游数据裁决。
"""
from __future__ import annotations

import json
import re

_MAX_TEXT = 4_000_000        # 防御性上限:正常提议远小于此;超长输入截断后再扫描
_MAX_CANDIDATES = 80         # 最多尝试的候选片段数,防病态文本(花括号海洋)拖垮解析

# 尾逗号是模型最常见的 JSON 瑕疵(生成列表时逐项追加所致);其余大改写不做——
# 修不出来的就交回调用方走引擎切换/数据驱动回退,不静默猜测用户数据。
_TRAILING_COMMA = re.compile(r",\s*([}\]])")
# json.loads 其实容忍 NaN/Infinity,此处收紧为 null:证据数值不允许非有限值进出。
_NONFINITE = re.compile(r"(?<![\w.])-?(?:NaN|Infinity)\b")

MAX_OBJECTS = 2000           # 单次提议对象数上限(下游裁决/图谱渲染的量级护栏)
MAX_RELATIONS = 5000         # 单次提议关系数上限


def _repair(fragment):
    return _NONFINITE.sub("null", _TRAILING_COMMA.sub(r"\1", fragment))


def _candidate_spans(text, limit=_MAX_CANDIDATES):
    """按出现顺序产出各配平花括号块的 (start, end)。

    字符串感知:双引号字面量内的 `{`/`}` 与转义引号不参与配平,否则
    `{"note":"结果见下表}"` 一类正文会把块边界切错。一个起点配平失败后
    从下一个 `{` 再试(含嵌套内层)——外层带杂质时内层可能才是有效载荷。
    """
    n, i, found = len(text), 0, 0
    while i < n and found < limit:
        if text[i] != "{":
            i += 1
            continue
        depth, j, in_str, esc, end = 0, i, False, False, -1
        while j < n:
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
            j += 1
        if end < 0:          # 自此处起再也无法配平,后面更不可能
            return
        yield i, end
        found += 1
        i += 1


def extract(text):
    """从模型回复中取第一个可解析的 JSON 顶层对象(dict);取不到返回 None。

    与旧路径同语义:None 表示「这份回复里没有可用 JSON」,由调用方决定
    换下一引擎或回退数据驱动——本模块不做任何重试,保持行为可预测。
    """
    if not isinstance(text, str):
        return None
    text = text[:_MAX_TEXT]
    for start, end in _candidate_spans(text):
        fragment = text[start:end]
        for candidate in (fragment, _repair(fragment)):
            try:
                obj = json.loads(candidate)
            except Exception:
                continue
            if isinstance(obj, dict):
                return obj
    return None


def empty_dropped():
    return {"objects": 0, "relations": 0}


def validate_proposal(data, max_objects=MAX_OBJECTS, max_relations=MAX_RELATIONS):
    """本体提议的形状契约:剔除坏元素并计数,返回 (干净提议, 剔除统计)。

    只查形状,不查引用——「关系两端必须是已提议对象」仍由数据裁决阶段按
    对象名单跳过,两道关各自留痕。统计里的计数供调用方向用户如实展示,
    不把剔除伪装成「模型没提」。
    """
    data = data if isinstance(data, dict) else {}
    dropped = empty_dropped()
    raw_objects = data.get("objects")
    raw_relations = data.get("relations")
    # 容器必须是列表:字符串/标量不逐字符迭代——那是类型错误,不是「4 个坏对象」。
    if not isinstance(raw_objects, (list, tuple)):
        raw_objects = []
    if not isinstance(raw_relations, (list, tuple)):
        raw_relations = []
    objects = []
    for item in raw_objects:
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].strip():
            if len(objects) < max_objects:
                objects.append(item)
            else:
                dropped["objects"] += 1
        else:
            dropped["objects"] += 1
    relations = []
    for item in raw_relations:
        if (isinstance(item, dict)
                and isinstance(item.get("source"), str) and item["source"].strip()
                and isinstance(item.get("target"), str) and item["target"].strip()):
            if len(relations) < max_relations:
                relations.append(item)
            else:
                dropped["relations"] += 1
        else:
            dropped["relations"] += 1
    out = dict(data)
    out["objects"], out["relations"] = objects, relations
    return out, dropped


def extract_proposal(text):
    """extract + 形状校验一步到位:返回 (提议或 None, 剔除统计)。

    有效对象为空时按 None 处理——与调用方「解析结果不含对象 → 尝试下一引擎」
    的既有语义一致,避免把空壳提议当成有效结果送进数据裁决。
    """
    data = extract(text)
    if data is None:
        return None, empty_dropped()
    proposal, dropped = validate_proposal(data)
    if not proposal.get("objects"):
        return None, dropped
    return proposal, dropped
