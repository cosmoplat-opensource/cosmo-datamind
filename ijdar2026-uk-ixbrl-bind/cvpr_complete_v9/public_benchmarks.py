"""Pinned public benchmark adapters, with terminal answers kept in scoring files.

ChartQAPro's endorsed prompt includes preceding reference conversation turns.
That is an explicit, common official input track; it is not adaptation feedback.
The terminal answer must never affect the prompt. No test inference here.
"""
import ast
import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import shutil
import zipfile

from PIL import Image
from prepare import EXP, OUT, save, sha

CHART_REV='e27c2874825874d6767d2bbc538ed4f0dc2c64c2'
TAT_REV='bc46208b22f0bfe47152c98fd0938d238ce868c2'
ROOT=EXP/'ext/cvpr_public_v9'
KIT=EXP/'ext/vlmevalkit_official'


def official_prompt_function():
    """Load only the unchanged endorsed pure prompt function, not the toolkit."""
    file=KIT/'vlmeval/dataset/utils/chartqapro.py'
    body=ast.parse(file.read_text())
    fn=next(n for n in body.body if isinstance(n,ast.FunctionDef) and n.name=='prompt_context')
    namespace={}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),str(file),'exec'),namespace)
    return namespace['prompt_context']


def chart_public(row,index,image_path,prompt_fn):
    questions=row['Question'];answers=row['Answer']
    if not questions or len(questions)!=len(answers):raise ValueError('Malformed question sequence')
    # Removing the terminal reference before calling the author function makes
    # the separation enforceable even if an upstream prompt later changes.
    safe_answers=list(answers[:-1])+['<TERMINAL_REFERENCE_NOT_AVAILABLE>']
    prompt=prompt_fn(questions,safe_answers,row['Question Type'],'Direct')
    return {'task_id':f'chartqapro-{index:04d}','image':image_path,
            'image_sha256':hashlib.sha256(row['image']).hexdigest(),
            'prompt':prompt,'paragraph':row.get('Paragraph') or '',
            'official_prior_conversation_context':len(questions)>1,
            'terminal_reference_disclosed':False}


def tat_public(doc,question,split):
    return {'task_id':question['uid'],'document_id':doc['uid'],
            'source_group':hashlib.sha256(doc['source'].rsplit('_',1)[0].encode()).hexdigest()[:20],
            'split':split,'question':question['question'],
            'pdf':f'docs/{doc["uid"]}.pdf','official_ocr':f'docs/{doc["uid"]}.json'}


def prepare_chart():
    import pyarrow.parquet as pq
    raw=OUT/'chartqapro_test.parquet.partial'
    target=ROOT/'chartqapro';target.mkdir(parents=True,exist_ok=True)
    table=pq.read_table(raw)
    if table.num_rows!=1948:raise ValueError('Official ChartQAPro row count changed')
    prompt_fn=official_prompt_function();public=[];references=[];images=set();types=Counter()
    for index,row in enumerate(table.to_pylist()):
        binary=row['image']
        with Image.open(io.BytesIO(binary)) as im:im.verify()
        digest=hashlib.sha256(binary).hexdigest();image=target/'images'/f'{digest}.jpg'
        image.parent.mkdir(exist_ok=True)
        if image.exists() and sha(image)!=digest:raise ValueError('Frozen chart image changed')
        if not image.exists():image.write_bytes(binary)
        item=chart_public(row,index,str(image.relative_to(target)),prompt_fn)
        public.append(item);images.add(digest);types[row['Question Type']]+=1
        references.append({'task_id':item['task_id'],'Answer':row['Answer'],
                           'Question Type':row['Question Type'],'Year':row['Year']})
    save(target/'public/tasks.json',public);save(target/'scoring_only/references.json',references)
    save(target/'protocol.json',{'dataset':'ChartQAPro','revision':CHART_REV,'questions':len(public),
      'unique_image_hashes':len(images),'question_types_scoring_only':dict(types),
      'source_sha256':sha(raw),'source':'https://huggingface.co/datasets/ahmed-masry/ChartQAPro',
      'endorsed_prompt_source_sha256':sha(KIT/'vlmeval/dataset/utils/chartqapro.py'),
      'official_prompt_includes_question_type_and_previous_reference_turns':True,
      'terminal_answers_only_in_scoring_files':True,'all_methods_share_prior_context':True,
      'not_no_reference_history_track':True,'inference_completed':False,
      'training_or_selection_on_this_test':False,'human_confirmations':0})
    print('Prepared ChartQAPro',len(public),'rows',len(images),'image hashes',flush=True)


