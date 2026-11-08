#!/usr/bin/env python3
"""Before scoring protocol-v4 runs: list finished runs with infrastructure failures (any API connection error, or 5+
per-question timeouts, which occur when a guard paused the run). usage: audit_infra_v4.py [--requeue]
--requeue moves such runs to runs/v4/_infra_contaminated/ and marks them pending in the queue state."""
import glob, json, os, shutil, sys
redo = []
for f in glob.glob("runs/v4/qa/*/*/*/*/all_results.json"):
    r = json.load(open(f))["results"]; run = f.split("/qa/")[1].rsplit("/", 3)[0]
    conn = sum("Connection error" in str(x.get("error")) for x in r); to = sum("Timeout" in str(x.get("error")) for x in r)
    if conn or to >= 5:
        redo.append(run); print(run, "connection errors", conn, "timeouts", to)
if "--requeue" in sys.argv and redo:
    p = "runs/v4/queue_state.json"; s = json.load(open(p)); os.makedirs("runs/v4/_infra_contaminated", exist_ok=True)
    for run in redo:
        sf, cr = run.split("/"); dst = f"runs/v4/_infra_contaminated/{sf}__{cr}"
        shutil.move(f"runs/v4/qa/{run}", dst + ("_2" if os.path.exists(dst) else "")); s[f"qa4__{sf}__{cr}"]["status"] = "pending"
    json.dump(s, open(p, "w"), ensure_ascii=False, indent=1)
print(len(redo), "contaminated runs")
sys.exit(1 if redo else 0)
