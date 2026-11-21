#!/usr/bin/env python3
"""File-access audit of the two input-aligned EvoOntology builds (Section 3.1).
Flags, for every tool call of a builder session:
  - the rules of audit_evo_builds.py (mapping, rewritten questions, dictionaries, split directories, gold SQL);
  - any path under runs/ other than the session's own workspace runs/v2/evo/w_opaque_merged_<fold>_aligned (that rule
    in audit_evo_builds.py lets through every runs/v2/evo/w_opaque* workspace);
  - any occurrence of the word "workload" (other than workload_aligned) or of construction_workload, with its context,
    so that a read of the original workloads (variants/opaque_merged/workload/, which carry BIRD ids, difficulty and
    source database) cannot pass unnoticed; most matches are prose and must be checked by hand;
  - Glob/Grep/LS calls, and Bash calls using ls or find, that contain "workload" other than workload_aligned, reported
    with their result, since a listing can expose file names.
Checked by hand on 2026-11-21: every match is prose, the plugin's workload-experience.md, or a read of workload_aligned;
no call reads or lists the original workloads.
Only the final log of each session is read (bird_merged.jsonl; the .attempt1 logs are earlier copies).
Output: runs/v4/evo_build_audit_aligned_20261120.json"""
import json
import re
from pathlib import Path

import audit_evo_builds as A

EXP = Path(__file__).resolve().parent
ORIG = re.compile(r"\bworkload\b(?!_aligned)|construction_workload")
out = {}
for fold in ("fwd", "rev"):
    log = EXP / f"runs/v2/build_logs/evo-w__opaque_merged_{fold}_aligned/bird_merged.jsonl"
    own = f"runs/v2/evo/w_opaque_merged_{fold}_aligned/"
    calls, hits = A.audit(log)
    other_runs, orig, listings, pending = [], [], [], {}
    for line in open(log, errors="replace"):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        m = ev.get("message")
        if not isinstance(m, dict) or not isinstance(m.get("content"), list):
            continue
        for c in m["content"]:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use":
                s = json.dumps(c.get("input"), ensure_ascii=False)
                for p in re.findall(r"runs/[^\s\"']*", s):
                    if not (p + "/").startswith(own):
                        other_runs.append({"tool": c.get("name"), "path": p[:200]})
                if ORIG.search(s):
                    ctx = [s[max(0, m.start() - 60):m.end() + 40] for m in ORIG.finditer(s)][:5]
                    orig.append({"tool": c.get("name"), "contexts": ctx})
                if (c.get("name") in ("Glob", "Grep", "LS") or (c.get("name") == "Bash" and re.search(r"\b(ls|find)\b", s))) and re.search(r"workload(?!_aligned)", s, re.I):
                    pending[c.get("id")] = {"tool": c.get("name"), "input": s[:300]}
            elif c.get("type") == "tool_result" and c.get("tool_use_id") in pending:
                x = pending.pop(c["tool_use_id"])
                x["result"] = json.dumps(c.get("content"), ensure_ascii=False)[:1500]
                listings.append(x)
    out[fold] = {"log": str(log.relative_to(EXP)), "tool_calls": calls, "flagged_by_v4_rules": hits,
                 "paths_under_runs_outside_own_workspace": other_runs, "original_workload_mentions": orig,
                 "listings_with_workload_pattern": listings + list(pending.values())}
(EXP / "runs/v4/evo_build_audit_aligned_20261120.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
for f, v in out.items():
    print(f, v["tool_calls"], len(v["flagged_by_v4_rules"]), len(v["paths_under_runs_outside_own_workspace"]),
          len(v["original_workload_mentions"]), len(v["listings_with_workload_pattern"]))
    for x in v["paths_under_runs_outside_own_workspace"][:5] + v["listings_with_workload_pattern"]:
        print("   ", x)
