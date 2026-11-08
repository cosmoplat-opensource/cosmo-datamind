#!/usr/bin/env python3
"""Protocol v4 (VLDB E&A paper): formal test-split runs of the ReAct agent under ontology and documentation conditions,
without evidence, on three schema settings, both folds, three repeats. Runs through queue_v2's runner.

usage: python3 queue_v4.py plan | run [--parallel 3] [--only S] | status

settings  R   readable BIRD Mini-Dev schemas (11 databases)
          O   BIRD-opaque (make_opaque.py; 11 databases)
          MG  BIRD-opaque-merged (make_opaque_merged.py; 9 databases in one 60-table schema)
conditions
          none      no ontology, no documentation
          doc       database documentation as static context
          evo       EvoOntology ontology through its browse/resolve tools
          evodoc    evo + documentation
          dm        our ontology (converted with dm2evo) through the same tools
          dmdoc     dm + documentation
          M         our ontology compiled into the context per question (with documentation) + one ontology check
Output runs/v4/qa/<setting>_<fold>/<condition>_r<rep>; scoring with score_react_dir.py (strict and column-tolerant EX).
"""
from __future__ import annotations

import argparse
import json

import queue_v2 as q2

EXP = q2.EXP
V4 = EXP / "runs" / "v4"
SR = {"fwd": "minidev", "rev": "minidev_rev"}
N = {("R", "fwd"): 251, ("R", "rev"): 249, ("O", "fwd"): 251, ("O", "rev"): 249, ("MG", "fwd"): 200, ("MG", "rev"): 200}


def setting_paths(S, fold):
    if S == "R":
        return dict(qdir=None, dbr="-", doc=EXP / "runs/datamind/workdir/dictionaries", evo=EXP / f"runs/v2/evo/w_full_{fold}",
                    dm2evo=EXP / f"runs/v2/dm2evo/w_full_{fold}", graphs=EXP / f"runs/v2/dm/w_full_{fold}/graphs.json")
    if S == "O":
        return dict(qdir=EXP / f"variants/opaque/questions/{SR[fold]}", dbr=EXP / "variants/opaque/dev_databases",
                    doc=EXP / "variants/opaque/dictionaries", evo=EXP / f"runs/v2/evo/w_opaque_{fold}",
                    dm2evo=EXP / f"runs/v2/dm2evo/w_opaque_{fold}", graphs=EXP / f"runs/v2/dm/w_opaque_{fold}/graphs.json")
    return dict(qdir=EXP / f"variants/opaque_merged/questions/{SR[fold]}", dbr=EXP / "variants/opaque_merged/dev_databases",
                doc=EXP / "variants/opaque_merged/dictionaries", evo=EXP / f"runs/v2/evo/w_opaque_merged_{fold}",
                dm2evo=EXP / f"runs/v2/dm2evo/w_opaque_merged_{fold}", graphs=EXP / f"runs/v2/dm/w_opaque_merged_{fold}/graphs.json")


def plan(_a=None):
    jobs, port = [], 8701
    for rep in (1, 2, 3):
        for S in ("MG", "O", "R"):
            for fold in ("fwd", "rev"):
                p = setting_paths(S, fold)
                for cond in ("none", "doc", "evo", "evodoc", "dm", "dmdoc", "M"):
                    out = V4 / "qa" / f"{S}_{fold}" / f"{cond}_r{rep}"
                    env = {"EVID": "0"}
                    if p["qdir"]:
                        env["QDIR"] = str(p["qdir"])
                    if cond in ("doc", "evodoc", "dmdoc"):
                        env.update(EVO_CONTEXT_MODE="doc", EVO_DOC_DIR=str(p["doc"]))
                    sem = {"evo": p["evo"], "evodoc": p["evo"], "dm": p["dm2evo"], "dmdoc": p["dm2evo"]}.get(cond, "-")
                    g = str(p["graphs"]) if cond == "M" else "-"
                    if cond == "M":
                        port += 1
                    args = [str(out), SR[fold], "test", g, str(sem), str(port if cond == "M" else 0), "1", "1", '""', str(p["dbr"])]
                    cmd = " ".join([str(EXP / "run_method.sh")] + args)
                    jobs.append({"id": f"qa4__{S}_{fold}__{cond}_r{rep}", "phase": "qa", "cmd": cmd, "env": env, "deps": [],
                                 "cwd": str(EXP), "out": str(out), "reuse_of": None,
                                 "check": {"kind": "react", "out": str(out), "n": N[(S, fold)]}})
    V4.mkdir(parents=True, exist_ok=True)
    (V4 / "plan.json").write_text(json.dumps(jobs, ensure_ascii=False, indent=1))
    print(len(jobs), "jobs ->", V4 / "plan.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["plan", "run", "status"])
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    a.phase = "qa"
    q2.PLAN_F, q2.STATE_F, q2.V2 = V4 / "plan.json", V4 / "queue_state.json", V4
    plan(a) if a.action == "plan" else {"run": q2.run, "status": q2.status}[a.action](a)


if __name__ == "__main__":
    main()
