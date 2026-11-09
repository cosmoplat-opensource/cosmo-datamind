#!/usr/bin/env python3
"""Protocol v5 (PVLDB revision): component ablations of the EvoOntology ontologies of the merged schema (MG).

For each fold, copies runs/v2/evo/w_opaque_merged_<fold> (the store the agent used in protocol v4) to
runs/v5/onto/<variant>_<fold> and empties one component of the active version:
  evo_norel  relations.json emptied: term-to-column mappings, terms, constraints and evidence kept
  evo_nomap  mappings.json emptied: relations (with their connection conditions), terms, constraints and evidence kept
Nothing else is changed; the store's other files are copied unchanged.  usage: python3 make_v5_ablations.py
"""
import json
import shutil
from pathlib import Path

EXP = Path(__file__).resolve().parent
OUT = EXP / "runs/v5/onto"
EMPTY = {"evo_norel": "relations.json", "evo_nomap": "mappings.json"}

for fold in ("fwd", "rev"):
    src = EXP / f"runs/v2/evo/w_opaque_merged_{fold}"
    for variant, fname in EMPTY.items():
        dst = OUT / f"{variant}_{fold}"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        db = dst / "bird_merged"
        ver = json.loads((db / "active.json").read_text())["active_version"]
        f = db / "versions" / ver / fname
        n = len(json.loads(f.read_text()))
        f.write_text("[]\n")
        print(f"{variant}_{fold}: emptied {fname} ({n} records) in {ver}")
