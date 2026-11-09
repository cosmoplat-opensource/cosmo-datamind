"""Challenge the development scope gain with capacity and optimal-span controls.

Designed after v1 development results; remains development, not an independent
confirmation. Same teacher data, 384 steps and shared native feature blocks.
The generic pair readout has 111 rather than 96 hidden units to match effective
parameters. Its span decoder maximizes raw relation logits without any labels.
"""

import json
from pathlib import Path

import numpy as np
import torch
from torch import nn

import fintab_scope_train_v10 as parent
from fintab_scope_prepare_v10 import DEST
from fintab_scope_score_v10 import aggregate, score
from prepare import save, sha


def best_horizontal_span(boxes, logits):
    if not len(boxes):
        return []
    centers = (boxes[:, 0] + boxes[:, 2]) / 2
    order = np.argsort(centers, kind="stable")
    # Equal visible x centers belong to one endpoint group; no grid gold is used.
    groups = []
    for i in order:
        if not groups or abs(centers[i] - centers[groups[-1][0]]) > 1e-6:
            groups.append([int(i)])
        else:
            groups[-1].append(int(i))
    weights = [float(np.sum(logits[g])) for g in groups]
    current, best, start, bounds = 0.0, 0.0, 0, None
    for i, value in enumerate(weights):
        if current < 0:
            current, start = 0.0, i
        current += value
        if current > best:
            best, bounds = current, (start, i)
    if bounds is None:
        return []
    return [j for group in groups[bounds[0]:bounds[1]+1] for j in group]


def predictions(model, full):
    model.eval()
    with torch.no_grad():
        mean = full.mean(0, keepdim=True)
        nodes = torch.cat([model.nodes(full[i:i+parent.WINDOW], mean) for i in range(0, len(full), parent.WINDOW)])
        heads = torch.nonzero(torch.sigmoid(model.header(nodes).flatten()) >= .5, as_tuple=True)[0].tolist()
        boxes = full[:, -4:]
        cy = (boxes[:, 1] + boxes[:, 3]) / 2
        raw_edges, optimal_edges = [], []
        for h in heads:
            targets = torch.nonzero(cy > cy[h], as_tuple=True)[0].tolist()
            if not targets:
                continue
            pairs = torch.tensor([[h, t] for t in targets], dtype=torch.long)
            logits = model.edges(nodes, boxes, pairs).numpy()
            raw_edges.extend((h, t) for t, s in zip(targets, logits) if s >= 0)
            selected = best_horizontal_span(boxes[targets].numpy(), logits)
            optimal_edges.extend((h, targets[j]) for j in selected)
        return {"header_words": heads, "coverage_edges": raw_edges, "optimal_span_edges": optimal_edges}


def run():
    torch.set_num_threads(2)
    folder = DEST / "strong_controls_v2"
    index = json.loads((DEST / "features/index.json").read_text())
    tables = json.loads((DEST / "public/tables.json").read_text())
    train = [t for t in tables if t["split"] == "train"]
    development = [t for t in tables if t["split"] == "val"]
    label_file = DEST / "training_only/train_labels.json"
    labels = json.loads(label_file.read_text())
    design = {"generic_readout_hidden": 111, "scope_readout_hidden": 96,
              "optimizer_steps": parent.STEPS, "same_data_optimizer_features_and_windows": True,
              "seeds": list(parent.SEEDS), "designed_after_v1_development_results": True,
              "optimal_decoder_uses_only_native_geometry_and_raw_logits": True,
              "train_labels_sha256": sha(label_file), "parent_runner_sha256": sha(parent.__file__),
              "feature_manifest_sha256": sha(DEST / "features/manifest.json"), "runner_sha256": sha(__file__)}
    frozen = folder / "design.json"
    if frozen.exists() and json.loads(frozen.read_text()) != design:
        raise ValueError("Frozen strong control protocol changed")
    save(frozen, design)
    (folder / "runner_snapshot.py").write_bytes(Path(__file__).read_bytes())
    features = {}
    for row in index:
        if sha(row["file"]) != row["sha256"]:
            raise ValueError("Frozen shared word features changed")
        features[row["table_id"]] = torch.from_numpy(np.load(row["file"]))
    for seed in parent.SEEDS:
        output = folder / str(seed)
        final = output / "manifest.json"
        if final.exists():
            continue
        torch.manual_seed(seed)
        model = parent.CoverageModel("generic_joint")
        model.pair = nn.Sequential(nn.Linear(2*parent.HIDDEN+12, 111), nn.GELU(), nn.Linear(111, 1))
        parent.train(model, train, labels, features, seed)
        output.mkdir(parents=True, exist_ok=True)
        torch.save(model.state_dict(), output / "weights.pt")
        rows = [{"table_id": t["table_id"], **predictions(model, features[t["table_id"]])} for t in development]
        save(output / "predictions.json", rows)
        save(final, {"complete": True, "seed": seed, "tables": len(rows),
                     "parameter_count": sum(p.numel() for p in model.parameters()),
                     "weights_sha256": sha(output / "weights.pt"),
                     "prediction_sha256": sha(output / "predictions.json"),
                     "design_sha256": sha(frozen), "val_labels_read": False})
    # Read development references only after all strong-control predictions are frozen.
    reference_file = DEST / "scoring_only/val_labels.json"
    references = json.loads(reference_file.read_text())
    scores = {}
    for seed in parent.SEEDS:
        rows = json.loads((folder / str(seed) / "predictions.json").read_text())
        if len(rows) != len(development) or {r["table_id"] for r in rows} != set(references):
            raise ValueError("Missing strong-control predictions")
        for key in ("coverage_edges", "optimal_span_edges"):
            # Existing scorer consumes its named public edge channel.
            scored = [score(references[r["table_id"]], r, key) for r in rows]
            scores[f"{key}/{seed}"] = aggregate(scored)
    means = {key: {metric: float(np.mean([scores[f"{key}/{seed}"][metric] for seed in parent.SEEDS]))
                   for metric in scores[f"{key}/{parent.SEEDS[0]}"]}
             for key in ("coverage_edges", "optimal_span_edges")}
    save(folder / "development_results.json", {"complete": True, "all_seed_results": scores, "seed_means": means,
                                               "reference_sha256": sha(reference_file),
                                               "independent_test_or_ontology_exclusive_advantage": False})
    print(json.dumps(means, indent=2), flush=True)


if __name__ == "__main__":
    run()
