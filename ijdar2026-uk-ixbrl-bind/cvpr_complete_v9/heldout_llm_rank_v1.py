"""Held-out companies: the deepseek-flash zero-shot field ranking pass (same recipe as llm_baseline_v30 --mode field_rank)."""
import os
import json, sys
from pathlib import Path
import llm_baseline_v30 as L
from native_structural_prior_v27 import table_context
HELD = Path(os.environ.get("HELDOUT_ROOT", str(Path(__file__).resolve().parents[1] / "runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1")))

class P:  # perception stand-in with the same sub-paths the baseline script reads
    pass

def main():
    pages = json.loads((HELD / "localization/public_pages.json").read_text())
    tasks = json.loads((HELD / "bank/public/tasks.json").read_text())
    ctx = {t["task_id"]: table_context(pages[t["page_hash"]]["words"], t["target_box"]) for t in tasks}
    (HELD / "bank").mkdir(exist_ok=True)
    (HELD / "bank/table_contexts.json").write_text(json.dumps(ctx))
    anchors = {t["task_id"]: {"page_hash": t["page_hash"], "headers": {}} for t in tasks}
    (HELD / "localization/public_anchors.json").write_text(json.dumps(anchors))
    L.BANK = HELD / "bank"; L.PERCEPTION = HELD; L.V27 = HELD / "bank"; L.DEST = HELD / "llm"
    sys.argv = ["llm_baseline_v30.py", "--mode", "field_rank"] + sys.argv[1:]
    L.main()

if __name__ == "__main__":
    main()
