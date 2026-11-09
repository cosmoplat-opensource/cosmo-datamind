import importlib.util
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('v9_benchmarks',ROOT/'public_benchmarks.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class PublicInputTests(unittest.TestCase):
    def test_terminal_reference_cannot_change_official_conversation_prompt(self):
        fn=m.official_prompt_function()
        row={'Question':['What is 2022 sales?','And 2023?'],'Answer':['5','6'],
             'Question Type':'Conversational','Year':['YES','NO'],'image':b'bytes','Paragraph':''}
        a=m.chart_public(row,0,'image.jpg',fn)
        row['Answer'][-1]='SECRET_TARGET';row['Year']=['SECRET_YEAR']
        b=m.chart_public(row,0,'image.jpg',fn)
        self.assertEqual(a,b);self.assertNotIn('SECRET',a['prompt'])
        self.assertIn('5',a['prompt'])
    def test_nonconversation_reference_is_not_prompted(self):
        fn=m.official_prompt_function()
        row={'Question':['What is the value?'],'Answer':['SECRET_TARGET'],
             'Question Type':'Factoid','image':b'bytes'}
        self.assertNotIn('SECRET_TARGET',m.chart_public(row,1,'image.jpg',fn)['prompt'])
    def test_tat_label_and_derivation_are_not_public(self):
        doc={'uid':'abc','source':'company_2022.pdf'}
        q={'uid':'id','question':'How much?','answer':'SECRET','derivation':'SECRET',
           'block_mapping':{'SECRET':0},'answer_type':'arithmetic','scale':'million'}
        item=m.tat_public(doc,q,'dev')
        self.assertEqual(set(item),{'task_id','document_id','source_group','split','question','pdf','official_ocr'})
        self.assertNotIn('SECRET',str(item))


if __name__=='__main__':unittest.main()
