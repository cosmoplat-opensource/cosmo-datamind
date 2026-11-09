"""Shared pretrained pixels/text for the existing two-source diagnostic pool.

Selection, quantities, hypotheses and historical feedback remain unchanged.
This prepares a development smoke comparison, not an independent main result.
No publisher annotations or future reference answers are read.
"""
import argparse
import json
from pathlib import Path
import time

import numpy as np
import torch
from PIL import Image
from transformers import AutoModel,AutoTokenizer,LayoutLMv3Model,LayoutLMv3ImageProcessor

from prepare import EXP,OUT,save,sha
from encoder import MODEL,REV

OLD=EXP/'runs/visual_scope_relations_v8_20261009/additional_native_development_v1'
NATIVE=OLD/'native_renders'
POLICIES=('hash_prefix','public_information_prefix')
TEXT_MODEL=EXP/'ext/shared_semantic_encoder/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41'
D=768+384+4+2+1


def pool(patches,box):
    if box is None:return np.zeros(768,dtype='float32')
    if len(box)!=4 or any(not 0<=v<=1 for v in box) or box[0]>=box[2] or box[1]>=box[3]:
        raise ValueError('Public region outside original image')
    x0,y0,x1,y1=box
    # Pool every patch intersecting a small glyph/heading, not just its center.
    xs=range(max(0,int(np.floor(x0*14))),min(14,int(np.ceil(x1*14))))
    ys=range(max(0,int(np.floor(y0*14))),min(14,int(np.ceil(y1*14))))
    indices=[1+y*14+x for y in ys for x in xs]
    return patches[indices].mean(0) if indices else np.zeros(768,dtype='float32')


def encode_texts(texts):
    tokenizer=AutoTokenizer.from_pretrained(TEXT_MODEL,local_files_only=True,trust_remote_code=False)
    model=AutoModel.from_pretrained(TEXT_MODEL,local_files_only=True,trust_remote_code=False).eval()
    arrays=[]
    for offset in range(0,len(texts),32):
        x=tokenizer(texts[offset:offset+32],truncation=True,max_length=256,padding=True,return_tensors='pt')
        with torch.no_grad():
            hidden=model(**x).last_hidden_state;mask=x['attention_mask'].unsqueeze(-1)
            embedding=(hidden*mask).sum(1)/mask.sum(1)
            arrays.append(torch.nn.functional.normalize(embedding,dim=-1).numpy())
    return dict(zip(texts,np.concatenate(arrays)))


