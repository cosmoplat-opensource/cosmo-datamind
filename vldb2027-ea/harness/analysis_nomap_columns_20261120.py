#!/usr/bin/env python3
"""Section 6.1: how many column codes that EvoOntology's term-to-column mappings name are still named in the ablated
store without mappings (terms, constraints, relations; and also counting evidence records). The unit is the column code
clNNNN: within one source database a shared key name keeps one code in several tables, so a code is not always one
column, and a code named in a term or relation may refer to another table's column with the same code.
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
    out[fold] = {"mapped_column_codes": len(cols), "named_in_terms_constraints_relations": len(cols & named),
                 "named_counting_evidence": len(cols & named_ev),
                 "codes_named_in_terms_constraints_relations": len(named)}
(EXP / "runs/v5/nomap_columns_20261120.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out))
