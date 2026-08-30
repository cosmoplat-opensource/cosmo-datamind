# -*- coding: utf-8 -*-
import skill_registry


def _skill(root, name, body, desc="说明"):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n", encoding="utf-8")
    return d


def test_local_root_wins_and_real_body_is_injected(tmp_path):
    local, engine = tmp_path / "local", tmp_path / "engine"
    _skill(engine, "same", "上游正文")
    _skill(local, "same", "本地正文", "本地说明")
    items = skill_registry.discover([str(local), str(engine)])
    assert [i["name"] for i in items] == ["same"]
    assert items[0]["description"] == "本地说明"
    text, used = skill_registry.method_text(["same"], [str(local), str(engine)])
    assert used == ["same"]
    assert "本地正文" in text and "上游正文" not in text


def test_custom_fallback_and_prompt_cap(tmp_path):
    text, used = skill_registry.method_text(
        ["mine"], [str(tmp_path / "missing")], custom_loader=lambda name: "X" * 50,
        per_skill_cap=20, total_cap=40)
    assert used == ["mine"]
    assert "技能 mine" in text
    assert len(text) <= 40


def test_find_rejects_path_traversal(tmp_path):
    _skill(tmp_path, "safe", "正文")
    assert skill_registry.find("safe", [str(tmp_path)]) is not None
    assert skill_registry.find("../safe", [str(tmp_path)]) is None


def test_fallback_never_shadows_real_skill_md(tmp_path):
    """兼容兜底不得顶掉真正的 SKILL.md。

    回归背景:DR-051 把解析顺序改成「覆盖优先」后,若把一行硬编码摘要放进
    custom_loader,它非空即胜出——4552 字的技能正文被压成 70 字进提示词,
    技能等于没生效,而流水线上「技能注入 · N 字」还显示成功。
    """
    root = tmp_path / "seed" / "demo-skill"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: demo-skill\ndescription: d\n---\n\n完整技能正文" * 40,
                                   encoding="utf-8")
    text, used = skill_registry.method_text(
        ["demo-skill"], (str(tmp_path / "seed"),),
        custom_loader=lambda n: "",
        fallback_loader=lambda n: "一行摘要")
    assert "完整技能正文" in text and "一行摘要" not in text
    assert used == ["demo-skill"] and len(text) > 200


def test_fallback_used_only_when_nothing_else_found(tmp_path):
    """注册表与覆盖件都没有时,兜底才生效(历史技能名的兼容路径)。"""
    text, used = skill_registry.method_text(
        ["legacy-name"], (str(tmp_path),),
        custom_loader=lambda n: "", fallback_loader=lambda n: "历史技能摘要")
    assert "历史技能摘要" in text and used == ["legacy-name"]


def test_custom_override_wins_over_builtin(tmp_path):
    """用户改写优先于出厂正文(DR-051)。"""
    root = tmp_path / "seed" / "s1"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: s1\n---\n\n出厂正文", encoding="utf-8")
    text, _ = skill_registry.method_text(
        ["s1"], (str(tmp_path / "seed"),),
        custom_loader=lambda n: "---\nname: s1\n---\n\n我的改写版",
        fallback_loader=lambda n: "摘要")
    assert "我的改写版" in text and "出厂正文" not in text
