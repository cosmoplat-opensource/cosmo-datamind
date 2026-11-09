import sys
import unittest
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from program_controls_v4 import NEW_METHODS, make
from program_scope_learning_v3 import FactorCorrection


class StrongProgramControlTests(unittest.TestCase):
    def test_generic_controls_have_at_least_factor_capacity_on_real_blocks(self):
        factor = sum(p.numel() for p in FactorCorrection(396, 1175, 12).parameters())
        for method in ("capacity_matched_joint", "generic_bilinear_joint"):
            self.assertGreaterEqual(
                sum(p.numel() for p in make(method, 396, 1175, 12, 19).parameters()),
                factor,
            )
        self.assertEqual(
            sum(
                p.numel()
                for p in make("additive_composition", 396, 1175, 12, 19).parameters()
            ),
            factor,
        )

    def test_new_controls_leave_base_program_unchanged_before_training(self):
        for method in NEW_METHODS:
            model = make(method, 3, 5, 12, 19)
            self.assertTrue(
                torch.equal(
                    model(torch.randn(3), torch.randn(5), torch.randn(4, 12)),
                    torch.zeros(4),
                )
            )

    def test_forward_batch_matches_per_target_for_every_control(self):
        torch.set_num_threads(1)
        fields, contexts, candidates = (
            torch.randn(3, 3),
            torch.randn(3, 5),
            torch.randn(3, 4, 12),
        )
        for method in NEW_METHODS:
            model = make(method, 3, 5, 12, 19)
            with torch.no_grad():
                for p in model.parameters():
                    p.add_(torch.randn_like(p) * 0.01)
            separate = torch.cat(
                [model(f, c, n) for f, c, n in zip(fields, contexts, candidates)]
            )
            joined = model(
                fields.repeat_interleave(4, 0),
                contexts.repeat_interleave(4, 0),
                candidates.flatten(0, 1),
            )
            self.assertTrue(torch.allclose(separate, joined, atol=1e-6))


if __name__ == "__main__":
    unittest.main()
