"""Matched full-value relational, structured-memory and learned-scope models.

Scope and memory models share the complete episodic memory mechanism. Their
geometry modules are the controlled difference. The generic structured model
retains all node/pair factors and public relations, with no candidate pruning.
"""

import math

import torch
from torch import nn
from torch.nn import functional as F
from visual_scope_geometry_v23 import GenericVisualGeometry, VisualScopeGeometry

METHODS = ("generic_relational", "semantic_memory", "scope_relational")
HIDDEN, K = 48, 8


def collapse_witnesses(values, groups, counts):
    """Vectorized equivalent of frozen v23 grouped log-mean-exp."""
    expanded = groups[None, :].expand(values.shape[0], -1)
    shape = (values.shape[0], len(counts))
    maximum = values.new_full(shape, -torch.inf).scatter_reduce(1, expanded, values.detach(), reduce="amax", include_self=True)
    exponentials = torch.exp(values - maximum.gather(1, expanded))
    total = values.new_zeros(shape).scatter_add(1, expanded, exponentials)
    return maximum + torch.log(total) - torch.log(counts.to(values.dtype))[None, :]


def pad_field(nodes):
    return F.pad(nodes, (0, 112))


def logmean(values, dim):
    return torch.logsumexp(values, dim) - math.log(values.shape[dim])


