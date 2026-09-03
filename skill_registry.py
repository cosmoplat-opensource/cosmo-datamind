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
_FRONT_MATTER = re.compile(r"^---\r?\n([\s\S]*?)\r?\n---\r?\n")
_SCALAR = re.compile(r"^([A-Za-z][\w\-]*):\s*(.*?)\s*$", re.M)

# Agent Skills 约定的元数据键。name/description 是必备(列表与摘要都读 description),
# 其余为可选:license 标注技能正文的许可,version 便于追踪改版,allowed-tools 声明
# 该技能预期使用的工具。未知键一律忽略而非报错——技能文件由使用者手写,多写一个键
# 不该让技能整个不可用。
_META_KEYS = ("name", "description", "license", "version", "allowed-tools")


def front_matter(text):
    """解析 SKILL.md 的 YAML front matter,返回标量键值(无 front matter 则空字典)。

    只认顶层标量键——技能元数据本就是扁平的,自己扫一遍即可,不引入 YAML 依赖
    (本模块的其余部分只用 stdlib,注册表要在没装任何第三方包的环境里也能工作)。
    """
    m = _FRONT_MATTER.match(text or "")
    if not m:
        return {}
    out = {}
    for key, value in _SCALAR.findall(m.group(1)):
        out[key] = value.strip().strip('"\'')
    return out


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
            # 摘要只从 front matter 取,不在全文里搜:技能正文里以 `description:` 开头的
            # 行(字段清单、YAML 示例)很常见,全文搜会把示例值当成这个技能的摘要显示。
            meta = front_matter(text)
            found[name] = {
                "name": name,
                "description": (meta.get("description") or "")[:240],
                "directory": directory,
                "skill_md": skill_md,
                "runnable": os.path.isfile(os.path.join(directory, "run.sh")),
                "root": root,
                "meta": {k: meta[k] for k in _META_KEYS if k in meta},
            }
    return [found[name] for name in sorted(found)]


def find(name, roots):
    """按安全名称查找技能;不存在返回 None。"""
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
        return None
    return next((item for item in discover(roots) if item["name"] == name), None)


def method_text(names, roots, custom_loader=None, per_skill_cap=3500, total_cap=12000,
                fallback_loader=None):
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
        if not text and fallback_loader:
            # 注册表与覆盖件都没有时才用兼容兜底(历史技能名的一行摘要)。
            # 放在最后:它一旦参与前面的优先级,就会顶掉真正的 SKILL.md 正文。
            text = fallback_loader(name) or ""
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
