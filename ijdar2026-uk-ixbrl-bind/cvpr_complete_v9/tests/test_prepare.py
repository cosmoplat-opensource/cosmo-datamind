import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('v9_prepare',Path(__file__).resolve().parents[1]/'prepare.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)


class PreparationTests(unittest.TestCase):
    def test_public_record_excludes_scoring_and_identity_annotations(self):
        q={'qid':'x','lei':'company','report_id':'2024','question':'Read the rectangle',
           'answer':123,'gold':{'concept':'secret'},'sql':'secret','silent_misgrounding_alternatives':[456]}
        row=p.public_record(q)
        self.assertEqual(set(row),p.PUBLIC_FIELDS)
        self.assertNotIn('secret',str(row));self.assertNotIn('123',str(row))

    def test_alias_repair_does_not_change_other_templates(self):
        sql='SELECT p.fact_id FROM fact_dims'
        self.assertEqual(p.repair_sql({'sql':sql,'template':'d_base_units'}),sql)
        self.assertEqual(p.repair_sql({'sql':sql,'template':'b_yoy_change_percent'}),'SELECT fact_id FROM fact_dims')
        grouped=sql+' GROUP BY p.fact_id'
        self.assertEqual(p.repair_sql({'sql':grouped,'template':'b_yoy_change_percent'}),'SELECT fact_id FROM fact_dims GROUP BY fact_id')

    def test_dimensional_exclusion_is_idempotent(self):
        q={'sql':'SELECT * FROM facts f JOIN calc k WHERE k.parent_code=1','template':'c_largest_calc_component'}
        fixed=p.repair_sql(q);self.assertIn('NOT IN',fixed)
        self.assertEqual(p.repair_sql({**q,'sql':fixed}),fixed)


if __name__=='__main__':unittest.main()
