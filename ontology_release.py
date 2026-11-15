#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图谱的发布状态(DR-115):作业完成、质量结论、正式发布是三件事。

- 构建作业结束只说明产出了一份草案;
- 验收检查给出质量结论(pass / review / fail),记在图谱的 build_quality 里;
- 发布是人的决定,记在工作区的发布记录里,带发布人、时间和当时的构建轮次。

正式问数按下面的规则取用图谱。本模块只做判定,不读写文件,不依赖 Web 层。
"""
from __future__ import annotations

STATES = ("published", "draft", "blocked")


def quality_of(ir):
    """图谱上记录的质量结论;内置图谱和早期产物没有这条记录,返回空串。"""
    q = (ir or {}).get("build_quality")
    return str((q or {}).get("result") or "") if isinstance(q, dict) else ""


def round_of(ir):
    """图谱经历的构建轮次数。发布记录按它判断「发布之后有没有再构建」。"""
    hist = (ir or {}).get("build_history")
    return len(hist) if isinstance(hist, list) else 0


def state(ir, record, strict=False):
    """→ {state, usable, quality, reason, ...发布记录}。

    published  有人发布过,且之后没有新的构建轮次;
    blocked    验收检查不通过,且没有有效的发布记录;
    draft      其余情况:待评审、通过但未发布、没有质量记录的内置与早期产物。

    usable 表示正式问数能否取用:published 可以;blocked 不可以;draft 默认可以,
    严格模式(共享部署)下只有 published 可以。
    """
    quality = quality_of(ir)
    rec = record if isinstance(record, dict) else {}
    current = rec.get("state") == "published" and int(rec.get("round") or 0) == round_of(ir)
    if current and quality != "fail":
        out = {"state": "published", "usable": True, "reason": ""}
    elif quality == "fail":
        out = {"state": "blocked", "usable": False, "reason": "验收检查不通过,修复阻断问题并重新构建后才能用于正式问数"}
    else:
        stale = rec.get("state") == "published" and not current
        out = {"state": "draft", "usable": not strict,
               "reason": ("发布之后又构建过,需要重新发布" if stale else "尚未发布") if strict or stale else ""}
    out["quality"] = quality
    for k in ("by", "at", "round"):
        if current and rec.get(k) is not None:
            out[k] = rec[k]
    return out
