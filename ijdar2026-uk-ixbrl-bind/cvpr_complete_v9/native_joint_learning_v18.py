"""Matched joint-state learners with historical-only partial feedback.

All models optimize and select whole Cartesian states. Missing teacher values
in the fixed beam are unobserved, never false-null targets. The memory control
stores masked partial prototypes as well as complete ones. Scope stays unknown.
"""

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path

import numpy as np
import torch
from torch import nn

from native_binding_candidates_v18 import DEST
from native_joint_features_v18 import NODE_DIM, REL_DIM, STATE_DIM, SEEDS, state_features
from native_binding_features_v17 import CHANNELS
from native_binding_learning_v17 import historical_positive, prediction
from native_binding_prepare_v14 import DEST as TEACHERS
from native_field_learning_v12 import feedback_policies
from native_field_retrieval_v12 import DEST as FIELD
from prepare import save, sha


FOLDER = DEST/"learning_v1"
POLICIES = ("hash_prefix","public_information_prefix")
METHODS = ("joint_prior","dense_joint","coupled_joint","joint_memory","frozen_joint_memory","coupled_no_pair")
PRETRAIN_STEPS, LOCAL_STEPS, K = 256,64,8


class JointMatcher(nn.Module):
    def __init__(self,method,dc):
        super().__init__()
        if method not in {"dense_joint","coupled_joint","joint_memory"}:
            raise ValueError("Unregistered joint model")
        self.method = method
        if method == "coupled_joint":
            self.node = nn.Linear(NODE_DIM,80)
            self.context = nn.Linear(dc,80)
            self.unary = nn.ModuleList(nn.Linear(80,1) for _ in CHANNELS)
            self.pairs = nn.ModuleList(nn.Linear(80,1) for _ in range(3))
            self.relations = nn.Linear(80,REL_DIM)
            for out in list(self.unary)+list(self.pairs)+[self.relations]:
                nn.init.zeros_(out.weight)
                nn.init.zeros_(out.bias)
        else:
            self.first = nn.Linear(STATE_DIM+dc,64)
            self.out = nn.Linear(64,1)
            nn.init.zeros_(self.out.weight)
            nn.init.zeros_(self.out.bias)

    def forward(self,states,context,supports=None,no_pair=False):
        if self.method == "coupled_joint":
            nodes = torch.tanh(self.node(states[:,:4*NODE_DIM].reshape(-1,4,NODE_DIM)))
            query = torch.tanh(self.context(context))
            result = sum(out(nodes[:,i]*query).flatten() for i,out in enumerate(self.unary))
            if not no_pair:
                result = result+sum(out(nodes[:,0]*nodes[:,j+1]*query).flatten() for j,out in enumerate(self.pairs))
                result = result+(torch.tanh(self.relations(query))*states[:,-REL_DIM:]).sum(1)
            return result
        if self.method == "dense_joint":
            first = nn.functional.linear(states,self.first.weight[:,:STATE_DIM],self.first.bias)
            first = first+nn.functional.linear(context,self.first.weight[:,STATE_DIM:])
            return self.out(torch.tanh(first)).flatten()
        if not supports:
            return states.new_zeros(len(states))+sum(p.sum()*0 for p in self.parameters())
        vectors,contexts,masks = (torch.stack(v) for v in zip(*supports))
        nearest = torch.argsort((contexts-context).square().mean(1),stable=True)[:K]
        selected_mask = masks[nearest]
        normalization = (STATE_DIM/selected_mask.sum(1).clamp_min(1)).sqrt()
        delta = (states[:,None,:]-vectors[nearest]).abs()*selected_mask[None,:,:]*normalization[None,:,None]
        first = nn.functional.linear(delta,self.first.weight[:,:STATE_DIM])
        first = first+nn.functional.linear((context-contexts[nearest]).abs(),self.first.weight[:,STATE_DIM:],self.first.bias)[None,:,:]
        values = self.out(torch.tanh(first)).squeeze(-1)
        return torch.logsumexp(values,1)-math.log(len(nearest))


def state_feedback(task,records,teacher,states):
    positive = torch.ones(len(states),dtype=torch.bool)
    known = []
    for j,c in enumerate(CHANNELS):
        node_positive = historical_positive(task,records[c],teacher,c)
        matched = node_positive[states[:,j]]
        available = bool(matched.any())
        known.append(available)
        if available:
            positive &= matched
    if not any(known):
        positive[:] = False
    return positive,known


