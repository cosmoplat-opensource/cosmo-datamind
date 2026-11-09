#!/usr/bin/env python3
"""Protocol v5 (PVLDB revision): additional test-split runs of the ReAct agent, same agent, model, splits, scoring and
three repeats as protocol v4 (queue_v4.py), through queue_v2's runner.

usage: python3 queue_v5.py plan | run [--parallel 4] [--only S] | status

A  component ablation of EvoOntology on BIRD-opaque-merged (MG), stores built by make_v5_ablations.py:
     evo_norel  relations removed (term-to-column mappings kept)
     evo_nomap  term-to-column mappings removed (relations kept)
B  turn budget on MG: none_t60, evo_t60 (max_turns 60 instead of 30; configs deepseek_*_t60.yaml)
C  BIRD-readable-merged (MGR; make_readable_merged.py): none, doc, evo, evodoc, dm, dmdoc; the ontology conditions are
   planned once their stores exist (build_evo_v2.py / build_dm_v2.py + dm2evo.py with --S readable_merged)
Output runs/v5/qa/<setting>_<fold>/<condition>_r<rep>; scoring with score_react_dir.py (strict and column-tolerant EX).
"""
from __future__ import annotations

import argparse
import json

import queue_v2 as q2
from queue_v4 import N, SR, setting_paths

EXP = q2.EXP
V5 = EXP / "runs" / "v5"


def mgr_paths(fold):
    v = EXP / "variants/readable_merged"
    return dict(qdir=v / f"questions/{SR[fold]}", dbr=v / "dev_databases", doc=v / "dictionaries",
                evo=EXP / f"runs/v2/evo/w_readable_merged_{fold}", dm2evo=EXP / f"runs/v2/dm2evo/w_readable_merged_{fold}")


def job(S, fold, cond, rep, sem, env_extra=None):
    p = mgr_paths(fold) if S == "MGR" else setting_paths("MG", fold)
    out = V5 / "qa" / f"{S}_{fold}" / f"{cond}_r{rep}"
    env = {"EVID": "0", "QDIR": str(p["qdir"])}
    if cond.startswith(("doc", "evodoc", "dmdoc")):
        env.update(EVO_CONTEXT_MODE="doc", EVO_DOC_DIR=str(p["doc"]))
    env.update(env_extra or {})
    args = [str(out), SR[fold], "test", "-", str(sem), "0", "1", "1", '""', str(p["dbr"])]
    return {"id": f"qa5__{S}_{fold}__{cond}_r{rep}", "phase": "qa", "cmd": " ".join([str(EXP / "run_method.sh")] + args),
            "env": env, "deps": [], "cwd": str(EXP), "out": str(out), "reuse_of": None,
            "check": {"kind": "react", "out": str(out), "n": N[("MG", fold)]}}


def plan(_a=None):
    jobs = []
    for rep in (1, 2, 3):
        for fold in ("fwd", "rev"):
            mg = setting_paths("MG", fold)
            for v in ("evo_norel", "evo_nomap"):
                jobs.append(job("MG", fold, v, rep, V5 / "onto" / f"{v}_{fold}"))
            jobs.append(job("MG", fold, "none_t60", rep, "-", {"EVO_CFG_SUFFIX": "_t60"}))
            jobs.append(job("MG", fold, "evo_t60", rep, mg["evo"], {"EVO_CFG_SUFFIX": "_t60"}))
            m = mgr_paths(fold)
            jobs.append(job("MGR", fold, "none", rep, "-"))
            jobs.append(job("MGR", fold, "doc", rep, "-"))
            for cond, key in (("evo", "evo"), ("evodoc", "evo"), ("dm", "dm2evo"), ("dmdoc", "dm2evo")):
                if (m[key] / "bird_merged" / "active.json").exists():
                    jobs.append(job("MGR", fold, cond, rep, m[key]))
    V5.mkdir(parents=True, exist_ok=True)
    (V5 / "plan.json").write_text(json.dumps(jobs, ensure_ascii=False, indent=1))
    print(len(jobs), "jobs ->", V5 / "plan.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["plan", "run", "status"])
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    a.phase = "qa"
    q2.PLAN_F, q2.STATE_F, q2.V2 = V5 / "plan.json", V5 / "queue_state.json", V5
    plan(a) if a.action == "plan" else {"run": q2.run, "status": q2.status}[a.action](a)


if __name__ == "__main__":
    main()
