#!/usr/bin/env python3
"""Ontology-element ablations: copy a DataMind graph set with one class of ontology content removed, then install it.

usage: python3 make_ablation_graphs.py <ablation> <source graphs dir> [--out runs/v3/abl]

ablations
  norel   relations, rejected/deferred relations (role keys live in relation evidence and go with them)
  nosem   concepts, caveats and derived concepts
  nolex   object Chinese names and aliases, attribute Chinese labels (cn := table / column name)
  nomet   metric layers and value catalogs
The source IRs are read from the DataMind workdir (WORK/built_<key>.json named in graphs.json); the ablated IRs are written
to <out>/<ablation>__<source dir name>/ir/<db>.json with a graphs.json, then installed with install_ir.py, which rewrites
the graph keys. Execution sources are rebound per database by bird_datamind.py as for every other graph.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

EXP = Path(__file__).resolve().parent
WORK = EXP / "runs" / "datamind" / "workdir"


def ablate(ir: dict, kind: str) -> dict:
    ir = json.loads(json.dumps(ir))
    if kind == "norel":
        for k in ("relations", "rejected_relations", "deferred_relations"):
            if k in ir:
                ir[k] = []
    elif kind == "nosem":
        for k in ("concepts", "caveats", "derived", "concept_rejected"):
            if k in ir:
                ir[k] = []
    elif kind == "nolex":
        for o in ir.get("objects", []):
            if o.get("table"):
                o["cn"] = o["table"]
            o["aliases"] = []
            for a in o.get("attrs") or []:
                a["cn"] = ""
    elif kind == "nomet":
        ir["metric_layers"] = {}
        for o in ir.get("objects", []):
            o.pop("catalog", None)
    else:
        raise SystemExit("unknown ablation " + kind)
    ir.setdefault("build_manifest", {})["ablation"] = kind
    return ir


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ablation")
    ap.add_argument("source")
    ap.add_argument("--out", default=str(EXP / "runs" / "v3" / "abl"))
    a = ap.parse_args()
    src = Path(a.source)
    dest = Path(a.out) / f"{a.ablation}__{src.name}"
    (dest / "ir").mkdir(parents=True, exist_ok=True)
    graphs = json.loads((src / "graphs.json").read_text())
    out = {}
    for db, row in graphs.items():
        ir = json.loads((WORK / f"{row['graph']}.json").read_text())
        (dest / "ir" / f"{db}.json").write_text(json.dumps(ablate(ir, a.ablation), ensure_ascii=False))
        out[db] = {**row, "ir": f"ir/{db}.json", "ablation_of": row["graph"]}
    (dest / "graphs.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    subprocess.run([sys.executable, str(EXP / "install_ir.py"), str(dest)], check=True)
    print("installed", dest)


if __name__ == "__main__":
    main()
