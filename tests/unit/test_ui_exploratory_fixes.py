# -*- coding: utf-8 -*-
"""界面上的几处缺陷(DR-112):评审弹窗切页后还挡着;没有可撤销的操作时撤销按钮仍可点;
评审页一上来是上百个红色删除按钮;窄屏下菜单占满首屏;不理会「减少动态效果」。源码级检查,浏览器行为由 test_ui 覆盖。"""
import re
from pathlib import Path

import server

ROOT = Path(__file__).resolve().parents[2]
UI = (ROOT / "ui/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "ui/styles/responsive.css").read_text(encoding="utf-8")


def _fn(name):
    i = UI.index("function " + name + "(")
    return UI[i:i + 900]


def test_switching_pages_closes_open_dialogs():
    assert "closeDialogs()" in _fn("goPage")
    body = _fn("closeDialogs")
    assert ".dq-modal" in body and "display='none'" in body and "RVM=null" in body


def test_review_reports_how_many_edits_can_be_undone(tmp_path, monkeypatch):
    c = server.app.test_client()
    d = c.get("/api/ont/review?graph=demo").get_json()
    assert isinstance(d["undoable"], int)
    assert 'id="rv_undo"' in UI and re.search(r"rv_undo'\)\.disabled=!\(?d\.undoable", UI)


def test_object_table_is_folded_unless_something_needs_confirming():
    assert "<details" in _fn("rvLoad") or "<details" in UI[UI.index("const ob=$('#rv_objs')"):UI.index("function rvSemanticHTML")]
    seg = UI[UI.index("const ob=$('#rv_objs')"):UI.index("function rvSemanticHTML")]
    assert "obj_candidate?' open'" in seg.replace(" ", "").replace("d.counts.", "") or "obj_candidate?'open'" in seg.replace(" ", "").replace("d.counts.", "")
    assert "color:#b91c1c" not in seg                      # 删除不再是页面上最显眼的操作


def test_narrow_screens_fold_the_menu_behind_a_toggle():
    assert 'class="pill nav-toggle"' in UI and "aside.classList.toggle('open')" in UI.replace('"', "'").replace("document.querySelector('aside')", "aside")
    narrow = CSS[CSS.index("@media (max-width:820px)"):]
    assert "aside:not(.open) .nav" in narrow and ".nav-toggle{display:inline-flex" in narrow.replace(" ", "")
    assert re.search(r"\.nav-toggle\{display:none", CSS.replace(" ", ""))


def test_reduced_motion_preference_is_honoured():
    assert "prefers-reduced-motion:reduce" in CSS.replace(" ", "")


def test_upload_feedback_mentions_files_that_were_only_archived():
    seg = UI[UI.index("toast('上传中…')"):UI.index("async function bcSkillUpload")]
    assert "d.notes" in seg
