#!/usr/bin/env python3
"""Gzip the per-question trace files of finished runs (all_results.json complete) to save disk; scoring reads only
all_results.json, and analysis scripts read traces through gzip. usage: compress_traces.py <qa root> [...]"""
import glob, gzip, json, os, shutil, sys
saved = 0
for root in sys.argv[1:]:
    for res in glob.glob(os.path.join(root, "*", "*", "*", "*", "all_results.json")):
        run = os.path.dirname(res)
        try:
            n = len(json.load(open(res))["results"])
        except Exception:
            continue
        for f in glob.glob(os.path.join(run, "*", "traces", "*.json")):
            before = os.path.getsize(f)
            with open(f, "rb") as a, gzip.open(f + ".gz", "wb") as b:
                shutil.copyfileobj(a, b)
            os.remove(f)
            saved += before - os.path.getsize(f + ".gz")
print("saved %.0f MB" % (saved / 1e6))
