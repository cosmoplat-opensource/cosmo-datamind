"""Equal-input header-coverage learning; official test and val labels unopened.

This is a donor/development component experiment, not a financial-semantic
binding benchmark. A generic Transformer receives the same pixels, text,
geometry, training labels, optimizer steps and rectangle decoder as the scope
model. Structural coverage must not be reported as ontology-exclusive ability.
"""

import argparse
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from fintab_scope_prepare_v10 import DEST, coverage
from prepare import save, sha

SEEDS = (19, 41, 73)
METHODS = ("generic_joint", "learned_rectangle_scope", "scope_without_pixels", "scope_without_text")
STEPS = 384
WINDOW = 512
PAIR_BATCH = 256
HIDDEN = 64


def training_labels(record):
    owners, cells = record["word_cell_ids"], record["cells"]
    header = np.array([float(cells[o]["is_column_header"]) if o is not None else -1 for o in owners], dtype="float32")
    target = np.array([o is not None and not cells[o]["is_column_header"]
                       and not cells[o]["is_projected_row_header"] for o in owners])
    positives, negatives = [], []
    body = np.flatnonzero(target)
    for h, owner in enumerate(owners):
        if owner is None or not cells[owner]["is_column_header"]:
            continue
        cell = cells[owner]
        for t in body:
            answer = coverage(cell, cells[owners[t]])
            if answer is not None:
                (positives if answer else negatives).append((h, int(t)))
    # Nonheader negatives are sampled from training annotations only. This
    # avoids materializing every O(N^2) negative of very long native tables.
    nonheaders = np.flatnonzero(header == 0)
    rng = np.random.default_rng(20261014)
    if len(nonheaders) and len(body):
        for h, t in zip(rng.choice(nonheaders, 512), rng.choice(body, 512)):
            negatives.append((int(h), int(t)))
    positives = list(dict.fromkeys(positives))
    negatives = list(dict.fromkeys(negatives))
    if len(positives) > 4096:
        positives = [positives[i] for i in rng.choice(len(positives), 4096, replace=False)]
    if len(negatives) > 4096:
        negatives = [negatives[i] for i in rng.choice(len(negatives), 4096, replace=False)]
    return header, np.array(positives, dtype="int64").reshape(-1, 2), np.array(negatives, dtype="int64").reshape(-1, 2)


def geometry(source, target):
    return torch.cat((source, target, target - source), -1)


def inside_score(rectangle, target):
    center = (target[:, :2] + target[:, 2:]) / 2
    margins = torch.cat((center - rectangle[:, :2], rectangle[:, 2:] - center), 1)
    # Explicit range factor, learned from annotated coverage rather than hardcoded ownership.
    return 20 * margins.min(1).values


class CoverageModel(nn.Module):
    def __init__(self, method):
        super().__init__()
        self.method = method
        self.projection = nn.Linear(1156, HIDDEN)
        layer = nn.TransformerEncoderLayer(HIDDEN, 4, dim_feedforward=128, dropout=0,
                                            activation="gelu", batch_first=True)
        self.context = nn.TransformerEncoder(layer, 1, enable_nested_tensor=False)
        self.header = nn.Linear(HIDDEN, 1)
        self.pair = nn.Sequential(nn.Linear(2*HIDDEN + 12, 96), nn.GELU(), nn.Linear(96, 1))
        self.range = (nn.Sequential(nn.Linear(HIDDEN, 32), nn.GELU(), nn.Linear(32, 4))
                      if method != "generic_joint" else None)

    def nodes(self, features, global_features=None):
        x = features.clone()
        if self.method == "scope_without_pixels":
            x[:, :768] = 0
        if self.method == "scope_without_text":
            x[:, 768:1152] = 0
        global_x = x.mean(0, keepdim=True) if global_features is None else global_features.clone()
        if self.method == "scope_without_pixels":
            global_x[:, :768] = 0
        if self.method == "scope_without_text":
            global_x[:, 768:1152] = 0
        projected = torch.tanh(self.projection(x)) + torch.tanh(self.projection(global_x))
        return self.context(projected.unsqueeze(0)).squeeze(0)

    def rectangles(self, nodes):
        if self.range is None:
            raise ValueError("Generic model uses its public-geometry decoder")
        raw = torch.sigmoid(self.range(nodes))
        lower = raw[:, :2]
        upper = lower + (1 - lower) * raw[:, 2:]
        return torch.cat((lower, upper), 1)

    def edges(self, nodes, boxes, pairs):
        h, t = pairs[:, 0], pairs[:, 1]
        logits = self.pair(torch.cat((nodes[h], nodes[t], geometry(boxes[h], boxes[t])), 1)).flatten()
        if self.method != "generic_joint":
            logits = logits + inside_score(self.rectangles(nodes[h]), boxes[t])
        return logits


