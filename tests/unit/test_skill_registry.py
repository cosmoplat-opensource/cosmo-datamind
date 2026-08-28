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
