# -*- coding: utf-8 -*-
"""长文证据的分块与检索。

回归背景:构建取证对每份上传文档只读前 3500 字、总块再截到 9000 字。能碳平台 5 份
设计文档共 41 万字,模型只看得到开头约 2%,排在后面的表的中文含义、枚举与关系说明
全部丢失。
"""
import pytest

import doc_chunks as dc


def _design_doc(n_tables=120, filler=600):
    parts = ["# demo_db 数据库设计文档", "## 1. 概览", "说明" * 50, "## 3. 表结构详细设计"]
    for i in range(n_tables):
        parts += [f"### 3.{i} tbl_{i:03d} —— 业务表{i}", "", "| 字段名 | 数据类型 | 说明 |", "| --- | --- | --- |",
                  "| id | bigint | 主键 |", f"| ref_{i}_no | varchar | 关联编号{i} |", "x" * filler, ""]
    return "\n".join(parts)


class TestTokenize:
    def test_cjk_bigrams_and_identifier_parts(self):
        toks = dc.tokenize("告警工单 alarm_work_order")
        assert {"告警", "警工", "工单", "alarm_work_order", "alarm", "work", "order"} <= set(toks)

    def test_single_cjk_char_kept(self):
        assert "表" in dc.tokenize("表")


class TestChunking:
    def test_heading_path_and_provenance(self):
        cs = dc.chunk_text(_design_doc(3, filler=10), "d.md")
        tbl = next(c for c in cs if "tbl_001" in c["path"])
        assert tbl["path"].endswith("3.1 tbl_001 —— 业务表1")
        assert tbl["id"] == f"d.md#{tbl['index']}" and tbl["line"] > 1
        assert len({c["id"] for c in cs}) == len(cs)

    def test_table_separator_rows_are_compacted(self):
        cs = dc.chunk_text(_design_doc(1, filler=10), "d.md")
        assert not any("| --- |" in c["text"] for c in cs)

    def test_long_table_is_split_and_repeats_header(self):
        rows = "\n".join(f"| col_{i} | int | 说明{i} |" for i in range(200))
        text = "### big_table —— 大表\n| 字段名 | 数据类型 | 说明 |\n| --- | --- | --- |\n" + rows
        cs = dc.chunk_text(text, "d.md", max_chars=500)
        assert len(cs) > 3 and all(len(c["text"]) <= 500 + 200 for c in cs)
        assert all(c["text"].count("| 字段名 |") >= 1 for c in cs[1:])

    def test_bold_subtitle_is_a_heading_but_label_lines_are_not(self):
        text = ("### 3.13 其它表\n**meta_index_cycle —— 指标周期**\n| a | b |\n"
                "**唯一索引：** `uix`（a）\n**meta_index_formula（指标公式表）**\n| c | d |")
        paths = [c["path"] for c in dc.chunk_text(text, "m.md")]
        assert any(p.endswith("meta_index_cycle —— 指标周期") for p in paths)
        assert any(p.endswith("meta_index_formula（指标公式表）") for p in paths)
        assert not any("唯一索引" in p for p in paths)

    def test_plain_text_without_headings_is_packed(self):
        cs = dc.chunk_text("\n\n".join("段落" * 80 for _ in range(10)), "t.txt", max_chars=400)
        assert len(cs) >= 5 and all(c["path"] == "" for c in cs)


