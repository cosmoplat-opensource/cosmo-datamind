# -*- coding: utf-8 -*-
"""从决策倒推的构建方法作为内置技能提供(DR-118):方法沉淀为技能,而不是只改流水线。"""
import os

import cq_check
import ontology_decision as od
import skill_registry

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _skill():
    items = {x["name"]: x for x in skill_registry.discover([os.path.join(ROOT, "skills_seed")])}
    assert "decision-driven-ontology" in items
    return items["decision-driven-ontology"]


def test_the_skill_is_listed_as_a_builtin_with_a_description():
    s = _skill()
    assert "决策" in s["description"] and "验收问题" in s["description"]


def test_the_skill_names_only_fields_the_platform_actually_accepts():
    body = skill_registry.skill_body(open(_skill()["skill_md"], encoding="utf-8").read())
    for field in ("route", "roles", "expect", "role", "routes", "constraints", "lighting", "/api/ont/release"):
        assert field in body, field
    for status in od.LIGHT:
        assert status in body, status
    for status in ("candidate", "evidenced", "certified"):
        assert status in od.STATUSES and status in body
    assert hasattr(cq_check, "check_route")


def test_the_skill_carries_no_customer_or_internal_names():
    body = open(_skill()["skill_md"], encoding="utf-8").read().lower()
    # 名称本身不写进仓库:只存小写名称的长度与 SHA-256,逐段比对技能正文
    import hashlib
    digests = (
        (4, "83590d8cbc109a1cd6797ae0966301eb009c30b83811ec21f16e0fe24dc9faf6"),
        (7, "30e363d3e8c59f2c1319f8d73d48e3ad26db5e087951a4d7ab809c6f5401aea8"),
        (3, "b61c7d641d8875bbd2d3e3c541cc756ad7575f05dd6382b9bbc93b1b77422778"),
        (3, "b7576f5c92a16572a4b26a19a1c5e37994e4b75df2ed80a0c2685bf81d777d8e"),
        (9, "4d748ebd71af03a7c325a5ecef86e3768b71597ba3c08dc26624bfdbd84a0a34"),
        (2, "648ac0860a36272a40d4f168cb36bd7d8719535604e3e68f8c51f49634a9cc44"),
        (3, "11a3f0a50cc6432a11f0e55b2d88a47c6a7e4a2d393da90e097d053c10ce3093"),
    )
    for n, digest in digests:
        found = any(hashlib.sha256(body[i:i + n].encode("utf-8")).hexdigest() == digest for i in range(len(body) - n + 1))
        assert not found, digest[:12]
