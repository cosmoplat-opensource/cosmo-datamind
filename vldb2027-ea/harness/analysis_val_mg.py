#!/usr/bin/env python3
"""Validation-set counts of Section 6.1-6.2 of the PVLDB draft (merged schema, fold A->B, 61 validation questions, one
run each): column-tolerant correct answers per development run, from each run's rescore.json.
usage: python3 analysis_val_mg.py  -> runs/v3/dev/val_mg_summary.json"""
import json
from pathlib import Path

DEV = Path(__file__).resolve().parent / "runs/v3/dev"
out = {}
for d in sorted(DEV.glob("mg_*_fwd_val")):
    f = d / "rescore.json"
    if not f.exists():
        continue
    r = json.loads(f.read_text())
    out[d.name] = {"questions": len(r), "tolerant_correct": round(sum(v[1] for v in r.values())),
                   "strict_correct": round(sum(v[0] for v in r.values()))}
# value semantics of the DR-094 build (Section 6.2) and the validation traces in which they reached the agent
IR94 = DEV.parent.parent / "v2/dm/w_opaque_merged_fwd_dr094/ir/bird_merged.json"
out["dr094_value_semantics"] = len(json.loads(IR94.read_text()).get("value_semantics") or [])
tr = sorted((DEV / "mg_reactdm94_fwd_val").glob("*/*/bird_merged/traces/*.json"))
out["dr094_traces_with_value_semantics"] = {"traces": len(tr), "with_meanings": sum(1 for t in tr if "stores codes" in t.read_text())}
(DEV / "val_mg_summary.json").write_text(json.dumps(out, indent=1))
for k, v in out.items():
    print(k, v)
