# -*- coding: utf-8 -*-
"""IR 形状兼容规则与路径限定的单一事实源。

背景:_rels 曾在六个模块各抄一份、_obj_names 两份且不一致(intent_check 少收
app 形状的 tables)、_confined 两份略异的安全关键实现。归一后由本测试锁住行为。
"""
import os

import pytest

import ir_shape
import srv_context


class TestRels:
    def test_demo_shape_uses_links(self):
        ir = {"links": [{"source": "a", "target": "b"}]}
        lst, sk, tk = ir_shape.rels(ir)
        assert lst is ir["links"] and (sk, tk) == ("source", "target")

    def test_built_shape_uses_relations(self):
        ir = {"relations": [{"source_concept": "a", "target_concept": "b"}]}
        lst, sk, tk = ir_shape.rels(ir)
        assert lst is ir["relations"] and (sk, tk) == ("source_concept", "target_concept")

    def test_readonly_default_does_not_mutate(self):
        """检查类调用不得往旧产物里塞键——只读语义是五个检查模块的共同前提。"""
        ir = {"objects": []}
        lst, _, _ = ir_shape.rels(ir)
        assert lst == [] and "relations" not in ir

    def test_create_flag_materializes_relations(self):
        """server 的编辑回放需要在缺 relations 的旧产物上就地补空列表。"""
        ir = {"objects": []}
        lst, _, _ = ir_shape.rels(ir, create=True)
        lst.append({"source_concept": "a", "target_concept": "b"})
        assert ir["relations"] and ir["relations"][0]["source_concept"] == "a"

    def test_links_wins_when_both_present(self):
        """两键并存时以 links 为准——与六份旧实现的判定顺序一致,不悄悄改变行为。"""
        ir = {"links": [], "relations": [{"source_concept": "x", "target_concept": "y"}]}
        lst, sk, _ = ir_shape.rels(ir)
        assert lst == [] and sk == "source"


class TestObjNames:
    def test_superset_includes_tables(self):
        """app 形状的多表概念必须能按表名指代——intent_check 旧实现漏掉的正是这里。"""
        o = {"name": "指标", "cn": "营收", "aliases": ["收入"],
             "tables": ["fact_a", "fact_b"]}
        names = ir_shape.obj_names(o)
        assert {"指标", "营收", "收入", "fact_a", "fact_b"} <= set(names)

    def test_blank_and_nonstring_skipped(self):
        o = {"id": "  ", "name": "客户", "aliases": [None, "", "买方"], "tables": [3]}
        assert ir_shape.obj_names(o) == ["客户", "买方"]


class TestConfine:
    def test_inside_ok(self, tmp_path):
        p = srv_context.confine(str(tmp_path), "a", "b.json")
        assert p.startswith(os.path.realpath(str(tmp_path)) + os.sep)

    def test_dotdot_escape_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            srv_context.confine(str(tmp_path), "..", "evil")

    def test_symlink_escape_rejected(self, tmp_path):
        """realpath 版比旧 normpath 版强:符号链接逃逸也拦得住。"""
        outside = tmp_path / "outside"; outside.mkdir()
        base = tmp_path / "base"; base.mkdir()
        (base / "ln").symlink_to(outside)
        with pytest.raises(ValueError):
            srv_context.confine(str(base), "ln", "x")

    def test_base_itself_allowed(self, tmp_path):
        assert srv_context.confine(str(tmp_path)) == os.path.realpath(str(tmp_path))
