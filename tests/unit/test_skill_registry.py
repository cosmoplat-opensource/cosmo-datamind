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


class TestFrontMatter:
    """技能元数据只从 front matter 取。

    回归背景:摘要原先在**全文**里搜 `description:`,server.py 里另有两份更弱的抄写
    (无 ^ 锚定、无 re.M,正文任意位置命中即算)。技能正文里以 `description:` 开头的
    行——字段清单、YAML 示例——很常见,于是技能列表会把示例值当成这个技能的摘要显示。
    """

    def test_body_description_line_is_not_taken_as_summary(self, tmp_path):
        d = tmp_path / "probe"; d.mkdir()
        (d / "SKILL.md").write_text("# 技能\n\n## 输出字段\n\ndescription: 对象定义字段\n",
                                    encoding="utf-8")
        assert skill_registry.discover([str(tmp_path)])[0]["description"] == ""

    def test_front_matter_wins_over_body_line(self, tmp_path):
        d = tmp_path / "probe"; d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: probe\ndescription: 真正的摘要\n---\n\ndescription: 干扰项\n",
            encoding="utf-8")
        assert skill_registry.discover([str(tmp_path)])[0]["description"] == "真正的摘要"

    def test_optional_metadata_is_exposed(self, tmp_path):
        """Agent Skills 约定的可选键(license/version/allowed-tools)原样透出。"""
        d = tmp_path / "probe"; d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: probe\ndescription: d\nlicense: Apache-2.0\nversion: 1.2\n"
            "allowed-tools: Read, Bash\n---\n\n正文\n", encoding="utf-8")
        meta = skill_registry.discover([str(tmp_path)])[0]["meta"]
        assert meta["license"] == "Apache-2.0" and meta["version"] == "1.2"
        assert meta["allowed-tools"] == "Read, Bash"

    def test_unknown_keys_do_not_break_parsing(self, tmp_path):
        """技能文件由使用者手写;多写一个键不该让技能整个不可用。"""
        d = tmp_path / "probe"; d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: probe\ndescription: d\n随手写的键: 值\nfoo: bar\n---\n\n正文\n",
            encoding="utf-8")
        it = skill_registry.discover([str(tmp_path)])[0]
        assert it["description"] == "d" and "foo" not in it["meta"]

    def test_crlf_and_quotes(self):
        assert skill_registry.front_matter(
            "---\r\nname: a\r\ndescription: CRLF\r\n---\r\n正文")["description"] == "CRLF"
        assert skill_registry.front_matter(
            '---\nname: a\ndescription: "引号"\n---\n正文')["description"] == "引号"

    def test_no_front_matter_returns_empty(self):
        assert skill_registry.front_matter("# 只有正文\n") == {}
        assert skill_registry.front_matter("") == {}

    def test_body_is_unaffected_by_metadata_parsing(self):
        """解析元数据不得吃掉正文——正文是真正注入构建 prompt 的东西。"""
        text = "---\nname: a\ndescription: d\n---\n\n## 步骤\n\n正文内容\n"
        assert skill_registry.skill_body(text).startswith("## 步骤")
