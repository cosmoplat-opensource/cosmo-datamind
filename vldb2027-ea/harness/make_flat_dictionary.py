#!/usr/bin/env python3
"""Flat term dictionary control (2026-11-20, reviewer request) for the merged schema (MG).

From the EvoOntology store the agent used in protocol v4 (runs/v2/evo/w_opaque_merged_<fold>), keep only
"business term (name, aliases) -> table.column": term definitions and scopes blanked, mapping semantics (filter,
aggregation, grain, validation) blanked, relations, constraints and evidence emptied; the term type is kept. Field names
are kept so that the agent's tools and their return-length budget are unchanged; the browse ranking also scores definition
text, so the order of returned terms can differ from the full store. The copied reports/ and workload/ folders are not
read by the agent's tools. Output: runs/v5/onto/evo_flat_<fold>.
usage: python3 make_flat_dictionary.py
"""
import json
import shutil
from pathlib import Path

EXP = Path(__file__).resolve().parent
OUT = EXP / "runs/v5/onto"
for fold in ("fwd", "rev"):
    src, dst = EXP / f"runs/v2/evo/w_opaque_merged_{fold}", OUT / f"evo_flat_{fold}"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    db = dst / "bird_merged"
    ver = db / "versions" / json.loads((db / "active.json").read_text())["active_version"]
    terms = json.loads((ver / "terms.json").read_text())
    for t in terms:
        for k in ("definition", "scope", "evidence"):
            if k in t:
                t[k] = ""
    maps = json.loads((ver / "mappings.json").read_text())
    for m in maps:
        for k in list(m):
            if k not in ("id", "term_id", "database_source", "table", "column"):
                m[k] = ""
    (ver / "terms.json").write_text(json.dumps(terms, ensure_ascii=False, indent=1))
    (ver / "mappings.json").write_text(json.dumps(maps, ensure_ascii=False, indent=1))
    counts = {}
    for f in ("relations", "constraints", "evidence"):
        counts[f] = len(json.loads((ver / f"{f}.json").read_text()))
        (ver / f"{f}.json").write_text("[]\n")
    print(f"evo_flat_{fold}: {len(terms)} terms, {len(maps)} mappings kept; emptied {counts}")