class ScopeComposer(nn.Module):
    def __init__(self, method, context_dimension):
        super().__init__()
        if method not in METHODS:
            raise ValueError("Unregistered full-binding control")
        self.method = method
        self.has_memory = method != "generic_relational"
        self.node = nn.Linear(224, HIDDEN)
        self.context = nn.Linear(context_dimension, HIDDEN)
        self.unary = nn.ModuleList(nn.Linear(HIDDEN, 1) for _ in range(4))
        self.pair_hidden = nn.ModuleList(nn.Linear(3 * HIDDEN, HIDDEN) for _ in range(3))
        self.pair_output = nn.ModuleList(nn.Linear(HIDDEN, 1) for _ in range(3))
        self.relation = nn.ModuleList(nn.Linear(HIDDEN, 2) for _ in range(3))
        if self.has_memory:
            self.memory_unary = nn.ModuleList(nn.Linear(HIDDEN, 1) for _ in range(4))
            self.memory_pair = nn.ModuleList(nn.Linear(HIDDEN, 1) for _ in range(3))
            self.memory_scale = nn.Parameter(torch.ones(7))
        self.geometry = VisualScopeGeometry() if method == "scope_relational" else GenericVisualGeometry()
        self.geometry_scale = nn.Parameter(torch.zeros(()))
        outputs = list(self.unary) + list(self.pair_output) + list(self.relation)
        if self.has_memory:
            outputs += list(self.memory_unary) + list(self.memory_pair)
        for layer in outputs:
            nn.init.zeros_(layer.weight)
            nn.init.zeros_(layer.bias)

    def memory_cache(self, bank):
        if not self.has_memory or not bank:
            return None
        indices = sorted(bank)
        contexts = torch.stack([bank[i]["context"] for i in indices])
        encoded_contexts = torch.tanh(self.context(contexts))
        prototypes = []
        for role in range(4):
            matrices, lengths = [], []
            for i in indices:
                pack = bank[i]
                positive = pack["masks"][role]
                if role:
                    positive = positive[pack["groups"][role - 1]]
                nodes = pack["nodes"][role][positive]
                lengths.append(len(nodes))
                if len(nodes):
                    matrices.append(pad_field(nodes) if role == 0 else nodes)
            encoded = torch.tanh(self.node(torch.cat(matrices))) if matrices else contexts.new_empty((0, HIDDEN))
            prototypes.append(list(encoded.split(lengths)))
        return {"indices": indices, "contexts": contexts,
                "raw_normalized": F.normalize(contexts, dim=1), "encoded_contexts": encoded_contexts,
                "encoded_normalized": F.normalize(encoded_contexts, dim=1), "prototypes": prototypes}

    def retrieve(self, cache, context, query, roles, exclude):
        if cache is None:
            return [], None
        eligible = [j for j, i in enumerate(cache["indices"]) if i != exclude and all(len(cache["prototypes"][role][j]) for role in roles)]
        if not eligible:
            return [], None
        raw = cache["raw_normalized"] @ F.normalize(context, dim=0)
        learned = cache["encoded_normalized"] @ F.normalize(query, dim=0)
        # Same hybrid retrieval in the scope and memory controls. Missing
        # component labels never displace eligible per-role/pair prototypes.
        candidates = torch.tensor(eligible, dtype=torch.long)
        raw_order = candidates[torch.argsort(raw[candidates], descending=True, stable=True)].tolist()
        learned_order = candidates[torch.argsort(learned[candidates], descending=True, stable=True)].tolist()
        selected = raw_order[:K // 2]
        selected += [j for j in learned_order if j not in selected][:K - len(selected)]
        return selected, F.log_softmax(raw[selected] + learned[selected], dim=0)

    def memory_unary_scores(self, encoded, cache, context, query, role, exclude):
        selected, weights = self.retrieve(cache, context, query, (role,), exclude)
        if not selected:
            return encoded.new_zeros(len(encoded))
        values = []
        for j in selected:
            prototype = cache["prototypes"][role][j]
            difference = -(encoded[:, None, :] - prototype[None, :, :]).abs()
            logits = self.memory_unary[role](difference * query).squeeze(-1)
            values.append(logmean(logits, 1))
        return torch.logsumexp(torch.stack(values, 1) + weights[None, :], 1) * self.memory_scale[role]

    def memory_pair_scores(self, field, leaf, cache, context, query, role, exclude):
        selected, weights = self.retrieve(cache, context, query, (0, role + 1), exclude)
        if not selected:
            return field.new_zeros((len(field), len(leaf)))
        values = []
        out = self.memory_pair[role]
        for j in selected:
            fp, lp = cache["prototypes"][0][j], cache["prototypes"][role + 1][j]
            a = logmean(out(-(field[:, None, :] - fp[None, :, :]).abs() * query).squeeze(-1), 1)
            b = logmean(out(-(leaf[:, None, :] - lp[None, :, :]).abs() * query).squeeze(-1), 1)
            values.append(a[:, None] + b[None, :])
        return torch.logsumexp(torch.stack(values, -1) + weights, -1) * self.memory_scale[4 + role]

    def factors(self, pack, cache=None, exclude=None, geometry_off=False):
        context = pack["context"]
        query = torch.tanh(self.context(context))
        encoded = [torch.tanh(self.node(pad_field(pack["nodes"][0])))]
        encoded += [torch.tanh(self.node(nodes)) for nodes in pack["nodes"][1:]]
        unaries = [out(nodes * query).flatten() for nodes, out in zip(encoded, self.unary, strict=True)]
        pool_encoded = torch.tanh(self.node(F.pad(pack["pool"], (112, 0))))
        glyph_scores = self.unary[0](encoded[0][:, None, :] * pool_encoded[None, :, :] * query).squeeze(-1)
        if geometry_off:
            geometry = [context.new_zeros(len(pack["pool"]))] + [context.new_zeros(len(n)) for n in pack["nodes"][1:]]
        else:
            geometry = [self.geometry(pack["pool"]) * self.geometry_scale]
            geometry += [self.geometry(nodes[:, 112:]) * self.geometry_scale for nodes in pack["nodes"][1:]]
        unaries[0] = unaries[0] + torch.logsumexp(glyph_scores + geometry[0][None, :], 1) - torch.logsumexp(geometry[0], 0)
        for role in range(1, 4):
            unaries[role] = unaries[role] + geometry[role]
        if self.has_memory and cache is not None:
            unaries = [u + self.memory_unary_scores(e, cache, context, query, role, exclude)
                       for role, (u, e) in enumerate(zip(unaries, encoded, strict=True))]
        pairs = []
        for role, leaf in enumerate(encoded[1:]):
            layer = self.pair_hidden[role]
            a = F.linear(encoded[0], layer.weight[:, :HIDDEN], layer.bias)
            b = F.linear(leaf, layer.weight[:, HIDDEN:2 * HIDDEN])
            c = F.linear(query, layer.weight[:, 2 * HIDDEN:])
            score = self.pair_output[role](torch.tanh(a[:, None, :] + b[None, :, :] + c)).squeeze(-1)
            relations = pack["relations"][role]
            relation_score = (relations * torch.tanh(self.relation[role](query))).sum(-1)
            score = score + (relation_score + pack["pair_prior"][role])[:, pack["groups"][role]]
            if self.has_memory and cache is not None:
                score = score + self.memory_pair_scores(encoded[0], leaf, cache, context, query, role, exclude)
            pairs.append(score)
        unaries = [u + p for u, p in zip(unaries, pack["prior"], strict=True)]
        return unaries, pairs, glyph_scores + geometry[0][None, :]

    def forward(self, pack, cache=None, exclude=None, geometry_off=False):
        unaries, pairs, field_occurrences = self.factors(pack, cache, exclude, geometry_off)
        semantic = [collapse_witnesses(pair + unary[None, :], group, counts)
                    for pair, unary, group, counts in zip(pairs, unaries[1:], pack["groups"], pack["group_counts"], strict=True)]
        return unaries[0], semantic, (unaries, pairs, field_occurrences)
