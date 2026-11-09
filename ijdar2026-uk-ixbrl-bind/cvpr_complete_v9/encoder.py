"""Pinned, frozen LayoutLMv3 visual patch features, common to every control.

This development encoder is not either planned full VLM backbone. No labels,
source tags or reference rectangles enter feature extraction.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time
from download import fetch as fetch_pinned

from prepare import EXP,OUT,save,sha

REV='cfbbbff0762e6aab37086fdd4739ad14fe7d5db4'
MODEL=EXP/'ext/cvpr_public_v9/encoders/layoutlmv3-base'/REV
FILES=('config.json','preprocessor_config.json','model.safetensors')


def fetch():
    meta=json.loads((OUT/'layoutlmv3_pinned_metadata.json').read_text())
    if meta['sha']!=REV:raise RuntimeError('Encoder publisher revision differs')
    entries={r['rfilename']:r for r in meta['siblings']};MODEL.mkdir(parents=True,exist_ok=True)
    for name in FILES:
        file=MODEL/name;expected=entries[name].get('lfs',{}).get('sha256')
        if file.exists():
            if expected and sha(file)!=expected:raise RuntimeError('Pinned encoder weight hash differs')
            continue
        size=entries[name]['size']
        if shutil.disk_usage(MODEL).free < size+5*1024**3:raise RuntimeError('Insufficient disk reserve')
        tmp=file.with_suffix(file.suffix+'.partial')
        url=f'https://huggingface.co/microsoft/layoutlmv3-base/resolve/{REV}/{name}'
        fetch_pinned(url,tmp,size,expected)
        if tmp.stat().st_size!=size or (expected and sha(tmp)!=expected):raise RuntimeError('Incomplete encoder file')
        tmp.replace(file)
        print('Pinned encoder file',name,file.stat().st_size,flush=True)
    save(MODEL/'provenance.json',{'source':'https://huggingface.co/microsoft/layoutlmv3-base',
       'revision':REV,'files':{name:sha(MODEL/name) for name in FILES},
       'publisher_weight_sha256_verified':True,'downloaded_language_generation_backbone':False})


def features(pages_path=None, output_dir=None):
    import numpy as np
    import torch
    import transformers
    from transformers import LayoutLMv3Model,LayoutLMv3ImageProcessor
    from PIL import Image
    torch.set_num_threads(2)
    provenance=json.loads((MODEL/'provenance.json').read_text())
    if any(sha(MODEL/name)!=digest for name,digest in provenance['files'].items()):
        raise RuntimeError('Frozen encoder files differ')
    model=LayoutLMv3Model.from_pretrained(MODEL,local_files_only=True,trust_remote_code=False).eval()
    processor=LayoutLMv3ImageProcessor.from_pretrained(MODEL,local_files_only=True,apply_ocr=False)
    dest=Path(output_dir) if output_dir else OUT/'common_perception/visual_features';dest.mkdir(parents=True,exist_ok=True)
    pages_file=Path(pages_path) if pages_path else OUT/'public/pages.json'
    pages=json.loads(pages_file.read_text());records=[]
    for i,page in enumerate(sorted(pages,key=lambda p:p['page_id']),1):
        file=dest/(page['page_id']+'.npy');mp=file.with_suffix('.json')
        if file.exists() and mp.exists():
            meta=json.loads(mp.read_text())
            if meta['image_sha256']!=page['image_sha256'] or sha(file)!=meta['features_sha256']:
                raise RuntimeError('Cached image/feature mismatch')
            records.append(meta);continue
        image=OUT/'public'/page['image']
        if sha(image)!=page['image_sha256']:raise RuntimeError('Frozen public page changed')
        started=time.monotonic()
        with Image.open(image) as im:inputs=processor(images=im.convert('RGB'),return_tensors='pt')
        with torch.no_grad():array=model(pixel_values=inputs['pixel_values']).last_hidden_state[0].cpu().numpy()
        if array.shape!=(197,768) or not np.isfinite(array).all():raise RuntimeError('Invalid visual patch grid')
        np.save(file,array.astype('float32'))
        meta={'page_id':page['page_id'],'image_sha256':page['image_sha256'],'features_sha256':sha(file),
              'grid':[14,14],'cls_tokens':1,'hidden':768,'encoder_revision':REV,
              'shared_all_methods':True,'seconds':time.monotonic()-started}
        save(mp,meta);records.append(meta);print('Shared frozen patch features',i,'/',len(pages),flush=True)
    save(dest/'manifest.json',{'pages':records,'encoder':provenance,'torch':torch.__version__,
        'transformers':transformers.__version__,'device':'cpu','image_input_resized_to':[224,224],
        'semantic_scope_reference_files_read':False,'original_pixels_modified':False,
        'development_only':True,'not_full_Qwen_or_InternVL_reproduction':True,
        'public_images_manifest_sha256':sha(pages_file),
        'code_sha256':sha(__file__)})


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('fetch','features'))
    parser.add_argument('--pages-manifest',type=Path);parser.add_argument('--output-dir',type=Path)
    args=parser.parse_args();fetch() if args.stage=='fetch' else features(args.pages_manifest,args.output_dir)
