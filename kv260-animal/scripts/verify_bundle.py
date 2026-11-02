#!/usr/bin/env python3
"""Verify distribution checksums without allocating a whole-volume buffer."""
import argparse
import json
from pathlib import Path
from utils import digest
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--scope',choices=['all','runtime','preview'],default='all')
    p.add_argument('--profile',choices=['digimouse','quick','native'],default='digimouse');a=p.parse_args()
    manifest=ROOT/'SHA256.json'
    if not manifest.is_file():
        print('Developer checkout: no release SHA256.json; checking overlay and selected scene only.')
        hashes={}
        if (ROOT/'overlays/manifest.json').exists():
            for name,value in json.loads((ROOT/'overlays/manifest.json').read_text())['sha256'].items():hashes['overlays/'+name]=value
        scene=ROOT/'preview'/a.profile/'scene.json'
        if scene.exists():
            for layer in json.loads(scene.read_text())['layers']:hashes['preview/'+a.profile+'/'+layer['file']]=layer['sha256']
        if not hashes:raise SystemExit('No release artifacts available; prepare/build the project first.')
    else:hashes=json.loads(manifest.read_text())
    count=0
    for name,expected in hashes.items():
        if a.scope!='all':
            # Input SHA256+CRC are checked in one streamed read at reconstruction
            # time. Do not hash all BIN inputs again before every launch.
            if name.startswith(('hardware/project/','data/original/')):continue
            if name.startswith('data/') and not name.endswith(('.json','.txt')):continue
            if name.startswith('preview/') and not name.startswith('preview/'+a.profile+'/') and name!='preview/index.html':continue
            if name.startswith('preview/') and name.endswith('.mesh') and not name.endswith('.display.mesh'):continue
        path=ROOT/name
        if Path(name).is_absolute() or '..' in Path(name).parts or not path.is_file():raise ValueError('invalid/missing package path: '+name)
        if manifest.exists() and ROOT not in path.resolve().parents:raise ValueError('file outside package: '+name)
        if digest(path)!=expected:raise ValueError('SHA256 mismatch: '+name)
        count+=1
    print('PASS:',count,'files checked; scope='+a.scope+'; no FPGA programmed.')
if __name__=='__main__':main()
