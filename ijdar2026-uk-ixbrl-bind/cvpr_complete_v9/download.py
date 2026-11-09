"""Bounded, exact-range downloads of pinned public artifacts."""
import argparse
import hashlib
from pathlib import Path
import re
import time
import requests


def check_range(status,content_range,start,stop,total,length):
    expected=f'bytes {start}-{stop-1}/{total}'
    if status!=206 or content_range!=expected or length!=stop-start:
        raise ValueError('Server byte range or length differs from requested pinned artifact')


def fetch(url,path,total,expected_sha=None,chunk_bytes=8*1024**2):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    offset=path.stat().st_size if path.exists() else 0
    if offset>total:raise RuntimeError('Existing partial exceeds pinned artifact size')
    session=requests.Session()
    try:
        while offset<total:
            stop=min(offset+chunk_bytes,total)
            for attempt in range(2):
                try:
                    reply=session.get(url,headers={'Range':f'bytes={offset}-{stop-1}'},timeout=(20,90))
                    reply.raise_for_status();body=reply.content
                    check_range(reply.status_code,reply.headers.get('Content-Range'),offset,stop,total,len(body))
                    with path.open('ab') as file:file.write(body)
                    offset=stop;print('Validated download bytes',offset,'/',total,flush=True);break
                except (requests.RequestException,ValueError):
                    if attempt==1:raise
                    time.sleep(2)
        if expected_sha:
            digest=hashlib.sha256()
            with path.open('rb') as file:
                for block in iter(lambda:file.read(8*1024**2),b''):digest.update(block)
            if digest.hexdigest()!=expected_sha:raise RuntimeError('Complete artifact SHA-256 differs from publisher')
    finally:session.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--url',required=True);p.add_argument('--path',type=Path,required=True)
    p.add_argument('--bytes',type=int,required=True);p.add_argument('--sha256');args=p.parse_args()
    fetch(args.url,args.path,args.bytes,args.sha256)
