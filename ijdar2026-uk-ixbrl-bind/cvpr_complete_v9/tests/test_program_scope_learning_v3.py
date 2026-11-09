import sys
import tempfile
import unittest
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from program_scope_learning_v3 import (
    MEMORY_NEIGHBORS,
    METHODS,
    LearnedMemoryCorrection,
    bag_loss,
    checkpoint_load,
    checkpoint_save,
    make,
    positive_programs,
    prior_and_candidates,
    train,
)


class ProgramApplicabilityTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1)

    def task(self, answers=("17", "-17"), year=2023):
        return {
            "task_id": "a",
            "source_year": year,
            "candidates": [{"computed_answer": v} for v in answers],
        }

    def test_future_feedback_and_wrong_identity_forbidden(self):
        event = {"task_id": "a", "source_year": 2023, "answer": "17"}
        self.assertEqual(
            positive_programs(self.task(), event).tolist(), [True, False, False]
        )
        for task, other in (
            (self.task(year=2024), event),
            (self.task(), dict(event, source_year=2025)),
            (self.task(), dict(event, task_id="b")),
        ):
            with self.assertRaises(ValueError):
                positive_programs(task, other)

    def test_no_fake_null_label_for_missing_candidate(self):
        mask = positive_programs(
            self.task(), {"task_id": "a", "source_year": 2023, "answer": "24"}
        )
        self.assertFalse(mask.any())
        self.assertIsNone(bag_loss(torch.zeros(3), mask))

    def test_null_competes_in_same_denominator_as_candidate(self):
        value = bag_loss(
            torch.tensor([0.0, 0.0, 20.0]), torch.tensor([True, False, False])
        )
        self.assertGreater(value.item(), 19)
        prior, features = prior_and_candidates(
            self.task(answers=()), {"candidate_index": None}, []
        )
        self.assertEqual(prior.tolist(), [2.200000047683716])
        self.assertEqual(features.shape, (1, 12))
        self.assertEqual(features[0, 10].item(), 1)

    def test_invalid_common_program_and_features_rejected(self):
        for observation, features in (
            ({"candidate_index": 2}, [[0.0] * 12] * 2),
            ({"candidate_index": True}, [[0.0] * 12] * 2),
            ({"candidate_index": None}, [[0.0] * 12]),
            ({"candidate_index": None}, [[float("nan")] * 12] * 2),
        ):
            with self.assertRaises(ValueError):
                prior_and_candidates(self.task(), observation, features)

    def test_all_heads_leave_base_unchanged_before_training(self):
        f, c, n = torch.randn(3), torch.randn(5), torch.randn(4, 12)
        supports = [
            (torch.randn(3), torch.randn(5), torch.randn(12)) for _ in range(10)
        ]
        for method in METHODS:
            model = make(method, 3, 5, 12, 19)
            self.assertTrue(torch.equal(model(f, c, n, supports), torch.zeros(4)))

    def test_generic_and_factor_batch_forward_equal_separate_queries(self):
        fs, cs, ns = torch.randn(3, 3), torch.randn(3, 5), torch.randn(3, 4, 12)
        for method in ("generic_joint", "factor_composition"):
            model = make(method, 3, 5, 12, 19)
            with torch.no_grad():
                for p in model.parameters():
                    p.add_(torch.randn_like(p) * 0.01)
            separate = torch.cat([model(f, c, n) for f, c, n in zip(fs, cs, ns)])
            batched = model(
                fs.repeat_interleave(4, 0), cs.repeat_interleave(4, 0), ns.flatten(0, 1)
            )
            self.assertTrue(torch.allclose(separate, batched, atol=1e-6))

    def test_memory_transfers_feature_edits_without_old_answer_values(self):
        model = LearnedMemoryCorrection(3, 5, 12)
        f, c, n = torch.randn(3), torch.randn(5), torch.randn(4, 12)
        supports = [
            (f + 0.001 * i, c + 0.001 * i, torch.ones(12) * i) for i in range(12)
        ]
        pairs = model.pairs(f, c, n, supports)
        self.assertEqual(pairs.shape, (4, MEMORY_NEIGHBORS, 20))
        self.assertTrue(torch.allclose(pairs[0, 0, -12:], n[0].abs()))
        self.assertEqual(model(f, c, n, supports).shape, (4,))

    def test_single_feedback_memory_cannot_match_itself(self):
        model = make("learned_semantic_memory", 3, 5, 12, 19)
        pack = {
            "field": torch.randn(3),
            "context": torch.randn(5),
            "candidate": torch.randn(3, 12),
            "prior": torch.zeros(3),
            "positive": torch.tensor([True, False, False]),
        }
        before = {k: v.clone() for k, v in model.state_dict().items()}
        train(
            model,
            [0],
            [pack],
            [(0, (pack["field"], pack["context"], pack["candidate"][0]))],
            1,
        )
        # No self evidence produces exactly zero matcher-head gradients.
        self.assertTrue(torch.equal(model.match[-1].weight, before["match.2.weight"]))
        self.assertTrue(torch.equal(model.match[-1].bias, before["match.2.bias"]))

    def test_only_missing_bags_do_not_change_model(self):
        for method in METHODS:
            model = make(method, 3, 5, 12, 19)
            before = {k: v.clone() for k, v in model.state_dict().items()}
            self.assertEqual(
                train(
                    model, [0], [{"positive": torch.zeros(2, dtype=torch.bool)}], [], 5
                ),
                0,
            )
            self.assertTrue(
                all(torch.equal(before[k], v) for k, v in model.state_dict().items())
            )

    def test_all_heads_can_learn_small_equal_feedback_bags(self):
        packs, supports = [], []
        for i in range(4):
            numeric = torch.zeros(3, 12)
            numeric[:, 0] = torch.tensor([1.0, 0.0, 0.0])
            numeric[2, 10] = 1
            p = {
                "field": torch.ones(3) * i / 10,
                "context": torch.ones(5) * i / 10,
                "candidate": numeric,
                "prior": torch.tensor([0.0, 2.2, 0.0]),
                "positive": torch.tensor([True, False, False]),
            }
            packs.append(p)
            supports.append((i, (p["field"], p["context"], numeric[0])))
        for method in METHODS:
            model = make(method, 3, 5, 12, 19)
            support = [s for _, s in supports]
            before = bag_loss(
                packs[0]["prior"]
                + model(
                    packs[0]["field"],
                    packs[0]["context"],
                    packs[0]["candidate"],
                    support,
                ),
                packs[0]["positive"],
            )
            self.assertEqual(train(model, list(range(4)), packs, supports, 20), 4)
            after = bag_loss(
                packs[0]["prior"]
                + model(
                    packs[0]["field"],
                    packs[0]["context"],
                    packs[0]["candidate"],
                    support,
                ),
                packs[0]["positive"],
            )
            self.assertLess(after.item(), before.item())

    def test_checkpoint_requires_matching_design_and_file_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model.pt"
            model = make("generic_joint", 3, 5, 12, 19)
            self.assertFalse(checkpoint_load(path, model, "design-a"))
            checkpoint_save(path, model, "design-a")
            self.assertTrue(checkpoint_load(path, model, "design-a"))
            with self.assertRaises(RuntimeError):
                checkpoint_load(path, model, "design-b")
            path.write_bytes(path.read_bytes() + b"changed")
            with self.assertRaises(RuntimeError):
                checkpoint_load(path, model, "design-a")


if __name__ == "__main__":
    unittest.main()