def prepare_tat():
    for split in ('train','dev','test'):
        source=(EXP/'ext/tatdqa_train/tatdqa_dataset_train.json') if split=='train' else OUT/f'tatdqa_dataset_{"test_gold" if split=="test" else "dev"}.json.partial'
        data=json.loads(source.read_text());public=[];refs=[];docs=set()
        for row in data:
            doc=row['doc'];docs.add(doc['uid'])
            for q in row['questions']:
                public.append(tat_public(doc,q,split))
                refs.append({'task_id':q['uid'],'reference':q})
        if len({r['task_id'] for r in public})!=len(public):raise ValueError('Duplicate TAT question IDs')
        root=ROOT/'tatdqa'/split
        save(root/'public/tasks.json',public);save(root/'scoring_only/references.json',refs)
        save(root/'protocol.json',{'dataset':'TAT-DQA','split':split,'revision':TAT_REV,
              'questions':len(public),'documents':len(docs),'source_sha256':sha(source),
              'source':'https://github.com/NExTplusplus/TAT-DQA',
              'download_mirror':'https://huggingface.co/datasets/next-tat/TAT-DQA',
              'official_ocr_is_a_separate_input_track':True,'pixels_only_track_requires_rendering':True,
              'terminal_answer_derivation_and_mapping_not_in_public_inputs':True,
              'test_inference_completed':False,'train_dev_test_split_preserved':True,
              'human_confirmations':0})
        print('Prepared TAT-DQA',split,len(docs),'documents',len(public),'questions',flush=True)


def extract_tat(splits=('train','dev','test')):
    for split in splits:
        root=ROOT/'tatdqa'/split;tasks=json.loads((root/'public/tasks.json').read_text())
        expected={t['document_id'] for t in tasks};archive=OUT/f'tatdqa_docs_{split}.zip.partial'
        if not archive.exists():raise RuntimeError('Archive not downloaded')
        headers=(OUT/f'tatdqa_{split}_archive.headers').read_text()
        import re
        sizes=re.findall(r'content-range:\s*bytes\s+0-0/(\d+)',headers,re.I)
        if not sizes or archive.stat().st_size!=int(sizes[-1]):
            raise RuntimeError(f'{split} archive is still incomplete; wait for download completion')
        dest=root/'docs';dest.mkdir(parents=True,exist_ok=True);manifest=[]
        # Parse and CRC-check each wanted member. No arbitrary ZIP paths are used.
        with zipfile.ZipFile(archive) as z:
            members={}
            for info in z.infolist():
                name=Path(info.filename)
                if '__MACOSX' in name.parts or name.stem not in expected or name.suffix not in ('.pdf','.json'):continue
                key=name.name
                if key in members:raise ValueError('Ambiguous duplicate source ZIP member')
                if info.file_size>64*1024*1024:raise ValueError('Unexpected oversized document')
                members[key]=info
            wanted={f'{uid}{suffix}' for uid in expected for suffix in ('.pdf','.json')}
            if set(members)!=wanted:raise ValueError('Official document archive coverage differs')
            total=sum(i.file_size for i in members.values())
            if shutil.disk_usage(root).free < total+5*1024**3:raise RuntimeError('Insufficient disk reserve for extraction')
            for name,info in sorted(members.items()):
                body=z.read(info);file=dest/name;digest=hashlib.sha256(body).hexdigest()
                if file.exists() and sha(file)!=digest:raise ValueError('Frozen source document changed')
                if not file.exists():file.write_bytes(body)
                manifest.append({'file':name,'bytes':len(body),'sha256':digest,'crc32':info.CRC})
        save(root/'document_manifest.json',{'documents':len(expected),'files':manifest,
              'archive_sha256':sha(archive),'crc_checked':True,'model_outputs_used_for_selection':False})
        print('Extracted TAT-DQA',split,len(manifest),'source files',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=('prepare','extract'))
    p.add_argument('--split',choices=('train','dev','test','all'),default='all')
    args=p.parse_args()
    if args.stage=='prepare':prepare_chart();prepare_tat()
    else:extract_tat(('train','dev','test') if args.split=='all' else (args.split,))
