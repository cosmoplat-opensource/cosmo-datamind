import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tat_memory_agent import common_first
from test_evidence_program import program


class CommonFirstStageTests(unittest.TestCase):
    def test_cached_execution_preserves_current_report_result(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'raw').mkdir()
            (root/'raw/q_0.json').write_text(json.dumps({'content':json.dumps(program()),'finish_reason':'stop'}))
            raw, feedback, failure=common_first({'task_id':'q'},root,1,0)
            self.assertIsNone(failure)
            self.assertEqual(feedback['execution']['answer']['answer'],'20.0')
            self.assertEqual(raw['finish_reason'],'stop')

    def test_common_api_failure_does_not_grant_extra_first_calls(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'responses').mkdir()
            (root/'responses/q.json').write_text(json.dumps({'status':'terminal_api_failure'}))
            raw, feedback, failure=common_first({'task_id':'q'},root,1,0)
            self.assertIsNone(raw);self.assertIsNone(feedback)
            self.assertEqual(failure['status'],'shared_first_stage_failure')

    def test_missing_common_run_is_pending_not_a_scored_wrong_answer(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):common_first({'task_id':'q'},Path(d),1,0)


if __name__=='__main__':unittest.main()
