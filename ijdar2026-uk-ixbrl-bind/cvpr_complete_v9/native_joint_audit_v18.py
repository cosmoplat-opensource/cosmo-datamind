"""2023-only candidate/beam feasibility after the public feature freeze."""

from collections import Counter
from datetime import datetime,timezone
import json

import numpy as np

from native_binding_candidates_v18 import DEST
from native_binding_candidate_audit_v16 import matching_components
from native_binding_prepare_v14 import DEST as TEACHERS
from native_joint_learning_v18 import state_feedback
from native_joint_features_v18 import SEEDS
from prepare import save,sha


def audit():
    output = DEST/"training_only/historical_feasibility.json"
    if output.exists():
        raise RuntimeError("Historical joint feasibility already frozen")
    feature_file = DEST/"features/manifest.json"
    features = json.loads(feature_file.read_text())
    public_file = DEST/"candidates/tasks.json"
    if not features["complete"] or sha(public_file) != features["public_candidate_sha256"]:
        raise ValueError("Public state freeze required before historical audit")
    teacher_file = TEACHERS/"training_only/past_binding_feedback.json"
    if sha(teacher_file) != json.loads((TEACHERS/"manifest.json").read_text())["teacher_sha256"]:
        raise ValueError("Historical teacher changed")
    feedback = json.loads(teacher_file.read_text())
    tasks = json.loads(public_file.read_text())
    records_file = DEST/"features/records.json"
    if sha(records_file) != features["files"]["records.json"]:
        raise ValueError("Shared component records changed")
    records = json.loads(records_file.read_text())
    counts,beam,rows = Counter(),{str(s):Counter() for s in SEEDS},{}
    for i,task in enumerate(tasks):
        if task["source_year"] != 2023:
            continue
        ident = task["task_id"]
        result = matching_components(task,feedback[ident])
        for key,value in result.items():
            counts[key] += value
        rows[ident] = result
        with np.load(DEST/"features"/(ident+".npz")) as arrays:
            for seed in SEEDS:
                states = arrays["states_"+task["company"]+"_"+str(seed)]
                _,known = state_feedback(task,records[i],feedback[ident],states)
                beam[str(seed)]["targets"] += 1
                beam[str(seed)]["complete_tuple_available"] += all(known)
                beam[str(seed)]["partial_feedback_available"] += any(known)
    counts["historical_targets"] = len(rows)
    if len(rows) != 280:
        raise ValueError("Complete historical inventory required")
    save(output,{"complete":True,"audited_utc":datetime.now(timezone.utc).isoformat(),"counts":dict(counts),
        "own_company_excluded_proposal_beam_by_seed":{s:dict(c) for s,c in beam.items()},"per_target_candidate_availability":rows,
        "stage":"historical_candidate_or_beam_upper_bound_not_model_accuracy",
        "future_or_independent_reference_files_read":False,"teacher_states_inserted_into_beam":False,
        "zero_and_calendar_interpretations_remain_uncertain_despite_semantic_teacher_agreement":True,
        "public_candidate_sha256":sha(public_file),"feature_manifest_sha256":sha(feature_file),"historical_teacher_sha256":sha(teacher_file),
        "runner_sha256":sha(__file__),"human_runtime_confirmations":0})
    print("Historical v18 joint feasibility",dict(counts),{s:dict(c) for s,c in beam.items()},flush=True)


if __name__ == "__main__":
    audit()
