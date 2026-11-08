#!/usr/bin/env python3
"""Protocol v3 QA jobs (VLDB paper): reruns with the variant-index fix, information-matched consumer runs and
ontology-component ablations. Every job is a copy of the corresponding protocol-v2 Q=dm job (same code, split,
evidence, style, candidates, exemplar directory) with only the listed changes; it runs through queue_v2's runner.

usage:
  python3 queue_v3.py plan
  python3 queue_v3.py run [--parallel 3] [--only SUBSTRING] [--retry-failed]
  python3 queue_v3.py status

Conditions (unit names runs/v3/qa/<dir>/<O>__dm__<S>__<cond>_r<rep>):
  fix          v2 units that executed on a schema variant and therefore lost exemplars and dictionary
               (BIRD nofk with dm-d/dm-w; industrial bare with every O); rerun after fix_variant_indexes.py
  noex         DATAMIND_EXEMPLAR_K=0 (no benchmark exemplars)                 O = dm-w
  nodict       DATAMIND_ABLATE=dict (no external or in-database column documentation)   O = dm-w
  noexdict     both, the information available to the ReAct agent            O = none, evo-w, dm-w
  norel/nosem/nolex/nomet   ontology copies without relations / concepts+caveats / Chinese names / metrics+catalogs
  gaterel      DATAMIND_ABLATE=gate_rel (table scope check only)
  intent       DATAMIND_ABLATE=intent (no intent cross-check or repair)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import queue_v2 as q2

EXP = q2.EXP
V3 = EXP / "runs" / "v3"
ABL = V3 / "abl"
PORT0 = 8301


def plan(_args=None):
    v2 = {j["id"]: j for j in json.loads((EXP / "runs" / "v2" / "plan.json").read_text())}
    jobs, port = [], PORT0

    def add(base_id, cond, env_over):
        nonlocal port
        b = v2[base_id]
        _, d, O, Q, tail = base_id.split("__")
        S, rep = tail.rsplit("_r", 1)
        out = V3 / "qa" / d / f"{O}__dm__{S}__{cond}_r{rep}"
        env = {**b["env"], **env_over, "DM_QA_OUT": str(out), "DM_PORT": str(port)}
        port += 1
        check = {**b["check"], "out": str(out)}
        jobs.append({"id": f"qa3__{d}__{O}__dm__{S}__{cond}_r{rep}", "phase": "qa", "cmd": b["cmd"], "env": env,
                     "deps": [], "cwd": b.get("cwd", str(EXP)), "out": str(out), "check": check, "reuse_of": None, "base": base_id, "cond": cond})

    reps = (1, 2, 3)
    # 1. reruns of variant-executed units (exemplar and dictionary index fix)
    for d in ("fwd", "rev"):
        for O in ("dm-d", "dm-w"):
            for r in reps:
                add(f"qa__{d}__{O}__dm__nofk_r{r}", "fix", {})
    for d in ("ind_fwd", "ind_rev"):
        for O in q2.ALL_O:
            for r in reps:
                add(f"qa__{d}__{O}__dm__bare_r{r}", "fix", {})
    # 2. information-matched consumer runs
    for d in ("fwd", "rev", "ind_fwd", "ind_rev"):
        for r in reps:
            for O in ("none", "evo-w", "dm-w"):
                add(f"qa__{d}__{O}__dm__full_r{r}", "noexdict", {"DATAMIND_EXEMPLAR_K": "0", "DATAMIND_ABLATE": "dict"})
            add(f"qa__{d}__dm-w__dm__full_r{r}", "noex", {"DATAMIND_EXEMPLAR_K": "0"})
            add(f"qa__{d}__dm-w__dm__full_r{r}", "nodict", {"DATAMIND_ABLATE": "dict"})
    # 3. ontology-component ablations of dm-w
    src = {"fwd": "w_full_fwd", "rev": "w_full_rev", "ind_fwd": "w_full_ind_fwd", "ind_rev": "w_full_ind_rev"}
    for d in ("fwd", "rev", "ind_fwd", "ind_rev"):
        kinds = ["norel", "nosem", "nolex"] + (["nomet"] if d.startswith("ind") else [])
        for r in reps:
            for k in kinds:
                add(f"qa__{d}__dm-w__dm__full_r{r}", k, {"DM_GRAPHS": str(ABL / f"{k}__{src[d]}" / "graphs.json")})
            add(f"qa__{d}__dm-w__dm__full_r{r}", "gaterel", {"DATAMIND_ABLATE": "gate_rel"})
            add(f"qa__{d}__dm-w__dm__full_r{r}", "intent", {"DATAMIND_ABLATE": "intent"})
    V3.mkdir(parents=True, exist_ok=True)
    (V3 / "plan.json").write_text(json.dumps(jobs, ensure_ascii=False, indent=1))
    print(len(jobs), "jobs ->", V3 / "plan.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["plan", "run", "status"])
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    a.phase = "qa"
    q2.PLAN_F, q2.STATE_F = V3 / "plan.json", V3 / "queue_state.json"
    q2.V2 = V3
    if a.action == "plan":
        plan(a)
    else:
        {"run": q2.run, "status": q2.status}[a.action](a)


if __name__ == "__main__":
    main()
