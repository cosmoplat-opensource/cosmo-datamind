"""v30 packs with the v31 (LLM-assisted) field prior in place of the v29 field prior.

The module globals of every loader in the chain are rebound here explicitly; v30's
loader cannot be reused because it rebinds the field prior to v29 inside its call."""

import native_v26_data
import native_v27_data
import native_v28_data
import native_v29_data
from native_structural_prior_v30 import PRIOR27, PRIOR28
from native_structural_prior_v31 import DEST as FIELD31
from native_v30_bank import DEST as BANK
from native_v30_bank import PERCEPTION


def load_data():
    native_v26_data.DEST, native_v26_data.ALIGN = BANK, PERCEPTION
    native_v27_data.STRUCTURAL, native_v27_data.BANK = PRIOR27, BANK
    native_v28_data.PAIR = PRIOR28
    native_v29_data.FIELD = FIELD31
    tasks, inventory, packs, prefixes, provenance = native_v29_data.load_data()
    if provenance.get("field_prior_sha256") != __import__("json").loads((FIELD31 / "manifest.json").read_text())["field_prior_sha256"]:
        raise ValueError("v31 field prior was not the one loaded")
    provenance = dict(provenance, bank="common_candidate_v30+priors_v27_v28+field_prior_v31_llm", perception="perception_v30", field_prior="prior_v31")
    return tasks, inventory, packs, prefixes, provenance
