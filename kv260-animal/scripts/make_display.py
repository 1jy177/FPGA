#!/usr/bin/env python3
"""Offline display LOD only. The complete reconstruction is never modified.

VTK is a workstation dependency, not a dependency of the PYNQ runtime.
"""
import argparse
import copy
import json
import shutil
from pathlib import Path
import numpy as np
import vtk
from vtk.util.numpy_support import numpy_to_vtk, vtk_to_numpy
from utils import digest, write_json
ROOT=Path(__file__).resolve().parents[1]

def simplify(path,meta,output,longest):
    points=np.memmap(path,mode='r',dtype='<f4',shape=(meta['vertices'],3))
    faces=np.memmap(path,mode='r',dtype='<u4',offset=meta['vertices']*12,shape=(meta['triangles'],3))
    vp=vtk.vtkPoints();vp.SetData(numpy_to_vtk(points,deep=False))
    # Signed int32 is safe under the enforced 128M-vertex mesh budget.
    connectivity=numpy_to_vtk(faces.reshape(-1).view(np.int32),deep=False)
    cells=vtk.vtkCellArray();cells.SetData(3,connectivity)
    poly=vtk.vtkPolyData();poly.SetPoints(vp);poly.SetPolys(cells)
    lo,hi=np.asarray(meta['bounds']);extent=hi-lo
    divisions=np.maximum(2,np.ceil(extent/extent.max()*longest)).astype(int)
    lod=vtk.vtkQuadricClustering();lod.SetInputData(poly)
    lod.SetNumberOfDivisions(*divisions.tolist());lod.UseInputPointsOn()
    lod.CopyCellDataOff();lod.Update()
    result=lod.GetOutput()
    p=vtk_to_numpy(result.GetPoints().GetData()).astype('<f4',copy=False)
    conn=vtk_to_numpy(result.GetPolys().GetConnectivityArray()).reshape(-1,3)
    # Remove any collapsed triangles. Clustering cannot alter the full mesh.
    keep=(conn[:,0]!=conn[:,1])&(conn[:,0]!=conn[:,2])&(conn[:,1]!=conn[:,2])
    f=conn[keep].astype('<u4')
    if not len(p) or not len(f):raise ValueError('empty display mesh')
    if not np.isfinite(p).all() or f.max()>=len(p):raise ValueError('invalid display mesh')
    with output.open('wb') as stream:p.tofile(stream);f.tofile(stream)
    return {'file':output.name,'vertices':len(p),'triangles':len(f),'sha256':digest(output),
        'bounds':[p.min(axis=0).tolist(),p.max(axis=0).tolist()],
        'method':'vtkQuadricClustering UseInputPointsOn; display only',
        'divisions':divisions.tolist(),
        'cluster_diagonal_mm':float(np.linalg.norm(extent/divisions)),
        'error_note':'Cluster diagonal is a within-cluster vertex displacement bound, not a surface Hausdorff bound. Small features may disappear.'}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--scene',type=Path,required=True)
    parser.add_argument('--body-divisions',type=int,default=480)
    parser.add_argument('--internal-divisions',type=int,default=240)
    parser.add_argument('--skeleton-divisions',type=int,default=700)
    parser.add_argument('--preserve-small',type=int,default=200000,
                        help='keep small anatomical meshes exact, especially glands')
    parser.add_argument('--data',type=Path,required=True,help='dataset folder, for input SHA256 provenance')
    a=parser.parse_args();scene=json.loads(a.scene.read_text());base=a.scene.parent
    dataset=json.loads((a.data/'dataset.json').read_text())
    for layer in scene['layers']:
        layer['input_sha256']=next(x['sha256'] for x in dataset['layers'] if x['id']==layer['id'])
        full=copy.deepcopy(layer.get('full') or {k:layer[k] for k in ('file','vertices','triangles','sha256','bounds')})
        path=base/full['file']
        if digest(path)!=full['sha256']:raise ValueError('full mesh hash mismatch')
        target=base/(layer['id']+'.display.mesh')
        if full['triangles']<=a.preserve_small:
            shutil.copyfile(path,target)
            display=dict(full,file=target.name,method='exact full mesh; small anatomy retained',
                         cluster_diagonal_mm=0,error_note='No display simplification.')
        else:
            divisions=(a.body_divisions if layer['id']=='body' else
                       a.skeleton_divisions if layer['id']=='skeleton' else a.internal_divisions)
            display=simplify(path,full,target,divisions)
        layer.update({k:display[k] for k in ('file','vertices','triangles','sha256','bounds')})
        layer['full']=full;layer['display']=display
        print(layer['id'],full['triangles'],'full triangles ->',display['triangles'],'display triangles',flush=True)
    scene['vertices']=sum(x['full']['vertices'] for x in scene['layers'])
    scene['triangles']=sum(x['full']['triangles'] for x in scene['layers'])
    scene['display_triangles']=sum(x['triangles'] for x in scene['layers'])
    scene['display_note']='Display LOD only. Complete uint16 MC meshes are retained without mesh smoothing or decimation.'
    write_json(a.scene,scene)
    shutil.copyfile(ROOT/'software/pynq_mc/scene.html',base/'index.html')
if __name__=='__main__':main()
