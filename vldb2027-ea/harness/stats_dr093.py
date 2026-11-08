#!/usr/bin/env python3
"""Post-hoc fix (DR-093) on MG test: dm93 / M93 against the v4 conditions, same pooling and bootstrap as stats_v4."""
import json
import stats_v4 as S
S.CONDS = ["none", "doc", "evo", "dm", "dm93", "M", "M93"]
S.NAMES.update(dm93="+ DataMind ontology, DR-093 builder", M93="M, DR-093 builder")
S.PAIRS = [("dm93", "dm"), ("dm93", "none"), ("dm93", "evo"), ("M93", "M"), ("M93", "doc"), ("evo", "none")]
r = S.summarize("MG", False)
S.V4.joinpath("stats_dr093.json").write_text(json.dumps(r, indent=1))
for c in S.CONDS:
    x = r["conditions"][c]; print(f"{c:6s} tolerant {x['tolerant']} {x['tolerant_ci']} strict {x['strict']} tool {x['tool_use']}")
for k, v in r["pairs"].items():
    print(k, v["diff"], v["ci"])
