# -*- coding: utf-8 -*-
"""能源与碳排放领域技能:随仓内置、可被发现,且正文包含实测总结出的关键规则。

技能正文会进入构建提示词(DR-050),改动即改变构建行为;这里锁住不可丢的规则。
对照实验(同数据、同表、同诉求)中,加上本技能后多态引用被识别 7 条(原 0 条)、
计量关系被建成带属性的关联对象、能源介质以字典值为个体出现。
"""
import os

import skill_registry

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _skill():
    items = {i["name"]: i for i in skill_registry.discover([os.path.join(ROOT, "skills_seed")])}
    assert "energy-carbon-ontology" in items
    return items["energy-carbon-ontology"]


def test_discoverable_with_metadata():
    it = _skill()
    assert it["description"].startswith("能源管理与碳排放领域")
    assert it["meta"]["license"] == "Apache-2.0" and it["meta"]["version"] == "1.1"


def test_body_keeps_the_rules_learned_from_real_data():
    body = skill_registry.skill_body(open(_skill()["skill_md"], encoding="utf-8").read())
    for rule in ("不要一张表建一个对象", "关联表建成关系", "租户键不是业务关系", "多态引用", "类别作用域编码",
                 "软删除", "编码口径冲突", "自增 id 碰撞", "排放因子必须作为独立概念", "二次能源",
                 "GB/T 32150-2025"):
        assert rule in body, rule


def test_body_fits_prompt_budget():
    """单个技能注入上限 3500 字(skill_registry.method_text 的 per_skill_cap),正文不应被截断。"""
    body = skill_registry.skill_body(open(_skill()["skill_md"], encoding="utf-8").read())
    assert len(body) <= 3500


def test_no_system_specific_identifiers():
    """开源仓里的通用方法只写规律,不写具体库名、表名或主机。

    按命名模式检查,而不是列出具体名称——把客户或系统名写进禁用词清单,
    等于把它们带进了开源仓。"""
    import re
    text = open(_skill()["skill_md"], encoding="utf-8").read()
    assert not re.search(r"\b[a-z]+_(db|platform_db)\b|\b[a-z]+\d+__|\b\d{1,3}(\.\d{1,3}){3}\b|https?://", text)
    assert not re.search(r"\b[a-z]+__[a-z_]+\b", text)          # 抽样库的「库名__表名」形式
