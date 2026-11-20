#!/usr/bin/env python3
"""Section 6.1: how many columns that EvoOntology's term-to-column mappings name are still named, as column codes, in
the ablated store without mappings (terms, constraints, relations; and also counting evidence records).
Column identity: (table code, column code) from the full store's mappings; a column counts as named in the ablated store
if its column code clNNNN occurs in the JSON text of those records (column codes are unique across the merged schema).
Output: runs/v5/nomap_columns_20261120.json"""
import json
import re
from pathlib import Path

EXP = Path(__file__).resolve().parent
out = {}
for fold in ("fwd", "rev"):
    full = EXP / f"runs/v2/evo/w_opaque_merged_{fold}/bird_merged/versions/ontology_v0"
    abl = EXP / f"runs/v5/onto/evo_nomap_{fold}/bird_merged/versions/ontology_v0"
    maps = json.loads((full / "mappings.json").read_text())
    cols = set()
    for m in maps:
        cols |= set(re.findall(r"\bcl\d{4}\b", str(m.get("column") or "")))
    text = {n: (abl / f"{n}.json").read_text() for n in ("terms", "constraints", "relations", "evidence")}
    named = set(re.findall(r"\bcl\d{4}\b", text["terms"] + text["constraints"] + text["relations"]))
    named_ev = named | set(re.findall(r"\bcl\d{4}\b", text["evidence"]))
    out[fold] = {"mapped_columns": len(cols), "named_in_terms_constraints_relations": len(cols & named),
                 "named_counting_evidence": len(cols & named_ev),
                 "codes_named_in_terms_constraints_relations": len(named)}
(EXP / "runs/v5/nomap_columns_20261120.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out))
