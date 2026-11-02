#!/usr/bin/env python3
"""Actual KV260 one/two-core correctness and end-to-end speed comparison."""
import argparse
import json
import sys
import time
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'software'))
from pynq_mc import Reconstructor
from pynq_mc.volume import make_sphere

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,default=ROOT/'data/digimouse/board_roi.vxl4.bin')
    p.add_argument('--output',type=Path,default=ROOT/'output/board_report.json');a=p.parse_args()
    report={'passed':False,'board_tested':False,'time_unix':time.time(),'tests':[]}
    sphere=make_sphere(ROOT/'output/test_sphere.bin',33)
    cpu=Reconstructor(backend='cpu')
    try:
        hw=Reconstructor(ROOT/'overlays/mc_kv260.bit')
        report['board_tested']=True
        for path,iso in [(sphere,32657),(sphere,0),(sphere,65535),(a.input,None),(a.input,None)]:
            reference=cpu.reconstruct(path,iso)
            timings={}
            for n in (1,2):
                hw.engines=n
                actual=hw.reconstruct(path,iso)
                np.testing.assert_array_equal(actual.faces,reference.faces)
                np.testing.assert_array_equal(actual.points,reference.points)
                timings[str(n)]=actual.timing
            report['tests'].append({'input':path.name,'iso':iso,'vertices':len(reference.points),
                'triangles':len(reference.faces),'cpu':reference.timing,'pynq':timings,
                'dual_vs_single_speedup':timings['1']['total_s']/timings['2']['total_s'],
                'dual_vs_cpu_speedup':reference.timing['total_s']/timings['2']['total_s']})
        report['passed']=True
    except BaseException as e:
        report['error']=str(e);raise
    finally:
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
if __name__=='__main__':main()
