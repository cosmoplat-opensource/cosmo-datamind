#!/usr/bin/env python3
"""Paper Table 4: contents of the EvoOntology and DataMind ontologies for the merged schema, both folds.
Terms, terms whose name contains an opaque code (tbNNN / clNNNN), column-level mappings, those attached to a term with a
business name, and relations (DataMind: total and verified, from the IR; EvoOntology: count).
DataMind ontologies are counted after conversion to EvoOntology's record format (dm2evo.py, DM2EVO_FULL=1), the form
the agent consumed. Writes runs/v4/analysis_table4.json."""
import json
import re
from pathlib import Path

EXP = Path(__file__).resolve().parent
OPAQUE = re.compile(r"(cl\d{4}|tb\d{3})", re.I)


def counts(version_dir):
    terms = {t["id"]: t for t in json.loads((version_dir / "terms.json").read_text())}
    maps = json.loads((version_dir / "mappings.json").read_text())
    col = [m for m in maps if m.get("column")]
    return {"terms": len(terms), "terms_opaque_name": sum(1 for t in terms.values() if OPAQUE.search(t["name"])),
            "column_mappings": len(col),
            "column_mappings_business_term": sum(1 for m in col if m["term_id"] in terms and not OPAQUE.search(terms[m["term_id"]]["name"])),
            "relations": len(json.loads((version_dir / "relations.json").read_text()))}


def main():
    out = {}
    for fold in ("fwd", "rev"):
        evo = counts(EXP / f"runs/v2/evo/w_opaque_merged_{fold}/bird_merged/versions/ontology_v0")
        dm = counts(EXP / f"runs/v2/dm2evo/w_opaque_merged_{fold}/bird_merged/versions/ontology_v0")
        ir = json.loads((EXP / f"runs/v2/dm/w_opaque_merged_{fold}/ir/bird_merged.json").read_text())
        rel = [r for r in ir.get("relations") or [] if r.get("source_concept") and r.get("target_concept")
               and not str(r.get("source_concept")).startswith("action__")]
        dm["ir_relations"] = len(rel)
        dm["ir_relations_verified"] = sum(1 for r in rel if r.get("status") == "verified")
        out[fold] = {"evoontology": evo, "datamind": dm}
    (EXP / "runs/v4/analysis_table4.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
