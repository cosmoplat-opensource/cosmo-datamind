from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prepare_target_crops import crop_box


class OriginalPixelCropTests(unittest.TestCase):
    def test_fixed_padding_uses_original_pixels_and_stays_inside_image(self):
        self.assertEqual(crop_box([0, 0, 0.1, 0.1], 100, 100), [0, 0, 26, 14])
        self.assertEqual(crop_box([0.9, 0.9, 1, 1], 100, 100), [74, 86, 100, 100])

    def test_invalid_or_reversed_geometry_is_not_clamped_into_a_plausible_crop(self):
        for box in ([-0.1, 0, 0.2, 0.2], [0.3, 0.1, 0.2, 0.2], [0, 0, 1.1, 1]):
            with self.assertRaises(ValueError):
                crop_box(box, 100, 100)


if __name__ == "__main__":
    unittest.main()