def prototype_mask(known):
    mask = torch.zeros(STATE_DIM)
    for j,value in enumerate(known):
        if value:
            mask[j*NODE_DIM:(j+1)*NODE_DIM] = 1
            mask[4*NODE_DIM+j] = 1
    f,p,d,q = known
    relations = [f and p,f and p,f and q,f and q,f and d,d,p,q]
    mask[-REL_DIM:] = torch.tensor(relations,dtype=torch.float32)
    return mask


def support_bank(indices,packs):
    result = []
    for i in indices:
        item = packs[i]
        if not item["positive"].any():
            continue
        mask = prototype_mask(item["known"])
        center = item["states"][item["positive"]].mean(0)*mask
        result.append((i,(center,item["context"],mask)))
    return result


def fit(model,local,replay,packs,supports,steps,seed):
    active = [i for i in local if packs[i]["positive"].any()]
    older = [i for i in replay if packs[i]["positive"].any()]
    if not active:
        return {"optimizer_steps":0,"sampled_local_cases":0,"sampled_replay_cases":0}
    rng = np.random.default_rng(seed+20261015)
    optimizer = torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.01)
    nlocal = nreplay = 0
    model.train()
    for _ in range(steps):
        batch = rng.choice(active,min(4 if older else 8,len(active)),replace=False).tolist()
        old = rng.choice(older,min(4,len(older)),replace=False).tolist() if older else []
        nlocal += len(batch)
        nreplay += len(old)
        optimizer.zero_grad()
        losses = []
        for i in batch+old:
            item = packs[i]
            memory = [v for j,v in supports if j != i]
            scores = item["prior"]+model(item["states"],item["context"],memory)
            losses.append(torch.logsumexp(scores,0)-torch.logsumexp(scores[item["positive"]],0))
        torch.stack(losses).mean().backward()
        nn.utils.clip_grad_norm_(model.parameters(),5)
        optimizer.step()
    return {"optimizer_steps":steps,"sampled_local_cases":nlocal,"sampled_replay_cases":nreplay}


def load_packs(company,seed,tasks,records,contexts,feedback):
    packs = {}
    for i,task in enumerate(tasks):
        if task["source_year"] != 2023 and task["company"] != company:
            continue
        with np.load(DEST/"features"/(task["task_id"]+".npz")) as arrays:
            suffix = company+"_"+str(seed)
            states = arrays["states_"+suffix]
            unary,relations = arrays["unary_"+suffix],arrays["relations_"+suffix]
            nodes = {c:arrays["node_"+c] for c in CHANNELS}
            values = torch.from_numpy(state_features(nodes,states,unary,relations))
        # Same soft contradiction prior for every model; incomplete graph
        # absence and uncertain geometry are never hard rejection rules.
        common_prior = unary.sum(1)-(relations[:,0]*(1-relations[:,1])+relations[:,2]*(1-relations[:,3]))
        if task["source_year"] == 2023:
            positive,known = state_feedback(task,records[i],feedback[task["task_id"]],states)
        else:
            positive,known = torch.zeros(len(states),dtype=torch.bool),[False]*4
        packs[i] = {"states":values,"indices":states,"prior":torch.from_numpy(common_prior),
                    "context":contexts[i],"positive":positive,"known":known}
    return packs


