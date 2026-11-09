"""Pinned community quantized derivatives of the two planned visual families.

These are explicit resource-constrained precision tracks, not official BF16
checkpoint reproductions. Every within-backbone method uses the same files.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

from prepare import EXP,OUT,save,sha
from download import fetch

CONFIG={
 'qwen':('mlx-community/Qwen3.5-9B-MLX-4bit','938d8919941c6e7efd3c7150eff7fe9d12afa631',
         OUT/'qwen_mlx_pinned_metadata.json','Qwen/Qwen3.5-9B','MLX affine 4-bit group 64'),
 'internvl':('lmstudio-community/InternVL3_5-8B-GGUF',None,
         OUT/'internvl_gguf_repository.json','OpenGVLab/InternVL3_5-8B','GGUF Q4_K_M; F16 visual projector')}
ROOT=EXP/'ext/cvpr_public_v9/backbones'


def selected_files(kind,metadata):
    if kind=='internvl':
        names={'InternVL3_5-8B-Q4_K_M.gguf','mmproj-model-f16.gguf','README.md'}
    else:
        names={f['rfilename'] for f in metadata['siblings'] if '/' not in f['rfilename']
               and f['rfilename'].endswith(('.json','.jinja','.safetensors','.md'))}
    entries={f['rfilename']:f for f in metadata['siblings']}
    if not names.issubset(entries):raise RuntimeError('Requested quantized precision files missing')
    return [entries[name] for name in sorted(names)]


def run(kind):
    repo,revision,metadata_file,original,precision=CONFIG[kind]
    meta=json.loads(metadata_file.read_text());revision=revision or meta['sha']
    if meta.get('id')!=repo or meta['sha']!=revision:raise RuntimeError('Backbone metadata revision differs')
    base=meta.get('cardData',{}).get('base_model')
    if isinstance(base,list):base=base[0] if len(base)==1 else base
    if base!=original:raise RuntimeError('Quantized derivative is not the planned instruction model')
    entries=selected_files(kind,meta);dest=ROOT/kind/revision;dest.mkdir(parents=True,exist_ok=True)
    total=sum(e['size'] for e in entries);missing=sum(e['size']-(dest/e['rfilename']).stat().st_size if (dest/e['rfilename']).exists() else e['size'] for e in entries)
    if shutil.disk_usage(dest).free<missing+5*1024**3:raise RuntimeError('Backbone download would exhaust disk reserve')
    design={'repo':repo,'revision':revision,'original_model':original,'precision':precision,'bytes':total,
       'source_metadata_sha256':sha(metadata_file),'official_BF16_reproduction':False,
       'native_model_family_preserved':True,'all_methods_same_files_and_precision':True}
    save(dest/'download_design.json',design);manifest={}
    for e in entries:
        name=e['rfilename'];file=dest/name;expected=e.get('lfs',{}).get('sha256')
        if file.exists():
            if file.stat().st_size!=e['size'] or expected and sha(file)!=expected:raise RuntimeError('Frozen backbone file differs')
        else:
            tmp=file.with_suffix(file.suffix+'.partial');url=f'https://huggingface.co/{repo}/resolve/{revision}/{name}'
            if e.get('lfs'):fetch(url,tmp,e['size'],expected)
            else:subprocess.run(['curl','-fL','-sS','--max-time','90',url,'-o',str(tmp)],check=True)
            if tmp.stat().st_size!=e['size']:raise RuntimeError('Incomplete pinned backbone artifact')
            tmp.replace(file)
        manifest[name]={'sha256':sha(file),'bytes':file.stat().st_size,'publisher_LFS_hash_verified':bool(expected)}
        save(dest/'download_progress.json',{'completed_files':manifest,'expected_files':len(entries)})
    save(dest/'provenance.json',{**design,'files':manifest,'code_sha256':sha(__file__),
       'conversion_recipe_from_model_publisher_card':True,'model_generation_verified':False})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--backbone',choices=CONFIG,required=True);run(p.parse_args().backbone)
