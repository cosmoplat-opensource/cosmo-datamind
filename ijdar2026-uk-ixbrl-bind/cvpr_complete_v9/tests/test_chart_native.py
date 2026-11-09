from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chart_native import messages, response_record
from prepare import sha


class ChartNativeInputTests(unittest.TestCase):
    def test_official_prompt_unchanged_and_extra_terminal_field_unused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / 'chart.jpg'
            Image.new('RGB', (10, 10)).save(image)
            task = {'image': 'chart.jpg', 'image_sha256': sha(image),
                    'prompt': 'OFFICIAL PRIOR CONTEXT', 'Answer': 'SECRET_TERMINAL'}
            content = messages(task, root)[0]['content']
            self.assertEqual(content[0]['text'], task['prompt'])
            self.assertNotIn('SECRET_TERMINAL', str(content))

    def test_changed_original_pixels_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Image.new('RGB', (10, 10)).save(root / 'chart.jpg')
            with self.assertRaises(RuntimeError):
                messages({'image': 'chart.jpg', 'image_sha256': 'changed'}, root)

    def test_incomplete_and_empty_answers_remain_terminal_failures(self):
        for raw in ({'content': '123', 'finish_reason': 'length'},
                    {'content': ' ', 'finish_reason': 'stop'}):
            record = response_record('a', raw)
            self.assertIsNone(record['answer'])
        self.assertEqual(response_record('a', {'content': ' [1, 2] ', 'finish_reason': 'stop'})['answer'], '[1, 2]')


if __name__ == '__main__':
    unittest.main()
