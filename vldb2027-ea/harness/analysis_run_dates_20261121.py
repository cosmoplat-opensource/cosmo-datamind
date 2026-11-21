#!/usr/bin/env python3
"""Run dates by experiment family (Section 3.1, model alias). The logs record only the model alias "deepseek-flash", so
the dates on which each family of runs was produced are taken from the modification times of its output files.
Families: the first three path components below runs/ (e.g. runs/v4/qa/MG_fwd). Directories whose name starts with
"_" (aborted or invalid runs) are skipped. For each family: number of files, first and last date (local time), and the
number of files written before 2026-10-17.
Output: runs/v5/run_dates_20261121.json"""
import datetime
import json
import os
from pathlib import Path

EXP = Path(__file__).resolve().parent
CUT = datetime.datetime(2026, 10, 17).timestamp()
fam = {}
for root in ("runs/v2", "runs/v3", "runs/v4", "runs/v5"):
    for dp, dn, fn in os.walk(EXP / root):
        dn[:] = [d for d in dn if not d.startswith("_")]
        rel = Path(dp).relative_to(EXP).parts
        if len(rel) < 4 or rel[2] not in ("qa", "dev", "evo", "dm", "dm2evo", "build_logs", "onto"):
            continue
        for f in fn:
            if not f.endswith((".json", ".jsonl")) or "rescore" in f or "stats" in f:
                continue
            t = os.path.getmtime(os.path.join(dp, f))
            fam.setdefault("/".join(rel[:4]), []).append(t)
day = lambda t: datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d")          # noqa: E731
out = {k: {"files": len(v), "first": day(min(v)), "last": day(max(v)), "before_2026_10_17": sum(t < CUT for t in v)}
       for k, v in sorted(fam.items())}
(EXP / "runs/v5/run_dates_20261121.json").write_text(json.dumps(out, indent=1))
for k, v in out.items():
    print(f"{k:50s} {v['first']}..{v['last']}  files={v['files']}  before 10-17={v['before_2026_10_17']}")
