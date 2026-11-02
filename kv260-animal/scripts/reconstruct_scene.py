#!/usr/bin/env python3
"""One dataset manifest drives both PYNQ reconstruction and browser viewing."""
import argparse
import json
import shutil
import sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'software'))
from pynq_mc import Reconstructor
from utils import digest

def verified_display(reference,meta,full,output):
    """Reuse only the LOD of the exact same input AND full reconstructed mesh.

    No VTK is needed on ARM. A mismatch stops execution, never displays a stale
    CPU reference in place of a hardware result.
    """
    scene=json.loads((reference/'scene.json').read_text())
    layer=next(x for x in scene['layers'] if x['id']==meta['id'])
    expected=layer.get('full')
    if not expected or 'display' not in layer:raise ValueError('reference has no display LOD')
    if layer['input_sha256']!=meta['sha256'] or expected['sha256']!=full['sha256']:
        raise ValueError('full reconstruction differs from display reference; inspect numerical output before making a new LOD')
    display=layer['display'];source=reference/display['file']
    if Path(display['file']).name!=display['file'] or digest(source)!=display['sha256']:
        raise ValueError('invalid display reference path/hash')
    destination=output/display['file']
    if source.resolve()!=destination.resolve():shutil.copyfile(source,destination)
    return dict(display)

def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=ROOT/'data/digimouse')
    p.add_argument('--output',type=Path,default=ROOT/'output/digimouse');p.add_argument('--backend',choices=['cpu','pynq'],default='pynq')
    p.add_argument('--buffer-mb',type=int,default=32)
    p.add_argument('--max-vertices',type=int,default=128000000);p.add_argument('--max-faces',type=int,default=256000000)
    p.add_argument('--engines',type=int,choices=[1,2],default=2);p.add_argument('--export-obj',action='store_true')
    p.add_argument('--display-reference',type=Path,help='precomputed LOD, accepted only after exact full-mesh SHA256 match')
    p.add_argument('--full-only',action='store_true',help='offline reference generation, before make_display.py')
    a=p.parse_args();dataset=json.loads((a.data/'dataset.json').read_text());a.output.mkdir(parents=True,exist_ok=True)
    reference=a.display_reference or ROOT/'preview'/dataset['profile']
    if not a.full_only and not (reference/'scene.json').is_file():
        raise ValueError('display reference missing; offline generation requires --full-only then make_display.py')
    engine=Reconstructor(ROOT/'overlays/mc_kv260.bit',backend=a.backend,engines=a.engines,
                        buffer_mb=a.buffer_mb,max_vertices=a.max_vertices,max_faces=a.max_faces)
    layers=[]
    for meta in dataset['layers']:
        source=a.data/meta['input']
        binary=a.output/(meta['id']+'.mesh')
        mesh=engine.reconstruct(source,expected_sha256=meta['sha256'],mesh_path=binary)
        if not len(mesh.points):raise ValueError('empty tissue '+meta['id'])
        if a.export_obj:mesh.save(a.output/(meta['id']+'.obj'))
        full=dict(vertices=len(mesh.points),triangles=len(mesh.faces),file=binary.name,
            sha256=digest(binary),bounds=[mesh.points.min(axis=0).tolist(),mesh.points.max(axis=0).tolist()])
        layer=dict(meta,**full,timing=mesh.timing,input_sha256=meta['sha256'])
        if not a.full_only:
            display=verified_display(reference,meta,full,a.output)
            layer.update({k:display[k] for k in ('file','vertices','triangles','sha256','bounds')})
            layer.update(full=full,display=display,display_verified_against_full=True)
        layers.append(layer)
        print('%s: %d vertices, %d triangles, %.3f s'%(meta['label'],len(mesh.points),len(mesh.faces),mesh.timing['total_s']),flush=True)
        del mesh
    manifest={'version':2,'title':dataset['title'],'source':dataset['source'],'note':dataset['note'],
        'profile':dataset['profile'],'board_tested':a.backend=='pynq',
        'backend':a.backend,'layers':layers,'vertices':sum(x.get('full',x)['vertices'] for x in layers),
        'triangles':sum(x.get('full',x)['triangles'] for x in layers),
        'display_triangles':sum(x['triangles'] for x in layers),
        'reconstruction_s':sum(x['timing']['total_s'] for x in layers),
        'obj_available':a.export_obj}
    for key in ('profile_label','default_preset','camera'):
        if key in dataset:manifest[key]=dataset[key]
    (a.output/'scene.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    shutil.copyfile(ROOT/'software/pynq_mc/scene.html',a.output/'index.html')
    print(json.dumps({k:v for k,v in manifest.items() if k!='layers'},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
