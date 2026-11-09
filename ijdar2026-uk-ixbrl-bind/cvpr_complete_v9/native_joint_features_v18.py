"""Reference-blind features for a common Cartesian joint-state space.

The same frozen v17 generic unary scorer selects the beam for every control.
All compound states and public compatibility features are frozen before the
2023 feedback file is opened. No state is inserted using a teacher answer.
"""

from datetime import datetime, timezone
import itertools
import json
from pathlib import Path

import numpy as np
import torch

from native_binding_candidates_v18 import DEST
from native_binding_expand_v17 import DEST as PREVIOUS
from native_binding_features_v17 import candidate_records, extras, CHANNELS
from native_binding_learning_v17 import BindingMatcher, prior
from native_field_retrieval_v12 import DEST as FIELD
from native_dimension_network_v17 import DEST as NETWORK
from pilot_features import encode_texts, TEXT_MODEL
from prepare import save, sha


SEEDS = (19, 41, 73)
PROJECTION, NODE_DIM, REL_DIM = 48, 112, 8
BEAM = 4
STATE_DIM = 4*NODE_DIM+4+REL_DIM
XI = "http://www.xbrl.org/2003/instance"
ISO = "http://www.xbrl.org/2003/iso4217"


def records_for(task):
    records = candidate_records(task)
    literal_zero = any(q["printed_value"] == "0" and not q.get("semantic_zero_hypothesis_not_literal_zero") for q in task["quantity_candidates"])
    for row in records["unit_quantity"]:
        uncertain_zero = row["metadata"]["printed"] == "0" and not literal_zero
        row["metadata"]["semantic_zero_hypothesis_not_literal_zero"] = uncertain_zero
        if uncertain_zero:
            row["text"] += " | possible zero encoding for a financial dash; unavailable interpretation remains unknown"
    return records


def node_beam(rows, scores, channel):
    ranked = sorted(range(len(rows)), key=lambda i: (-float(scores[i]), i))
    chosen, values = [], set()
    # Include both period kinds when they are visibly proposed, without labels.
    if channel == "period":
        for kind in ("instant", "duration"):
            found = next((i for i in ranked if rows[i]["value"] and rows[i]["value"]["kind"] == kind), None)
            if found is not None:
                chosen.append(found)
                values.add(json.dumps(rows[found]["value"], sort_keys=True))
    for i in ranked:
        if rows[i]["value"] is None:
            continue
        key = json.dumps(rows[i]["value"], sort_keys=True)
        if key not in values and len(chosen) < BEAM:
            chosen.append(i)
            values.add(key)
    return chosen + [len(rows)]  # explicit unknown, never a teacher-derived null


def header_alignment(row, box):
    header = (row or {}).get("metadata", {}).get("header") or {}
    hbox = header.get("box")
    if not hbox:
        return 0.0
    center = (box[0]+box[2])/2
    hcenter = (hbox[0]+hbox[2])/2
    return max(0.0, 1.0-abs(center-hcenter)/max(hbox[2]-hbox[0], .01))


def unit_compatibility(field, amount):
    if not field or not amount or amount["value"] is None:
        return 0., 0.
    kind = field["metadata"].get("type") or ""
    unit = amount["value"]["unit"]
    numerator, denominator = unit["numerator"], unit["denominator"]
    if kind == "{"+XI+"}monetaryItemType":
        match = len(numerator) == 1 and numerator[0].startswith("{"+ISO+"}") and not denominator
    elif kind == "{"+XI+"}sharesItemType":
        match = numerator == ["{"+XI+"}shares"] and not denominator
    elif kind.startswith("{http://www.xbrl.org/dtr/type/") and kind.endswith("}perShareItemType"):
        match = len(numerator) == 1 and numerator[0].startswith("{"+ISO+"}") and denominator == ["{"+XI+"}shares"]
    else:
        return 0., 0.
    return 1., float(match)


