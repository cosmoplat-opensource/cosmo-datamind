# -*- coding: utf-8 -*-
"""深度问数实时会话与历史回放必须显式分离。"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UI = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
HISTORY = (ROOT / "ui" / "modules" / "qa-history.js").read_text(encoding="utf-8")


def test_history_module_loads_before_main_chat_script():
    assert '/assets/modules/qa-history.js?v=1' in UI
    assert UI.index('/assets/modules/qa-history.js?v=1') < UI.index("<script>const $=")


def test_live_conversation_is_not_presented_as_history():
    assert "DQ_VIEW_MODE === 'live'" in HISTORY
    assert "c.id === DQ_CONV.id" in HISTORY
    assert "本次实时分析" in UI
    assert "本次新分析 · 引擎运行中" in UI


def test_followup_from_history_branches_without_overwriting_snapshot():
    assert "const fromHistory = window.DQ_VIEW_MODE === 'history'" in HISTORY
    assert "DQ_CONV = null" in HISTORY
    assert "inherited = fromHistory ? CHAT_HIST.slice(-2)" in HISTORY
    assert "dqRenderCard(card, t.q, t.d, 'history')" in HISTORY
    assert "原记录保持不变" in UI


def test_saved_conversations_get_time_and_history_label():
    assert "conv.ts = Date.now()" in HISTORY
    assert "dqHistoryTime(c.ts)" in HISTORY
    assert "历史回放" in UI
