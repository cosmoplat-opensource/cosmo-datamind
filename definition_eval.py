#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定义质量评分 —— DR-040。

元数据覆盖统计(DR-010)测「有没有定义/反例/接地」——形式覆盖率;
本模块测「定义**好不好**」——属加种差形式是否成立、是否循环、有无反例、与参考集是否契合。
两者正交:前者答「填没填」,后者答「填得对不对」。

全确定性、离线:结构维度靠规则,参考式靠字符二元组重叠(不需嵌入模型/LLM)。
LLM-judge / 嵌入相似度可作为可选增强,在引擎/模型可用时叠加(留接口,不作离线必需)。
"""
import re

_MIN_DEF_LEN = 8          # 定义最短字数(过短视为缺失)
_MIN_CE_LEN = 4           # 反例最短字数
_COPULA = ("是", "为", "指")


def _has_genus_differentia(d):
    """属加种差判定,兼容两种中文形态 + 英文:
    - 名词短语式「[种差]的[属]」:末个「的」前有≥4字种差、后有≥2字属;
    - 系词式「X 是/为/指 [属加种差]」:句首≤4字内出现系词且其后≥6字实义;
    - 英文「... a/an X that/which ...」。"""
    d = (d or "").strip().rstrip("。.")
    if len(d) < _MIN_DEF_LEN:
        return False
    if "的" in d:
        i = d.rfind("的")
        if len(d[:i]) >= 4 and len(d[i + 1:]) >= 2:
            return True
    for m in _COPULA:
        j = d.find(m)
        if 0 <= j <= 4 and len(d) - j - 1 >= 6:
            return True
    if re.search(r"\b(a|an)\b.+\b(that|which|used to|is a)\b", d, re.I):
        return True
    return False


def _bigrams(s):
    s = re.sub(r"\s+", "", (s or ""))
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _overlap(a, b):
    """字符二元组 Jaccard 重叠(确定性,无模型)。"""
    A, B = _bigrams(a), _bigrams(b)
    if not A or not B:
        return 0.0
    return round(len(A & B) / len(A | B), 3)


def score_definition(term, definition, counter_example="", gold=None, judge=None):
    """给一条定义打分。返回 {score, dims, issues}。
    结构维度各 0/1;有 gold 时加参考重叠(0-1),按 0.7 结构 + 0.3 参考 融合。

    judge: 可选 callable(term, definition)->float(0-1),语义充分性打分(LLM-judge/嵌入)。
    传入时补 `dims['semantic']` 并以 0.6 结构参考 + 0.4 语义 再融合——
    这是覆盖「结构成立但空洞」局限的增强层;纯 callable,离线可用 mock、在线接真引擎。"""
    d = (definition or "").strip()
    ce = (counter_example or "").strip()
    t = (term or "").strip()
    dims, issues = {}, []

    dims["present"] = 1.0 if len(d) >= _MIN_DEF_LEN else 0.0
    if not dims["present"]:
        issues.append("定义缺失或过短")

    # 循环:定义仅重复术语(去掉术语与系词/标点后无实义)。
    # 定义不存在时,「非循环」不成立而非真空为真——否则空定义会凭此白拿分,
    # 让「完全没写」看起来像「写了一部分」。故无定义时结构维度一律判 0。
    stripped = d.replace(t, "").strip("是为指的。.，,、 ") if t else d
    circular = bool(d) and (d == t or (bool(t) and len(stripped) < 2))
    dims["non_circular"] = 1.0 if (dims["present"] and not circular) else 0.0
    if circular:
        issues.append("循环定义(仅重复术语,无实质种差)")

    dims["genus_differentia"] = 1.0 if (dims["present"] and _has_genus_differentia(d)) else 0.0
    if dims["present"] and not dims["genus_differentia"]:
        issues.append("非属加种差形式(缺属或缺种差)")

    dims["counter_example"] = 1.0 if len(ce) >= _MIN_CE_LEN else 0.0
    if not dims["counter_example"]:
        issues.append("缺反例")

    struct = [dims["present"], dims["non_circular"], dims["genus_differentia"], dims["counter_example"]]
    score = sum(struct) / len(struct)

    if gold:
        ref = _overlap(d, gold)
        dims["reference"] = ref
        score = 0.7 * score + 0.3 * ref

    if judge is not None and dims["present"]:
        try:
            sem = float(judge(term, d))
            sem = max(0.0, min(1.0, sem))
            dims["semantic"] = round(sem, 3)
            score = 0.6 * score + 0.4 * sem
            if sem < 0.5:
                issues.append("语义充分性偏低(judge)")
        except Exception:
            pass   # judge 不可用/异常:如实退回确定性分,不臆造语义分

    return {"score": round(score, 3), "dims": dims, "issues": issues}


def score_ontology(ir, gold_glossary=None, weak_threshold=0.6):
    """批量给本体所有对象的定义打分 + 聚合。
    gold_glossary: 可选 {术语中文名: 参考集定义};按对象 cn/name 匹配。"""
    gold_glossary = gold_glossary or {}
    objs = ir.get("objects", [])
    rows, scores = [], []
    for o in objs:
        term = o.get("cn") or o.get("name") or ""
        gold = gold_glossary.get(term)
        r = score_definition(term, o.get("definition", ""),
                             o.get("counterExample", ""), gold=gold)
        rows.append({"term": term, **r})
        scores.append(r["score"])
    mean = round(sum(scores) / len(scores), 3) if scores else 0.0
    weak = sum(1 for s in scores if s < weak_threshold)
    return {"scored": len(rows), "mean_score": mean,
            "weak": weak, "weak_threshold": weak_threshold,
            "rows": sorted(rows, key=lambda x: x["score"]),
            "note": "确定性定义质量评分:结构(属加种差/非循环/反例)+ 参考式重叠;"
                    "与元数据覆盖统计正交(前者测填没填,本表测填得对不对)"}
