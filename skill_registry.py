#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本体构建技能注册表。

DataMind 可以独立运行,上游 ontology-engine 只是可选增强。因此内置技能来自两层:
本仓 ``skills_seed``(始终可用) + 上游 ``web/skills_seed``(若已配置)。本模块把发现、
读取与 prompt 注入做成确定性纯函数,避免各 API 各扫一套目录而出现「列表里有、构建时没用」
或「没有引擎就一个技能也没有」的漂移。
"""
from __future__ import annotations

import os
import re

_SAFE_NAME = re.compile(r"^[\w\-]{1,80}$")
_FRONT_MATTER = re.compile(r"^---\r?\n[\s\S]*?\r?\n---\r?\n")
_DESCRIPTION = re.compile(r"^description:\s*(.+?)\s*$", re.M)


def skill_body(text):
    """移除 YAML front matter,返回可注入方法论正文。"""
    text = text or ""
    m = _FRONT_MATTER.match(text)
    return text[m.end():].strip() if m else text.strip()


def discover(roots):
    """按 roots 优先级发现技能;同名时先出现的根胜出。"""
    found = {}
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            if name in found or not _SAFE_NAME.fullmatch(name):
                continue
            directory = os.path.join(root, name)
            skill_md = os.path.join(directory, "SKILL.md")
            if not os.path.isdir(directory) or not os.path.isfile(skill_md):
                continue
            try:
                text = open(skill_md, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            match = _DESCRIPTION.search(text)
            found[name] = {
                "name": name,
                "description": (match.group(1).strip().strip('"\'') if match else "")[:240],
                "directory": directory,
                "skill_md": skill_md,
                "runnable": os.path.isfile(os.path.join(directory, "run.sh")),
                "root": root,
            }
    return [found[name] for name in sorted(found)]


def find(name, roots):
    """按安全名称查找技能;不存在返回 None。"""
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
        return None
    return next((item for item in discover(roots) if item["name"] == name), None)


def method_text(names, roots, custom_loader=None, per_skill_cap=3500, total_cap=12000):
    """把真实 SKILL.md 正文与自定义技能正文编入构建 prompt。

    返回 ``(文本, 已注入技能名)``。总长有硬上限,防止技能内容挤掉数据库与业务证据。
    """
    entries = {item["name"]: item for item in discover(roots)}
    parts, used, total = [], [], 0
    for name in names or []:
        if name in used:
            continue
        text = ""
        # 用户改写优先于随仓内置:同名时以 workdir 里的覆盖件为准。
        # 原先内置优先,导致「编辑内置技能」在查看页显示为已改、构建时却仍用出厂正文——
        # 改一处不生效比不给改更容易误导人。
        if custom_loader:
            text = custom_loader(name) or ""
        if not text:
            entry = entries.get(name)
            if entry:
                try:
                    text = open(entry["skill_md"], encoding="utf-8", errors="replace").read()
                except OSError:
                    text = ""
        body = skill_body(text)[:per_skill_cap]
        if not body:
            continue
        segment = f"〔技能 {name}〕\n{body}"
        room = total_cap - total
        if room <= 0:
            break
        segment = segment[:room]
        parts.append(segment)
        used.append(name)
        total += len(segment)
    return "\n\n".join(parts), used