class TestSelect:
    def test_table_at_end_of_long_document_is_anchored(self):
        """旧实现的核心缺陷:文档尾部的表永远进不了提示词。"""
        cs = dc.chunk_text(_design_doc(120), "big.md")
        picked, st = dc.select(cs, anchors=["tbl_119", "tbl_000"], budget=4000)
        assert st["anchors_covered"] == 2
        assert any("tbl_119" in c["path"] for c in picked)

    def test_db_prefixed_anchor_matches_bare_table_name(self):
        cs = dc.chunk_text(_design_doc(5, filler=10), "d.md")
        picked, st = dc.select(cs, anchors=["ems__tbl_003"], budget=3000)
        assert st["anchors_covered"] == 1 and "tbl_003" in picked[0]["path"]

    def test_heading_hit_beats_mere_mention(self):
        text = "### 概览\n本库的 tbl_007 很重要 tbl_007 tbl_007\n### 3.7 tbl_007 —— 目标表\n| id | 主键 |"
        picked, _ = dc.select(dc.chunk_text(text, "d.md"), anchors=["tbl_007"], budget=2000, anchor_share=0.2)
        assert "目标表" in picked[0]["path"]

    def test_missing_anchor_is_reported(self):
        cs = dc.chunk_text(_design_doc(3, filler=10), "d.md")
        _, st = dc.select(cs, anchors=["tbl_001", "not_there"], budget=3000)
        assert st["anchors_missing"] == ["not_there"]

    def test_budget_is_respected(self):
        cs = dc.chunk_text(_design_doc(120), "big.md")
        picked, st = dc.select(cs, query="业务表 关联编号", anchors=[f"tbl_{i:03d}" for i in range(120)], budget=5000)
        assert st["chars"] <= 5000 and sum(len(c["text"]) for c in picked) == st["chars"]

    def test_query_retrieval_fills_remaining_budget(self):
        text = "# 手册\n## 告警处理\n告警产生后自动转为工单,由值班人员处理\n## 无关\n" + "天气晴朗" * 50
        picked, st = dc.select(dc.chunk_text(text, "h.md"), query="告警如何转成工单", budget=2000)
        assert picked and "告警处理" in picked[0]["path"]

    def test_render_carries_provenance(self):
        cs = dc.chunk_text(_design_doc(2, filler=10), "d.md")
        picked, _ = dc.select(cs, anchors=["tbl_001"], budget=2000)
        out = dc.render(picked)
        assert "证据片段「d.md」§" in out and "(块" in out

    def test_deterministic(self):
        cs = dc.chunk_text(_design_doc(50), "d.md")
        a = dc.select(cs, query="关联编号", anchors=["tbl_010", "tbl_040"], budget=3000)
        b = dc.select(cs, query="关联编号", anchors=["tbl_010", "tbl_040"], budget=3000)
        assert a == b


def test_gather_evidence_reads_whole_document_via_chunks(make_sqlite, tmp_path, monkeypatch):
    """集成:长文档尾部描述的表,经 _gather_evidence 进入提示词证据。"""
    server = pytest.importorskip("server")
    work = tmp_path / "work"; work.mkdir()
    (work / "uploads_design.md").write_text(_design_doc(150), encoding="utf-8")
    monkeypatch.setattr(server, "WORK", str(work))
    db = make_sqlite({"tbl_149": ("id INTEGER PRIMARY KEY, ref_149_no TEXT", [])})
    ev = server._gather_evidence(db, query="关联编号")
    assert len((work / "uploads_design.md").read_text(encoding="utf-8")) > 50_000   # 远超旧的 3500 字上限
    assert "tbl_149 —— 业务表149" in ev["docs"]
    assert ev["doc_stats"]["anchors_covered"] == 1 and ev["n_docs"] == 1
    assert ev["files"][0]["consumed_as_text"] is True and ev["files"][0]["chunks_used"] >= 1


def test_budget_exhaustion_is_reported_separately_from_not_found():
    """「文档里没有」与「预算不够没选上」是两件事,处理办法不同,不能混报。"""
    cs = dc.chunk_text(_design_doc(80), "big.md")
    _, st = dc.select(cs, anchors=[f"tbl_{i:03d}" for i in range(80)] + ["ghost"], budget=3000)
    assert st["anchors_unbudgeted"] > 0
    assert "tbl_079" not in st["anchors_missing"]
    assert st["anchors_covered"] + st["anchors_unbudgeted"] + len(st["anchors_missing"]) == 81


def test_long_single_line_is_split_not_truncated():
    """docx 段落常不换行:超过块长的单行要切开保留,不能截断丢字。"""
    text = "# 标题\n" + "".join(f"第{i:04d}句。" for i in range(400))
    chunks = dc.chunk_text(text, "d.docx", max_chars=900, overlap=0)
    assert sum(c["text"].count("句") for c in chunks) == 400           # 不重叠时每个字恰好出现一次
    assert all(len(c["text"]) <= 900 + len("# 标题") + 1 for c in chunks)
