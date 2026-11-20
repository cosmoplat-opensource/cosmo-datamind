#!/usr/bin/env python3
"""File-access audit of the input-aligned EvoOntology builds (Section 3.1), with the rules of audit_evo_builds.py plus
reads of the original workloads (which carry BIRD ids, difficulty and source database) or of EvoOntology's own
construction_workload files. Glob/Grep/LS calls that only list such paths are reported separately (listing exposes file
names, not contents). Output: runs/v4/evo_build_audit_aligned_20261120.json"""
import glob
import json
import re
from pathlib import Path

import audit_evo_builds as A

EXP = Path(__file__).resolve().parent
EXTRA = re.compile(r"opaque_merged/workload/|construction_workload")
out = {}
for f in sorted(glob.glob(str(EXP / "runs/v2/build_logs/evo-w__opaque_merged_*_aligned/*.jsonl"))):
    calls, hits = A.audit(f)
    reads, lists = [], []
    for line in open(f, errors="replace"):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") != "assistant":
            continue
        for c in (ev.get("message") or {}).get("content") or []:
            if isinstance(c, dict) and c.get("type") == "tool_use":
                s = json.dumps(c.get("input"), ensure_ascii=False)
                if EXTRA.search(s):
                    (lists if c.get("name") in ("Glob", "Grep", "LS") else reads).append({"tool": c.get("name"), "input": s[:300]})
    out[str(Path(f).relative_to(EXP))] = {"tool_calls": calls, "flagged_by_v4_rules": hits,
                                           "reads_of_original_workloads": reads, "listings_matching_workload_names": lists}
(EXP / "runs/v4/evo_build_audit_aligned_20261120.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
for k, v in out.items():
    print(k, v["tool_calls"], len(v["flagged_by_v4_rules"]), len(v["reads_of_original_workloads"]), len(v["listings_matching_workload_names"]))
    for x in v["flagged_by_v4_rules"] + v["reads_of_original_workloads"] + v["listings_matching_workload_names"]:
        print("   ", x.get("tool"), (x.get("excerpt") or x.get("input"))[:200])
