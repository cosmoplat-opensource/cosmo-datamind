# -*- coding: utf-8 -*-
"""反幻觉评测台单测(IR-009 / DR-039)。

先验证评测台本身可用(能算出精确率/召回/幻觉泄漏率),
再用它给出裁决器在**干净子集**上的质量基线:泄漏 0、召回 1。
另单列 DR-038 探针case,证明**固定 θ 的两类失效**(供 DR-038 用)。
"""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "benchmark"))
import adversarial_fk
import hallucination_eval


def _clean(cands):
    return [c for c in cands if not c["cat"].startswith("dr038")]


def test_harness_reports_metrics(tmp_path):
    db = adversarial_fk.build(str(tmp_path / "adv.db"))
    m = hallucination_eval.evaluate(db, adversarial_fk.CANDIDATES)
    for k in ("precision", "recall", "f1", "hallucination_leak_rate", "tp", "fp", "fn", "tn"):
        assert k in m


def test_clean_subset_no_leak_full_recall(tmp_path):
    # 干净子集:裁决器应 verify 全部真 FK、拒绝全部假边
    db = adversarial_fk.build(str(tmp_path / "adv.db"))
    m = hallucination_eval.evaluate(db, _clean(adversarial_fk.CANDIDATES))
    assert m["hallucination_leak_rate"] == 0.0, f"有假边泄漏为 verified: {m}"
    assert m["recall"] == 1.0, f"有真 FK 未 verify: {m}"
    assert m["precision"] == 1.0


def test_surrogate_collision_not_verified(tmp_path):
    db = adversarial_fk.build(str(tmp_path / "adv.db"))
    m = hallucination_eval.evaluate(db, adversarial_fk.CANDIDATES)
    row = next(r for r in m["rows"] if r["cat"] == "surrogate_collision")
    assert row["verified"] is False   # 代理键碰撞:名不符 → 不得 verified


def test_reverse_direction_suppressed(tmp_path):
    db = adversarial_fk.build(str(tmp_path / "adv.db"))
    m = hallucination_eval.evaluate(db, adversarial_fk.CANDIDATES)
    row = next(r for r in m["rows"] if r["cat"] == "reverse")
    assert row["verified"] is False   # 方向反 → 抑制


def test_dr038_fixed_theta_failure_modes(tmp_path):
    # 记录固定 θ 的两类失效(DR-038 动机):高基数部分重叠真FK被漏、低基数同名巧合假边被收
    db = adversarial_fk.build(str(tmp_path / "adv.db"))
    m = hallucination_eval.evaluate(db, adversarial_fk.CANDIDATES)
    hi = next(r for r in m["rows"] if r["cat"] == "dr038_highcard_partial")
    lo = next(r for r in m["rows"] if r["cat"] == "dr038_lowcard_coincidence")
    assert hi["is_true_fk"] is True and hi["verified"] is False   # 真FK被固定θ漏(FN)
    assert lo["is_true_fk"] is False and lo["verified"] is True    # 假边被固定θ收(FP/泄漏)


def test_dr038_adaptive_theta_recovers_highcard_recall(tmp_path):
    # 自适应 θ 在基准上是纯召回增益:高基数部分重叠真 FK 被找回,泄漏率不升。
    # (但在真实 demo 上会促成同名代理键偶合的假 verified——见 DR-038,故不接入 quick_build。)
    db = adversarial_fk.build(str(tmp_path / "adv.db"))
    fixed = hallucination_eval.evaluate(db, adversarial_fk.CANDIDATES)
    adap = hallucination_eval.evaluate(db, adversarial_fk.CANDIDATES, adaptive=True)
    assert adap["recall"] > fixed["recall"]
    assert adap["hallucination_leak_rate"] <= fixed["hallucination_leak_rate"]
    hi = next(r for r in adap["rows"] if r["cat"] == "dr038_highcard_partial")
    assert hi["verified"] is True
