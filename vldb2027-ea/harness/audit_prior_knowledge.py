#!/usr/bin/env python3
"""Value meanings in an ontology that the build inputs do not support.

For each enum_semantics / business_rule constraint, pairs "VALUE (meaning)" or "VALUE = meaning" / "VALUE means meaning"
are extracted. A meaning is supported if most of its content-word bigrams occur in the construction workload text (questions +
evidence) or among the database's stored text values. Unsupported meanings are then looked up in the BIRD column
documentation, which the builders did not read (see audit_evo_builds.py): a match there points to the builder model's
prior knowledge of the public benchmark.
usage: python3 audit_prior_knowledge.py <ontology version dir> <workload.json> <db key> <sqlite> <documentation json>
"""
import json, re, sqlite3, sys
from pathlib import Path

STOP = set("the a an of and or for with no not is are to in on by at from that this which when its it be has have".split())


def words(s):
    return [w for w in re.findall(r"[a-z]{4,}", s.lower()) if w not in STOP]


def bigrams(s):
    w = words(s)
    return [" ".join(w[i:i + 2]) for i in range(len(w) - 1)] or w


def norm(s):
    return " ".join(words(s))


def pairs(desc):
    out = []
    for m in re.finditer(r"'?([A-Za-z0-9+\-]{1,12})'?\s*\(([^()]{3,80})\)", desc):
        out.append((m.group(1), m.group(2)))
    for m in re.finditer(r"'([^']{1,20})'\s*(?:=|means|stands for|refers to)\s*([^;,.]{3,80})", desc):
        out.append((m.group(1), m.group(2)))
    return out


def main():
    vdir, wl_path, key, db, doc = sys.argv[1:6]
    wl = norm(" ".join(json.load(open(wl_path))[key]))
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    vals = set()
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        for r in con.execute(f'PRAGMA table_info("{t}")'):
            try:
                for (v,) in con.execute(f'SELECT DISTINCT "{r[1]}" FROM "{t}" WHERE typeof("{r[1]}")=\'text\' LIMIT 3000'):
                    vals.add(norm(v))
            except sqlite3.Error:
                pass
    doct = norm(Path(doc).read_text())
    C = json.load(open(Path(vdir) / "constraints.json"))
    rows = []
    for c in C:
        if c.get("constraint_type") not in ("enum_semantics", "business_rule"):
            continue
        for v, meaning in pairs(c.get("description") or ""):
            w = bigrams(meaning)
            if not w:
                continue
            sup = sum(1 for x in w if x in wl or any(x in v for v in vals if len(v) < 200)) / len(w)
            ind = sum(1 for x in w if x in doct) / len(w)
            rows.append({"constraint": c["id"], "value": v, "meaning": meaning.strip(), "supported": sup >= 0.5, "in_doc": ind >= 0.5})
    uns = [r for r in rows if not r["supported"]]
    print(json.dumps({"pairs": len(rows), "unsupported": len(uns), "unsupported_in_bird_doc": sum(r["in_doc"] for r in uns),
                      "examples": [f"{r['value']} = {r['meaning']}" for r in uns if r["in_doc"]][:12]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
