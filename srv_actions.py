#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动作层共享存储与参数 schema 原语。

DR-043 的路由拆分要求 blueprint 不反向导入 ``server``。动作注册表同时被动作 API
与本体构建投影消费，因此把文件位置、读取和 schema 校验留在这一层；HTTP 行为放在
``bp_actions``，主应用只注入数据目录查询函数。
"""
import json
import os
import re

from srv_context import WORK

ACTION_TYPES_F = os.path.join(WORK, "action_types.json")
ACTION_LOG_F = os.path.join(WORK, "action_log.json")
ACTION_PARAM_TYPES = ("text", "textarea", "select")


def load_action_types():
    try:
        with open(ACTION_TYPES_F, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return []


def load_action_log():
    try:
        with open(ACTION_LOG_F, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return []


def valid_action_params(params):
    """参数 schema 校验 → 规整后的列表；非法返回 ``None``。"""
    if not isinstance(params, list) or len(params) > 12:
        return None
    out, seen = [], set()
    for param in params:
        if not isinstance(param, dict):
            return None
        name = str(param.get("name") or "").strip()[:30]
        if not re.match(r"^[a-z][a-z0-9_]*$", name) or name in seen:
            return None
        seen.add(name)
        typ = param.get("type") if param.get("type") in ACTION_PARAM_TYPES else "text"
        row = {
            "name": name,
            "cn": str(param.get("cn") or "").strip()[:30] or name,
            "type": typ,
            "required": bool(param.get("required")),
        }
        if typ == "select":
            options = [
                str(option).strip()[:40]
                for option in (param.get("options") or [])
                if str(option).strip()
            ][:12]
            if not options:
                return None
            row["options"] = options
        out.append(row)
    return out
