"""Complete shared native retrieval, then audit historical supervision only."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

from native_correspondence_prepare_v10 import DEST as NATIVE
from native_field_retrieval_v10 import DEST
from prepare import save, sha


def main():
    source = NATIVE / "common_visual/manifest.json"
    start = time.monotonic()
    while not source.exists():
        if time.monotonic() - start > 900:
            raise RuntimeError("Native observation still incomplete; no partial-split replacement")
        print("Awaiting complete frozen shared visual observations", flush=True)
        time.sleep(15)
    manifest = DEST / "manifest.json"
    if not manifest.exists():
        subprocess.run([sys.executable, str(Path(__file__).with_name("native_field_retrieval_v10.py"))], check=True)
    if not json.loads(manifest.read_text())["complete"]:
        raise RuntimeError("Expanded shared native retrieval is incomplete")
    tasks = json.loads((DEST / "public/tasks.json").read_text())
    feedback_file = NATIVE / "training_only/past_feedback.json"
    feedback = json.loads(feedback_file.read_text())
    past = [t for t in tasks if t["source_year"] == 2023]
    counts = {"past_targets": len(past), "retrieval_exact_field_coverage": 0,
              "retrieval_name_coverage": 0, "top1_exact_field_correct": 0,
              "common_visual_failed_past_targets": 0}
    details = []
    for task in past:
        ref = feedback[task["task_id"]]
        if ref["source_year"] != 2023:
            raise ValueError("Future reference cannot enter retrieval development audit")
        cards = task["field_candidates"]
        ranks = [i for i, c in enumerate(cards) if c["name"] == ref["field_name"]
                 and c["namespace"] == ref["field_namespace"]]
        name_ranks = [i for i, c in enumerate(cards) if c["name"] == ref["field_name"]]
        counts["retrieval_exact_field_coverage"] += bool(ranks)
        counts["retrieval_name_coverage"] += bool(name_ranks)
        counts["top1_exact_field_correct"] += bool(ranks and ranks[0] == 0)
        counts["common_visual_failed_past_targets"] += task["common_observation_failed"]
        details.append({"task_id": task["task_id"], "exact_field_rank": ranks[0] if ranks else None,
                        "name_rank": name_ranks[0] if name_ranks else None,
                        "missing_field_consumes_automatic_feedback_budget": not bool(ranks)})
    save(DEST / "past_retrieval_audit.json", {
        "complete": True, "counts": counts, "details": details,
        "future_teacher_or_scoring_files_read": False,
        "no_new_human_confirmations": True, "automatic_feedback_budgets": [0, 5, 10],
        "public_input_sha256": sha(DEST / "public/tasks.json"),
        "past_teacher_sha256": sha(feedback_file), "runner_sha256": sha(__file__),
        "completed_utc": datetime.now(timezone.utc).isoformat(),
    })
    print("Historical native field retrieval audit", json.dumps(counts), flush=True)


if __name__ == "__main__":
    main()
