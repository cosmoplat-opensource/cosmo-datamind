# -*- coding: utf-8 -*-
"""LLM 回复 JSON 抽取与本体提议形状校验(DR-058)。

旧路径 `re.search(r"\\{[\\s\\S]*\\}")` 的两类真实失败形态都要挡住:
回复前后夹带花括号文字时取错范围;尾逗号等小毛病把可修复的解析失败
当成引擎故障,浪费一次几分钟的长推理。同时形状契约必须剔除坏元素,
不让一个缺 name 的对象或空 source 的关系拖垮整份提议。
"""
import llm_json


class TestExtract:
    def test_plain_object(self):
        assert llm_json.extract('{"objects": [1]}') == {"objects": [1]}

    def test_object_wrapped_in_prose_and_fences(self):
        text = '好的，以下是结果：\n```json\n{"objects": [{"name": "a"}]}\n```\n如需调整请告知。'
        assert llm_json.extract(text) == {"objects": [{"name": "a"}]}

    def test_braces_inside_strings_do_not_break_balance(self):
        text = r'{"note": "格式 {错}也\"不会\" 影响配平", "ok": true}'
        assert llm_json.extract(text)["ok"] is True

    def test_trailing_comma_is_repaired(self):
        assert llm_json.extract('{"a": [1, 2, 3,], "b": {"c": 1,},}') == \
            {"a": [1, 2, 3], "b": {"c": 1}}

    def test_nonfinite_with_other_flaws_is_repaired_to_null(self):
        """json.loads 原生容忍 NaN(与旧路径一致);但与尾逗号并存导致原样解析失败时,
        最小修复把非有限值收紧为 null,证据数值不允许 NaN 进出。"""
        assert llm_json.extract('{"x": NaN,}') == {"x": None}

    def test_prefix_noise_then_real_payload(self):
        """首个 `{` 属于正文杂文、配平失败时,扫描应继续找到真正的 JSON 块。"""
        text = '说明:集合写法 {a, b} 不是 JSON。\n结果:{"objects": [], "relations": []}'
        assert llm_json.extract(text) == {"objects": [], "relations": []}

    def test_first_valid_block_wins(self):
        text = '{"first": true} 后置说明 {"second": false}'
        assert llm_json.extract(text) == {"first": True}

    def test_no_json_returns_none(self):
        assert llm_json.extract("完全没有结构化内容") is None
        assert llm_json.extract("{未配平的块") is None
        assert llm_json.extract(None) is None
        assert llm_json.extract(123) is None

    def test_object_inside_top_level_list_is_returned(self):
        """顶层 list 里的对象会被取出——旧贪婪正则同样如此,后续按「不含 objects」
        判定无效并换下一引擎,语义保持一致。"""
        assert llm_json.extract('[{"a": 1}]') == {"a": 1}

    def test_balanced_but_unparseable_then_inner_payload(self):
        """外层块配平但非法(尾随杂质)时,应继续尝试内层候选。"""
        text = '{"broken": <占位符>, "inner": {"objects": [1]}}'
        assert llm_json.extract(text) == {"objects": [1]}


class TestValidateProposal:
    def test_good_proposal_passes_through(self):
        p = {"objects": [{"name": "a"}, {"name": "b"}],
             "relations": [{"source": "a", "target": "b", "verb": "关联"}],
             "extra": "保留未知键"}
        out, dropped = llm_json.validate_proposal(p)
        assert out["extra"] == "保留未知键" and dropped == {"objects": 0, "relations": 0}

    def test_bad_elements_are_dropped_and_counted(self):
        p = {"objects": [{"cn": "缺 name"}, {"name": "  "}, {"name": "ok"}, "非字典"],
             "relations": [{"source": "", "target": "b"}, {"source": "ok"},
                           {"source": "ok", "target": "b"}, None]}
        out, dropped = llm_json.validate_proposal(p)
        assert [o["name"] for o in out["objects"]] == ["ok"]
        assert out["relations"] == [{"source": "ok", "target": "b"}]
        assert dropped == {"objects": 3, "relations": 3}

    def test_malformed_container_yields_empty_not_crash(self):
        out, dropped = llm_json.validate_proposal({"objects": "不是列表", "relations": None})
        assert out["objects"] == [] and out["relations"] == []
        assert dropped == {"objects": 0, "relations": 0}

    def test_oversized_proposal_is_clamped(self):
        p = {"objects": [{"name": f"o{i}"} for i in range(10)],
             "relations": [{"source": "a", "target": "b"}] * 8}
        out, dropped = llm_json.validate_proposal(p, max_objects=3, max_relations=2)
        assert len(out["objects"]) == 3 and len(out["relations"]) == 2
        assert dropped == {"objects": 7, "relations": 6}


class TestExtractProposal:
    def test_end_to_end(self):
        text = '```json\n{"objects": [{"name": "fact_sales_order"}], ' \
               '"relations": [{"source": "x", "target": "", "verb": "关联"}],}\n```'
        proposal, dropped = llm_json.extract_proposal(text)
        assert proposal["objects"] == [{"name": "fact_sales_order"}]
        assert proposal["relations"] == []
        assert dropped["relations"] == 1

    def test_no_objects_is_none(self):
        proposal, _ = llm_json.extract_proposal('{"objects": [], "relations": []}')
        assert proposal is None

    def test_no_json_is_none_with_zero_stats(self):
        proposal, dropped = llm_json.extract_proposal("纯文本")
        assert proposal is None and dropped == {"objects": 0, "relations": 0}