def run():
    torch.set_num_threads(2)
    feature_file = DEST/"features/manifest.json"
    feature_meta = json.loads(feature_file.read_text())
    public_file = DEST/"candidates/tasks.json"
    if not feature_meta["complete"] or sha(public_file) != feature_meta["public_candidate_sha256"] or not feature_meta["all_cases_within_a_fold_use_its_same_excluded_company_proposal_parent"]:
        raise ValueError("Complete fold-safe common joint-state freeze required")
    if any(sha(DEST/"features"/name) != digest for name,digest in feature_meta["files"].items()):
        raise ValueError("Common joint features changed")
    tasks = json.loads(public_file.read_text())
    records = json.loads((DEST/"features/records.json").read_text())
    contexts = torch.from_numpy(np.load(DEST/"features/context.npy"))
    sources = json.loads((FIELD/"public/tasks.json").read_text())
    policies = feedback_policies(sources)
    save(FOLDER/"feedback_prefixes.json",policies)
    teacher_file = TEACHERS/"training_only/past_binding_feedback.json"
    teacher_meta = json.loads((TEACHERS/"manifest.json").read_text())
    if sha(teacher_file) != teacher_meta["teacher_sha256"]:
        raise ValueError("Historical feedback changed")
    feedback = json.loads(teacher_file.read_text())
    counts = {m:sum(p.numel() for p in JointMatcher(m,feature_meta["context_dimension"]).parameters()) for m in ("dense_joint","coupled_joint","joint_memory")}
    if max(counts.values())/min(counts.values()) > 1.01:
        raise ValueError("Joint model parameter counts exceed 1% matching tolerance")
    design = {"methods":list(METHODS),"seeds":list(SEEDS),"policies":list(POLICIES),"automatic_feedback_budgets":[0,5,10],
        "parameter_counts":counts,"pretrain_optimizer_steps":PRETRAIN_STEPS,"maximum_local_optimizer_steps":LOCAL_STEPS,
        "optimizer":"AdamW","learning_rate":.001,"weight_decay":.01,"gradient_clip":5,"memory_neighbors":K,
        "maximum_pretrain_cases_per_step":8,"maximum_local_cases_per_step":4,"maximum_replay_cases_per_step":4,
        "all_controls_optimize_and_select_whole_joint_states":True,
        "partial_component_feedback_masked_not_converted_to_false_null":True,
        "memory_retains_masked_partial_prototypes_not_only_complete_labels":True,
        "coupled_no_pair_is_weight_tied_inference_ablation_not_a_retrained_method":True,
        "all_models_get_same_joint_states_public_relations_and_common_contradiction_prior":True,
        "own_company_excluded_from_parent_training_and_every_fold_proposal_scorer":True,
        "candidate_feature_manifest_sha256":sha(feature_file),"historical_teacher_sha256":sha(teacher_file),
        "feedback_prefix_sha256":sha(FOLDER/"feedback_prefixes.json"),"runner_sha256":sha(__file__),
        "future_or_independent_reference_files_read":False,"human_runtime_confirmations":0,
        "post_v17_development_not_independent_test":True,"visual_scope_and_full_dts_membership_certified":False}
    design_file = FOLDER/"design.json"
    if design_file.exists() and json.loads(design_file.read_text()) != design:
        raise ValueError("Frozen joint-learning design changed")
    save(design_file,design)
    (FOLDER/"runner_snapshot.py").write_bytes(Path(__file__).read_bytes())
    index = {t["task_id"]:i for i,t in enumerate(tasks)}
    for company in sorted(policies[POLICIES[0]]):
        global_indices = [i for i,t in enumerate(tasks) if t["source_year"] == 2023 and t["company"] != company]
        future = [i for i,t in enumerate(tasks) if t["source_year"] != 2023 and t["company"] == company]
        for seed in SEEDS:
            packs = load_packs(company,seed,tasks,records,contexts,feedback)
            supports = support_bank(global_indices,packs)
            parents = {}
            for base in ("dense_joint","coupled_joint","joint_memory"):
                path = FOLDER/"parents"/company/str(seed)/base
                path.mkdir(parents=True,exist_ok=True)
                weight,metadata = path/"weights.pt",path/"metadata.json"
                torch.manual_seed(seed)
                parent = JointMatcher(base,feature_meta["context_dimension"])
                if metadata.exists():
                    previous = json.loads(metadata.read_text())
                    if previous["weights_sha256"] != sha(weight) or previous["design_sha256"] != sha(design_file):
                        raise ValueError("Frozen joint parent changed")
                    parent.load_state_dict(torch.load(weight,weights_only=True))
                else:
                    exposure = fit(parent,global_indices,[],packs,supports,PRETRAIN_STEPS,seed)
                    torch.save(parent.state_dict(),weight)
                    save(metadata,{"weights_sha256":sha(weight),"design_sha256":sha(design_file),"excluded_company":company,"exposure":exposure})
                parents[base] = parent
                print("Joint parent",company,seed,base,flush=True)
            for policy in POLICIES:
                for budget in (0,5,10):
                    local = [index[ident] for ident in policies[policy][company][str(budget)]]
                    if any(tasks[i]["company"] != company or tasks[i]["source_year"] != 2023 for i in local):
                        raise ValueError("Local feedback crossed company/year boundary")
                    bank = support_bank(global_indices+local,packs)
                    for method in METHODS:
                        output = FOLDER/"predictions"/policy/company/method/str(seed)/(str(budget)+".json")
                        if output.exists():
                            record = json.loads(output.read_text())
                            if record["design_sha256"] != sha(design_file) or [p["task_id"] for p in record["predictions"]] != [tasks[i]["task_id"] for i in future]:
                                raise ValueError("Frozen joint prediction changed")
                            continue
                        base = "joint_memory" if method == "frozen_joint_memory" else "coupled_joint" if method == "coupled_no_pair" else method
                        model = None if method == "joint_prior" else deepcopy(parents[base])
                        exposure = {"optimizer_steps":0,"sampled_local_cases":0,"sampled_replay_cases":0}
                        checkpoint = FOLDER/"local"/policy/company/base/str(seed)/(str(budget)+".pt")
                        checkpoint_meta = checkpoint.with_suffix(".json")
                        if model is not None and budget and method != "frozen_joint_memory":
                            if checkpoint_meta.exists():
                                checkpoint_record = json.loads(checkpoint_meta.read_text())
                                if checkpoint_record["weights_sha256"] != sha(checkpoint) or checkpoint_record["design_sha256"] != sha(design_file):
                                    raise ValueError("Frozen local joint model changed")
                                model.load_state_dict(torch.load(checkpoint,weights_only=True))
                                exposure = checkpoint_record["exposure"]
                            else:
                                exposure = fit(model,local,global_indices,packs,bank,LOCAL_STEPS,seed)
                                checkpoint.parent.mkdir(parents=True,exist_ok=True)
                                torch.save(model.state_dict(),checkpoint)
                                save(checkpoint_meta,{"weights_sha256":sha(checkpoint),"design_sha256":sha(design_file),"exposure":exposure})
                        if model is not None:
                            model.eval()
                        predictions = []
                        with torch.no_grad():
                            for i in future:
                                item = packs[i]
                                logits = item["prior"]
                                if model is not None:
                                    logits = logits+model(item["states"],item["context"],[v for _,v in bank],no_pair=method == "coupled_no_pair")
                                selected = int(torch.argmax(logits))
                                chosen = item["indices"][selected]
                                choices = {c:records[i][c][j]["value"] if j < len(records[i][c]) else None for c,j in zip(CHANNELS,chosen,strict=True)}
                                result = prediction(tasks[i],choices)
                                result["selected_joint_state_index"] = selected
                                qindex,pindex = int(chosen[3]),int(chosen[1])
                                result["quantity_is_uncertain_dash_zero_hypothesis"] = (qindex < len(records[i]["unit_quantity"]) and bool(records[i]["unit_quantity"][qindex]["metadata"].get("semantic_zero_hypothesis_not_literal_zero")))
                                result["period_uses_uncertain_calendar_convention"] = (pindex < len(records[i]["period"]) and bool(records[i]["period"][pindex]["metadata"].get("calendar_convention_assumed")))
                                predictions.append(result)
                        save(output,{"design_sha256":sha(design_file),"company":company,"seed":seed,"method":method,"policy":policy,
                             "automatic_feedback_cases":budget,"exposure":exposure,"predictions":predictions,
                             "future_reference_files_read":False,"whole_joint_state_was_selected":True})
            save(FOLDER/"progress.json",{"last_company":company,"last_seed":seed,"updated_utc":datetime.now(timezone.utc).isoformat(),
                "completed_prediction_files":len(list((FOLDER/"predictions").rglob("*.json")))})
            del packs
    files = sorted((FOLDER/"predictions").rglob("*.json"))
    if len(files) != 1512:
        raise ValueError("All 1512 joint-state configurations required before scoring")
    save(FOLDER/"manifest.json",{"complete":True,"completed_utc":datetime.now(timezone.utc).isoformat(),
        "design_sha256":sha(design_file),"prediction_files":len(files),
        "predictions_sha256":{str(p.relative_to(FOLDER)):sha(p) for p in files},"future_reference_files_read":False,
        "independent_native_test_completed":False})
    print("Complete whole-joint-state development predictions frozen",len(files),flush=True)


if __name__ == "__main__":
    run()