def prepare(policy):
    torch.set_num_threads(2)
    old=OLD/'shared_scope_heads_v1'/policy;dest=OUT/'scope_pilot'/policy
    if (dest/'features/manifest.json').exists():raise RuntimeError('Completed public feature freeze exists')
    source=old/'data/public_inputs.json';data=json.loads(source.read_text())
    graph_file=old/'public_provenance/public_support_graph.json';graph=json.loads(graph_file.read_text())
    prior_file=old/'model/public_graph_features.json';prior=json.loads(prior_file.read_text())
    meta=json.loads((old/'model/feature_manifest.json').read_text())
    if sha(source)!=meta['public_input_sha256'] or sha(graph_file)!=meta['public_graph_sha256'] or sha(prior_file)!=meta['public_features_sha256']:
        raise RuntimeError('Existing shared public freeze differs')
    registry=graph['shared_support_registry'];pages={r['id']:r['pages'] for r in json.loads((NATIVE/'public_pages_dpr2.json').read_text())['reports']}
    reports={r:json.loads((NATIVE/'common_perception_dpr2'/f'{r}.json').read_text()) for r in pages}
    texts=set(q['query']['question'] for q in data['inputs'].values())
    texts.update(h['literal_text'] or '(unknown)' for h in registry.values())
    for report in reports.values():texts.update(c['row_text'] for c in report['elements']['cells'])
    embeddings=encode_texts(sorted(texts))
    provenance=json.loads((MODEL/'provenance.json').read_text())
    if any(sha(MODEL/f)!=h for f,h in provenance['files'].items()):raise RuntimeError('Pinned visual encoder differs')
    vision=LayoutLMv3Model.from_pretrained(MODEL,local_files_only=True,trust_remote_code=False).eval()
    processor=LayoutLMv3ImageProcessor.from_pretrained(MODEL,local_files_only=True,apply_ocr=False)
    shared={};records=[]
    for report,obs in sorted(reports.items()):
        patches={};widths={};image_records=[]
        for page,record in enumerate(pages[report]):
            image=Path(record['image_path'])
            if sha(image)!=record['image_sha256']:raise RuntimeError('Frozen native pixels differ')
            cache=OUT/'scope_pilot/shared_patches'/f'{record["image_sha256"]}.npy';cache.parent.mkdir(parents=True,exist_ok=True)
            with Image.open(image) as im:
                widths[page]=im.size
                if not cache.exists():
                    x=processor(images=im.convert('RGB'),return_tensors='pt')
                    with torch.no_grad():v=vision(pixel_values=x['pixel_values']).last_hidden_state[0].numpy()
                    if v.shape!=(197,768) or not np.isfinite(v).all():raise RuntimeError('Invalid common patch features')
                    np.save(cache,v.astype('float32'))
            patches[page]=np.load(cache);image_records.append({'image_sha256':sha(image),'features_sha256':sha(cache)})
        cells=obs['elements']['cells'];headers=sorted((ident,h) for ident,h in registry.items() if h['report']==report)
        features=[]
        def vector(text,box,page,role):
            geometry=box if box is not None else [0,0,0,0]
            return np.concatenate([pool(patches[page],box),embeddings[text],geometry,role,[page/max(1,len(pages[report])-1)]]).astype('float32')
        for cell in cells:
            w,h=widths[cell['page']];box=[v/(w if i%2==0 else h) for i,v in enumerate(cell['bbox'])]
            features.append(vector(cell['row_text'],box,cell['page'],[0,0]))
        for _,header in headers:
            features.append(vector(header['literal_text'] or '(unknown)',header.get('bbox_normalized'),header['page'],
                [int(header['role']=='period'),int(header['role']=='unit')]))
        array=np.stack(features);file=dest/'features'/f'{report}.npy';file.parent.mkdir(parents=True,exist_ok=True);np.save(file,array)
        shared[report]={'cells':[c['id'] for c in cells],'headers':[ident for ident,_ in headers],
          'header_roles':[0 if h['role']=='period' else 1 for _,h in headers],
          'features':str(file.relative_to(dest)),'features_sha256':sha(file),
          'native_observation_sha256':sha(NATIVE/'common_perception_dpr2'/f'{report}.json'),'images':image_records}
        print('Shared scope report prepared',policy,report,'nodes',len(features),flush=True)
    packs={}
    for ident,item in data['inputs'].items():
        report=item['query']['report'];nodes=shared[report];cell_indices={c:i for i,c in enumerate(nodes['cells'])};head_indices={h:i for i,h in enumerate(nodes['headers'])}
        paths=[];program_indices=[];bundles=graph['program_support_bundles'][ident]
        if [b['original_program_id'] for b in bundles]!=[p['id'] for p in item['candidates']]:raise RuntimeError('Original program order changed')
        for j,bundle in enumerate(bundles):
            for p in bundle['period_support_OR']:
                for u in bundle['unit_support_OR']:
                    paths.append([cell_indices[bundle['cell']],head_indices[p],head_indices[u]]);program_indices.append(j)
        query=np.concatenate([np.zeros(768),embeddings[item['query']['question']],np.zeros(7)]).tolist()
        packs[ident]={'report':report,'paths':paths,'program_indices':program_indices,'query':query,
             'prior':prior['inputs'][ident]['prior'],'period_status':prior['inputs'][ident]['period_status']}
    save(dest/'features/packs.json',packs);save(dest/'features/reports.json',shared)
    save(dest/'features/manifest.json',{'policy':policy,'input_dimension':D,'source_public_input':str(source),
        'source_public_input_sha256':sha(source),'source_graph_sha256':sha(graph_file),
        'packs_sha256':sha(dest/'features/packs.json'),'reports_sha256':sha(dest/'features/reports.json'),
        'visual_encoder':provenance,'text_encoder_files':{f.name:sha(f) for f in TEXT_MODEL.iterdir() if f.is_file()},
        'qa_candidates_and_historical_feedback_unchanged':True,'common_features_for_all_methods':True,
        'scope_ownership_gold_opened':False,'publisher_QA_gold_opened':False,'new_human_confirmations':0,
        'scope_ownership_supervision_available':False,'full_semantic_binding_certified':False,
        'development_only':True,'company_clusters':2,'code_sha256':sha(__file__)})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--policy',choices=POLICIES,required=True);prepare(p.parse_args().policy)
