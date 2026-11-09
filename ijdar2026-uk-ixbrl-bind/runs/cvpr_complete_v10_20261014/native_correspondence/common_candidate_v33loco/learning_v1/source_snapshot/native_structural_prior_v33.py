"""v33 priors: the v31 field prior and the v32 structural prior refitted with FEW-SHOT language-model readings.

The zero-shot field_rank pass that fed v31/v32 gave the model strictly less information than the system has at
budget 10 (no company history, no convention statement, no report metadata).  llm_fewshot_rank_v1 repeats that
pass with the same ten history cells the system sees (the target itself excluded), so the indicator features
llm_rank1/2/3/absent (field) and llm_period_choice / llm_dims_choice (structural) now come from a reader with
matched information.  Weights are refitted on the 2023 development teachers exactly as in v31/v32; the feature
sets, fitting code and scoring code are the v31/v32 modules themselves, only their input folder differs.
"""

import json
from pathlib import Path

import native_structural_prior_v31 as p31
import native_structural_prior_v32 as p32
from native_v30_bank import DEST as BANK
from prepare import save, sha

RANKS = BANK / "llm_fewshot_rank_v1/answers"
FIELD33 = BANK / "prior_v33_field"
PRIOR33 = BANK / "prior_v33_structural"
LLM_NOTE = "deepseek-flash FEW-SHOT reading (ten same-company 2023 cells with filed tuples, conventions, report metadata; target excluded), vision input, same public lists"


def _annotate(folder):
    m = json.loads((folder / "manifest.json").read_text())
    m["llm"] = LLM_NOTE; m["llm_answers_dir"] = str(RANKS); m["llm_answers_design_sha256"] = sha(RANKS.parent / "design.json"); m["wrapper_source_sha256"] = sha(Path(__file__))
    save(folder / "manifest.json", m)


def main():
    answered = len(list(RANKS.glob("*.json")))
    if answered != 840:
        raise RuntimeError("few-shot rank answers incomplete: %d / 840" % answered)
    p31.RANKS, p31.DEST = RANKS, FIELD33
    p31.main(); _annotate(FIELD33)
    p32.RANKS, p32.DEST = RANKS, PRIOR33
    p32.main(); _annotate(PRIOR33)
    print("v33 priors frozen:", FIELD33.name, PRIOR33.name, flush=True)


if __name__ == "__main__":
    main()