def subset_pairs(pos, neg, mapping, rng):
    result, answers = [], []
    for rows, value in ((pos, 1), (neg, 0)):
        eligible = [(mapping[int(h)], mapping[int(t)]) for h, t in rows if int(h) in mapping and int(t) in mapping]
        if not eligible:
            continue
        chosen = rng.choice(len(eligible), min(PAIR_BATCH // 2, len(eligible)), replace=False)
        result.extend(eligible[j] for j in chosen)
        answers.extend([value] * len(chosen))
    return torch.tensor(result, dtype=torch.long).reshape(-1, 2), torch.tensor(answers, dtype=torch.float32)


def train(model, tables, labels, features, seed):
    schedule = np.random.default_rng(seed + 20261014)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=0.01)
    supervision = {t["table_id"]: training_labels(labels[t["table_id"]]) for t in tables}
    model.train()
    for step in range(STEPS):
        table = tables[int(schedule.integers(len(tables)))]
        ident = table["table_id"]
        full = features[ident]
        indices = np.sort(schedule.choice(len(full), min(WINDOW, len(full)), replace=False))
        mapping = {int(old): new for new, old in enumerate(indices)}
        x = full[indices]
        hdr, pos, neg = supervision[ident]
        node = model.nodes(x, full.mean(0, keepdim=True))
        known = hdr[indices] >= 0
        optimizer.zero_grad()
        losses = []
        if known.any():
            losses.append(F.binary_cross_entropy_with_logits(model.header(node[known]).flatten(),
                          torch.tensor(hdr[indices][known]), pos_weight=torch.tensor(3.0)))
        pairs, target = subset_pairs(pos, neg, mapping, schedule)
        if len(pairs):
            losses.append(F.binary_cross_entropy_with_logits(model.edges(node, x[:, -4:], pairs), target))
        if losses:
            sum(losses).backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5)
            optimizer.step()
        if (step + 1) % 128 == 0:
            print("Header coverage training", model.method, seed, step + 1, "/", STEPS, flush=True)


def rectangle_projection(boxes, members):
    """Give generic control the same rectangle family, with visible geometry only.

    Minimal rectangle containing its positive members cannot lose a selected
    member. It can add intermediary members; no reference chooses the span.
    """
    if not members:
        return []
    centers = (boxes[:, :2] + boxes[:, 2:]) / 2
    chosen = centers[members]
    lo, hi = chosen.min(0), chosen.max(0)
    return np.flatnonzero(((centers >= lo) & (centers <= hi)).all(1)).tolist()


def predict(model, full):
    model.eval()
    with torch.no_grad():
        mean = full.mean(0, keepdim=True)
        nodes = torch.cat([model.nodes(full[i:i+WINDOW], mean) for i in range(0, len(full), WINDOW)])
        heads = torch.nonzero(torch.sigmoid(model.header(nodes).flatten()) >= 0.5, as_tuple=True)[0].tolist()
        boxes = full[:, -4:]
        cy = (boxes[:, 1] + boxes[:, 3]) / 2
        edges, projected = [], []
        for h in heads:
            targets = torch.nonzero(cy > cy[h], as_tuple=True)[0].tolist()
            if not targets:
                continue
            pairs = torch.tensor([[h, t] for t in targets], dtype=torch.long)
            scores = torch.sigmoid(model.edges(nodes, boxes, pairs))
            selected = [t for t, s in zip(targets, scores.tolist()) if s >= 0.5]
            edges.extend((h, t) for t in selected)
            projection = rectangle_projection(boxes.numpy(), selected)
            projected.extend((h, t) for t in projection if float(cy[t]) > float(cy[h]))
        return {"header_words": heads, "coverage_edges": edges,
                "generic_rectangle_decoder_edges": projected}