def public_relations(rows, state, box, graph):
    f, p, d, q = [rows[c][i] if i < len(rows[c]) else None for c, i in zip(CHANNELS, state, strict=True)]
    declared = f["metadata"].get("period_type") if f else None
    pvalue = p["value"] if p else None
    period_known = declared in {"instant", "duration"} and pvalue is not None
    unit_known, unit_match = unit_compatibility(f, q)
    dims = d["value"] if d else None
    supported_axes = graph["field_axis_hints"].get(f["value"], []) if f else []
    axis_support = sum(v["axis"] in supported_axes for v in dims)/len(dims) if dims else 0.
    membership = graph["pair_set"]
    member_support = sum((v["axis"],v["member"]) in membership for v in dims)/len(dims) if dims else 0.
    # Absence from the incomplete graph never becomes a contradiction.
    return np.array([float(period_known), float(period_known and declared == pvalue["kind"]), unit_known, unit_match,
                     axis_support, member_support, header_alignment(p,box), header_alignment(q,box)], dtype="float32")


def state_features(nodes, states, unary, relations):
    parts = [nodes[c][states[:, j]] for j, c in enumerate(CHANNELS)]
    return np.concatenate(parts+[unary,relations], 1).astype("float32")


def prepare():
    folder = DEST / "features"
    if (folder / "manifest.json").exists():
        raise RuntimeError("Joint state feature inventory already frozen")
    torch.set_num_threads(2)
    public_file = DEST / "candidates/tasks.json"
    candidates_meta = json.loads((DEST / "candidates/manifest.json").read_text())
    old_meta_file = PREVIOUS / "features/manifest.json"
    old_meta = json.loads(old_meta_file.read_text())
    graph_file = NETWORK / "public/report_graphs.json"
    graph_meta = json.loads((NETWORK / "manifest.json").read_text())
    if not candidates_meta["complete"] or sha(public_file) != candidates_meta["candidate_inventory_sha256"] or sha(graph_file) != graph_meta["public_graphs_sha256"]:
        raise ValueError("Complete frozen shared public candidates/graphs required")
    for name in ("context.npy","ids.json"):
        if sha(PREVIOUS/"features"/name) != old_meta["files"][name]:
            raise ValueError("Frozen common context feature changed")
    tasks = json.loads(public_file.read_text())
    ids = json.loads((PREVIOUS / "features/ids.json").read_text())
    if ids != [t["task_id"] for t in tasks] or len(tasks) != 840:
        raise ValueError("Complete ordered native target inventory required")
    records = [records_for(t) for t in tasks]
    embeddings = encode_texts(sorted({r["text"] for row in records for rows in row.values() for r in rows}))
    old_fields_file = FIELD / "features/candidates.npz"
    field_meta = json.loads((FIELD / "features/manifest.json").read_text())
    if sha(old_fields_file) != field_meta["files"]["candidates.npz"]:
        raise ValueError("Common source field vectors changed")
    old_fields = np.load(old_fields_file)
    contexts = np.load(PREVIOUS / "features/context.npy")
    projection = np.random.default_rng(20261015).normal(size=(384,PROJECTION)).astype("float32")/np.sqrt(PROJECTION)
    graphs = json.loads(graph_file.read_text())
    for graph in graphs.values():
        graph["pair_set"] = {(p["axis"],p["member"]) for p in graph["axis_member_hints"]}
    folder.mkdir(parents=True, exist_ok=True)
    np.save(folder/"projection.npy",projection)
    np.save(folder/"context.npy",contexts)
    save(folder/"ids.json",ids)
    save(folder/"records.json",[{c:[{"value":r["value"],"metadata":r["metadata"]} for r in row[c]] for c in CHANNELS} for row in records])
    parent_hashes, models = {}, {}
    companies = sorted({t["company"] for t in tasks})
    for company in companies:
        for seed in SEEDS:
            parent = PREVIOUS/"learning_v1/parents"/company/str(seed)/"generic_joint"
            metadata = json.loads((parent/"metadata.json").read_text())
            if metadata["excluded_company"] != company or sha(parent/"weights.pt") != metadata["weights_sha256"]:
                raise ValueError("Shared unary parent violates company exclusion or provenance")
            model = BindingMatcher("generic_joint",448,old_meta["context_dimension"])
            model.load_state_dict(torch.load(parent/"weights.pt",weights_only=True))
            model.eval()
            models[(company,seed)] = model
            parent_hashes[str(parent/"weights.pt")] = metadata["weights_sha256"]
    for i, (task,row) in enumerate(zip(tasks,records,strict=True)):
        full, nodes, priors = {}, {}, {}
        for c in CHANNELS:
            vectors = []
            for j, candidate in enumerate(row[c]):
                vector = extras(candidate,c,task["target_box"])
                vector[:384] = embeddings[candidate["text"]]
                if c == "field":
                    vector[:401] = old_fields[task["task_id"]][j]
                if c == "unit_quantity":
                    vector[436] = candidate["metadata"].get("semantic_zero_hypothesis_not_literal_zero",False)
                vectors.append(vector)
            null = np.zeros(448,dtype="float32")
            null[415] = 1
            null[416+CHANNELS.index(c)] = 1
            full[c] = torch.from_numpy(np.stack(vectors+[null]))
            nodes[c] = np.concatenate((full[c][:,:384].numpy()@projection,full[c][:,384:].numpy()),1).astype("float32")
            priors[c] = prior(task,c,full[c],row[c])
        arrays = {"node_"+c:nodes[c] for c in CHANNELS}
        # Every case within a held-out-company fold uses that fold's same
        # excluded-company parent, including nonlocal historical training cases.
        # Using each training case's own parent would indirectly import the
        # held-out company's labels into its joint training features.
        folds = companies if task["source_year"] == 2023 else [task["company"]]
        for fold in folds:
            for seed in SEEDS:
                model = models[(fold,seed)]
                beam, scores = [], {}
                with torch.no_grad():
                    for c in CHANNELS:
                        probe = full[c].clone()
                        probe[:,436] = 0
                        scores[c] = (priors[c]+model(probe,torch.from_numpy(contexts[i]))).numpy()
                        scores[c] += np.array([.2*header_alignment(r,task["target_box"]) for r in row[c]]+[0],dtype="float32")
                        beam.append(node_beam(row[c],scores[c],c))
                states = np.array(list(itertools.product(*beam)),dtype="int32")
                unary = np.stack([scores[c][states[:,j]] for j,c in enumerate(CHANNELS)],1)
                relations = np.stack([public_relations(row,s,task["target_box"],graphs[task["report"]]) for s in states])
                suffix = fold+"_"+str(seed)
                arrays["states_"+suffix] = states
                arrays["unary_"+suffix] = unary.astype("float32")
                arrays["relations_"+suffix] = relations
        np.savez_compressed(folder/(task["task_id"]+".npz"),**arrays)
        if (i+1)%140 == 0:
            print("Frozen common joint states",i+1,"/",len(tasks),flush=True)
    snapshot = folder/"feature_builder_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(folder/"manifest.json",{
        "complete":True,"prepared_utc":datetime.now(timezone.utc).isoformat(),"targets":len(tasks),
        "node_dimension":NODE_DIM,"state_dimension":STATE_DIM,"context_dimension":old_meta["context_dimension"],
        "seeds":list(SEEDS),"nonunknown_beam_per_channel":BEAM,"maximum_joint_states":(BEAM+1)**4,
        "public_candidate_sha256":sha(public_file),"old_context_feature_manifest_sha256":sha(old_meta_file),
        "public_graph_sha256":sha(graph_file),"common_unary_parent_sha256":parent_hashes,
        "files":{p.name:sha(p) for p in folder.iterdir() if p.is_file() and p.name!="manifest.json"},
        "shared_text_encoder_sha256":{p.name:sha(p) for p in TEXT_MODEL.iterdir() if p.is_file()},
        "all_controls_receive_same_joint_states_unary_scores_and_public_compatibility_features":True,
        "teacher_or_future_reference_files_read":False,"teacher_states_inserted_into_joint_beam":False,
        "common_proposal_scorer_is_a_frozen_historical_model_not_an_independent_source_test":True,
        "all_cases_within_a_fold_use_its_same_excluded_company_proposal_parent":True,
        "incomplete_dimension_network_absence_is_not_a_hard_constraint":True,
        "visual_header_ownership_or_full_dts_certified":False,
    })
    print("Common Cartesian joint-state feature freeze complete",flush=True)


if __name__ == "__main__":
    prepare()
