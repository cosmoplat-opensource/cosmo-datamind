"""Held-out companies: deepseek-flash zero-shot vision baseline (same recipe as llm_baseline_v30 --mode vision)."""
import os
import sys
from pathlib import Path
import llm_baseline_v30 as L
HELD = Path(os.environ.get("HELDOUT_ROOT", str(Path(__file__).resolve().parents[1] / "runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1")))
L.BANK = HELD / "bank"; L.PERCEPTION = HELD; L.V27 = HELD / "bank"; L.DEST = HELD / "llm"
import score_native_binding_v17 as S
import json
def scoring_references(future):
    refs = json.loads((HELD / "scoring_only/references.json").read_text())
    return {k: refs[k] for k in future}, {}
L.scoring_references = scoring_references
sys.argv = ["llm_baseline_v30.py", "--mode", "vision"] + sys.argv[1:]
L.main()