def run(methods):
    torch.set_num_threads(2)
    folder = DEST / "learning_v1"
    public_file, feature_file = DEST / "public/tables.json", DEST / "features/manifest.json"
    feature_manifest = json.loads(feature_file.read_text())
    if not feature_manifest["complete"]:
        raise RuntimeError("Complete same-input native features required")
    rows = json.loads(public_file.read_text())
    train_rows = [r for r in rows if r["split"] == "train"]
    val_rows = [r for r in rows if r["split"] == "val"]
    labels_file = DEST / "training_only/train_labels.json"
    labels = json.loads(labels_file.read_text())
    if set(labels) != {r["table_id"] for r in train_rows}:
        raise ValueError("Training split labels differ from frozen public inventory")
    index_file = DEST / "features/index.json"
    if sha(index_file) != feature_manifest["index_sha256"]:
        raise ValueError("Frozen common feature index changed")
    features = {}
    for record in json.loads(index_file.read_text()):
        file = Path(record["file"])
        if sha(file) != record["sha256"]:
            raise ValueError("Frozen common native features changed")
        features[record["table_id"]] = torch.from_numpy(np.load(file))
    design = {"methods": METHODS, "seeds": SEEDS, "optimizer_steps": STEPS,
              "learning_rate": 0.001, "weight_decay": 0.01, "pair_batch_limit": PAIR_BATCH,
              "context_window_words": WINDOW, "all_words_receive_predictions": True,
              "training_pair_pool_per_class_table_limit": 4096,
              "nonheader_training_negatives_per_table": 512,
              "effective_parameter_counts_reported_without_unused_matching_parameters": True,
              "generic_control_has_the_same_rectangle_decoder": True,
              "all_models_receive_pixels_text_geometry_and_training_labels": True,
              "predeclared_probability_threshold": 0.5,
              "train_tables": len(train_rows), "development_tables": len(val_rows),
              "val_labels_read_by_training_or_prediction": False,
              "official_test_labels_or_predictions_opened": False,
              "public_sha256": sha(public_file), "feature_manifest_sha256": sha(feature_file),
              "train_labels_sha256": sha(labels_file), "runner_sha256": sha(__file__),
              "not_financial_semantic_ownership_or_independent_main_method_evaluation": True,
              "no_new_human_feedback": True}
    design_file = folder / "design.json"
    # JSON serializes tuples to lists; compare their serialized normal form.
    design = json.loads(json.dumps(design))
    if design_file.exists() and json.loads(design_file.read_text()) != design:
        raise RuntimeError("Frozen header learning protocol changed")
    save(design_file, design)
    (folder / "trainer_snapshot.py").write_bytes(Path(__file__).read_bytes())
    for method in methods:
        for seed in SEEDS:
            output = folder / method / str(seed)
            manifest_file = output / "manifest.json"
            if manifest_file.exists():
                continue
            torch.manual_seed(seed)
            model = CoverageModel(method)
            weights = output / "weights.pt"
            meta = output / "weights_metadata.json"
            output.mkdir(parents=True, exist_ok=True)
            if weights.exists() and meta.exists():
                record = json.loads(meta.read_text())
                if record["weights_sha256"] != sha(weights) or record["design_sha256"] != sha(design_file):
                    raise ValueError("Frozen header model checkpoint changed")
                model.load_state_dict(torch.load(weights, weights_only=True))
            else:
                train(model, train_rows, labels, features, seed)
                torch.save(deepcopy(model.state_dict()), weights)
                save(meta, {"weights_sha256": sha(weights), "design_sha256": sha(design_file),
                            "parameter_count": sum(p.numel() for p in model.parameters()),
                            "training_split_only": True})
            predictions = []
            for table in val_rows:
                predictions.append({"table_id": table["table_id"], **predict(model, features[table["table_id"]])})
            save(output / "predictions.json", predictions)
            save(manifest_file, {"complete": True, "method": method, "seed": seed,
                                 "tables": len(predictions), "prediction_sha256": sha(output / "predictions.json"),
                                 "weights_sha256": sha(weights), "design_sha256": sha(design_file),
                                 "val_labels_read": False})
            print("Frozen development header predictions", method, seed, len(predictions), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--method", choices=METHODS, action="append")
    args = parser.parse_args()
    run(args.method or METHODS)
