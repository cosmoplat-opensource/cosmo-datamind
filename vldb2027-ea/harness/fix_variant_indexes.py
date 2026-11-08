#!/usr/bin/env python3
"""Make the exemplar and dictionary indexes resolve the schema-variant databases (variants/<S>/dev_databases).

Both indexes are keyed by the realpath of the execution database. Protocol-v2 units that executed on a variant
(S=bare, and S=nofk with O=dm-* on BIRD) therefore received no exemplars and no column descriptions, while all other
units did. This adds, for every indexed database, the variant paths that exist:
  * exemplar indexes (runs/exemplars/*/index.json): nofk and bare variants (the exemplars are benchmark questions with
    gold SQL, identical across schema variants);
  * dictionary index (runs/datamind/workdir/dictionaries/index.json): nofk variant only (nofk strips declared foreign keys,
    not descriptions; bare strips the documentation by design, so it keeps no dictionary).
Originals are kept as index.json.before_variant_fix.  Idempotent.
"""
import json
import os
import shutil
from pathlib import Path

EXP = Path(__file__).resolve().parent
ORIG = os.path.realpath(EXP / "EvoOntology/benchmarks/bird/data/mini_dev_data/dev_databases")


def extend(index_file: Path, variants):
    idx = json.loads(index_file.read_text())
    backup = index_file.with_name("index.json.before_variant_fix")
    if not backup.exists():
        shutil.copy(index_file, backup)
    added = 0
    for path, name in list(idx.items()):
        if not path.startswith(ORIG + "/"):
            continue
        rel = path[len(ORIG) + 1:]
        for S in variants:
            vp = os.path.realpath(EXP / "variants" / S / "dev_databases" / rel)
            if os.path.exists(vp) and vp not in idx:
                idx[vp] = name; added += 1
    index_file.write_text(json.dumps(idx, ensure_ascii=False, indent=1))
    print(index_file.relative_to(EXP), "added", added)


for f in sorted((EXP / "runs/exemplars").glob("*/index.json")):
    extend(f, ("nofk", "bare"))
extend(EXP / "runs/datamind/workdir/dictionaries/index.json", ("nofk",))
