"""v30 priors: the v27 structural, v28 pair and v29 field priors refitted on the v30 bank.

The three prior builders are re-pointed at the v30 candidate bank and the v30
perception patch and run unchanged; their outputs land under
common_candidate_v30/prior_v27, prior_v28 and prior_v29.
"""

import native_structural_prior_v27 as p27
import native_structural_prior_v28 as p28
import native_structural_prior_v29 as p29
from native_v30_bank import DEST as BANK
from native_v30_bank import PERCEPTION

PRIOR27, PRIOR28, PRIOR29 = BANK / "prior_v27", BANK / "prior_v28", BANK / "prior_v29"


def rebind():
    for m in (p27, p28, p29):
        m.BANK = BANK
        m.ALIGN = PERCEPTION
    p27.DEST = PRIOR27
    p28.DEST, p28.V27 = PRIOR28, PRIOR27
    p29.DEST, p29.V27 = PRIOR29, PRIOR27


def main():
    rebind()
    if not (PRIOR27 / "manifest.json").exists():
        p27.main()
    if not (PRIOR28 / "manifest.json").exists():
        p28.main()
    if not (PRIOR29 / "manifest.json").exists():
        p29.main()
    print("v30 priors frozen", flush=True)


if __name__ == "__main__":
    main()
