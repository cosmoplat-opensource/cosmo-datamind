"""v30 ablation loaders: the v30 bank with a chosen subset of the refitted priors.

VARIANT is fixed per generated script: "none" (bank only), "p27" (structural prior),
"p27p28" (+ pair prior). The full set (p27+p28+p29) is the v30 main run."""

import native_v26_data
import native_v27_data
import native_v28_data
from native_structural_prior_v30 import PRIOR27, PRIOR28
from native_v30_bank import DEST as BANK
from native_v30_bank import PERCEPTION


def load_variant(variant):
    native_v26_data.DEST, native_v26_data.ALIGN = BANK, PERCEPTION
    native_v27_data.STRUCTURAL, native_v27_data.BANK = PRIOR27, BANK
    native_v28_data.PAIR = PRIOR28
    if variant == "none":
        tasks, inventory, packs, prefixes, provenance = native_v26_data.load_data()
    elif variant == "p27":
        tasks, inventory, packs, prefixes, provenance = native_v27_data.load_data()
    elif variant == "p27p28":
        tasks, inventory, packs, prefixes, provenance = native_v28_data.load_data()
    else:
        raise ValueError("Unknown v30 ablation variant " + variant)
    provenance = dict(provenance, bank="common_candidate_v30+ablation_" + variant, perception="perception_v30")
    return tasks, inventory, packs, prefixes, provenance
