from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from train_memory import create_index, retrieve


class TrainingMemoryTests(unittest.TestCase):
    def examples(self):
        tasks = [{'task_id': 'a', 'document_id': 'x', 'split': 'train', 'question': 'What is revenue growth?'},
                 {'task_id': 'b', 'document_id': 'y', 'split': 'train', 'question': 'What is deferred tax expense?'}]
        refs = [{'task_id': t['task_id'], 'reference': {'question': t['question'], 'answer': 1,
            'scale': '', 'answer_type': 'arithmetic', 'derivation': '2-1', 'facts': ['2', '1']}} for t in tasks]
        return tasks, refs

    def test_validation_examples_cannot_enter_static_memory(self):
        tasks, refs = self.examples(); tasks[0]['split'] = 'dev'
        with self.assertRaises(RuntimeError): create_index(tasks, refs)

    def test_retrieval_uses_question_not_reference_values(self):
        tasks, refs = self.examples(); a = create_index(tasks, refs)
        refs[0]['reference']['answer'] = 999999; b = create_index(tasks, refs)
        x = retrieve(a, 'revenue growth', 1); y = retrieve(b, 'revenue growth', 1)
        self.assertEqual(x[0]['task_id'], y[0]['task_id'])
        self.assertEqual(x[0]['retrieval_score'], y[0]['retrieval_score'])

    def test_region_annotations_are_not_exported(self):
        tasks, refs = self.examples(); refs[0]['reference']['block_mapping'] = [{'secret': [0, 2]}]
        self.assertNotIn('block_mapping', str(create_index(tasks, refs)['entries']))


if __name__ == '__main__': unittest.main()
