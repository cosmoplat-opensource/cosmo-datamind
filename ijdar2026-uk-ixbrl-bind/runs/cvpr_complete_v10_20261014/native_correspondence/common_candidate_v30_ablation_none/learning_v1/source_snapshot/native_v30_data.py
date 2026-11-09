"""Load the v30 bank with the v30-refitted structural, pair and field priors (v29-shaped packs)."""

import native_v26_data
import native_v27_data
import native_v28_data
import native_v29_data
from native_structural_prior_v30 import PRIOR27, PRIOR28, PRIOR29
from native_v30_bank import DEST as BANK
from native_v30_bank import PERCEPTION


def load_data():
    native_v26_data.DEST, native_v26_data.ALIGN = BANK, PERCEPTION
    native_v27_data.STRUCTURAL, native_v27_data.BANK = PRIOR27, BANK
    native_v28_data.PAIR = PRIOR28
    native_v29_data.FIELD = PRIOR29
    tasks, inventory, packs, prefixes, provenance = native_v29_data.load_data()
    provenance = dict(provenance, bank="common_candidate_v30+priors_v27_v28_v29_refit", perception="perception_v30")
    return tasks, inventory, packs, prefixes, provenance
