#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图谱的发布状态(DR-115):作业完成、质量结论、正式发布是三件事。

- 构建作业结束只说明产出了一份草案;
- 验收检查给出质量结论(pass / review / fail),记在图谱的 build_quality 里;
- 发布是人的决定,记在工作区的发布记录里,带发布人、时间和所发布内容的修订号。

正式问数按下面的规则取用图谱。本模块只做判定,不读写文件,不依赖 Web 层。
"""
from __future__ import annotations

import hashlib
import json

STATES = ("published", "draft", "blocked")


def quality_of(ir):
    """图谱上记录的质量结论;内置图谱和早期产物没有这条记录,返回空串。"""
    q = (ir or {}).get("build_quality")
    return str((q or {}).get("result") or "") if isinstance(q, dict) else ""


# 不计入修订的部分:构建过程的记录与每次检查都会重算的结果。其余内容(对象、关系、指标、约束、路线……)变了,修订就变。
_VOLATILE = ("build_history", "build_manifest", "build_quality", "gaps", "scenario")


def revision_of(ir):
    """图谱内容的修订号:内容的摘要。发布绑定到修订,内容一变(再构建一轮、人工编辑),发布即失效。
    用内容而不是构建轮次计数,是因为构建历史只保留最近若干条,计数到上限后不再变化。"""
    body = {k: v for k, v in (ir or {}).items() if k not in _VOLATILE}
    blob = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    return "rev_" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def state(ir, record, strict=False, revision=None):
    """→ {state, usable, quality, reason, revision, ...发布记录}。

    published  有人发布过,且发布的就是当前这份内容;
    blocked    验收检查不通过,或者已被人撤回;
    draft      其余情况:待评审、通过但未发布、发布后内容又变了、没有质量记录的内置与早期产物。

    usable 表示正式问数能否取用:published 可以;blocked 不可以;draft 默认可以,
    严格模式(共享部署)下只有 published 可以。revision 可由调用方传入(已有缓存时省去重算)。
    """
    quality = quality_of(ir)
    rec = record if isinstance(record, dict) else {}
    rev = revision or revision_of(ir)
    current = rec.get("state") == "published" and rec.get("revision") == rev
    if quality == "fail":
        out = {"state": "blocked", "usable": False, "reason": "验收检查不通过,修复阻断问题并重新构建后才能用于正式问数"}
    elif rec.get("state") == "withdrawn" and rec.get("revision") in (None, rev):
        out = {"state": "blocked", "usable": False, "reason": "已被撤回,重新发布后才能用于正式问数"}
    elif current:
        out = {"state": "published", "usable": True, "reason": ""}
    else:
        stale = rec.get("state") == "published"
        out = {"state": "draft", "usable": not strict,
               "reason": ("发布之后内容有变化(再次构建或人工编辑),需要重新发布" if stale else "尚未发布") if strict or stale else ""}
    out["quality"] = quality
    out["revision"] = rev
    for k in ("by", "at"):
        if (current or out["state"] == "blocked") and rec.get(k) is not None:
            out[k] = rec[k]
    return out
