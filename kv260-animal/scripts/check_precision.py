#!/usr/bin/env python3
"""Analytic sphere quality: uint16 field versus the same field quantized to 8b."""
import argparse
import struct
import sys
from pathlib import Path
import numpy as np
from utils import write_json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'software'))
from pynq_mc import Reconstructor
from pynq_mc.volume import make_sphere,load,write

def main():
    p=argparse.ArgumentParser();p.add_argument('--work',type=Path,required=True);a=p.parse_args()
    a.work.mkdir(parents=True,exist_ok=True)
    path=make_sphere(a.work/'sphere16.bin',65);header,field=load(path)
    quant=(np.asarray(field)>>8)<<8;write(a.work/'sphere8.bin',quant.astype('<u2'),32768)
    engine=Reconstructor(backend='cpu')
    results={}
    for name,file in [('uint16',path),('quantized8',a.work/'sphere8.bin')]:
        mesh=engine.reconstruct(file,slab_cells=3)
        residual=np.linalg.norm(mesh.points.astype('f8')-32,axis=1)-65*.30
        results[name]={'vertices':len(mesh.points),'triangles':len(mesh.faces),
                      'radius_rmse_voxels':float(np.sqrt(np.mean(residual**2))),
                      'radius_max_error_voxels':float(np.max(np.abs(residual)))}
        assert np.isfinite(mesh.points).all() and mesh.faces.max()<len(mesh.points)
        assert not np.any(np.diff(np.sort(mesh.faces,axis=1),axis=1)==0)
    assert results['uint16']['radius_rmse_voxels']<results['quantized8']['radius_rmse_voxels']
    report={'passed':True,'method':'analytic sphere, same sampled field; no mesh smoothing/decimation',
            'scope':'CPU reference geometry; hardware interpolation independently exercised in precision.cpp and RTL cosim',
            'input_shape':[65]*3,'results':results,
            'rmse_ratio_8bit_over_16bit':results['quantized8']['radius_rmse_voxels']/results['uint16']['radius_rmse_voxels']}
    write_json(a.work/'sphere_precision.json',report);print(report)
if __name__=='__main__':main()
