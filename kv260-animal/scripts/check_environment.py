#!/usr/bin/env python3
import importlib
import json
import os
import platform
import shutil
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
model=Path('/proc/device-tree/model')
compatible=Path('/proc/device-tree/compatible')
result={'machine':platform.machine(),'model':model.read_bytes().rstrip(b'\0').decode() if model.exists() else None,
        'compatible':compatible.read_bytes().replace(b'\0',b' ').decode() if compatible.exists() else None,
        'python':sys.version,'root':os.geteuid()==0,'g++':shutil.which('g++'),'modules':{},
        'overlay_files':{n:(ROOT/'overlays'/n).is_file() for n in ('mc_kv260.bit','mc_kv260.hwh','manifest.json')}}
for name in ('numpy','pynq'):
    try:result['modules'][name]=getattr(importlib.import_module(name),'__version__','unknown')
    except Exception as e:result['modules'][name]='ERROR: '+str(e)
mem=Path('/proc/meminfo')
if mem.exists():result['memory']=[x for x in mem.read_text().splitlines() if x.startswith(('MemAvailable:','CmaTotal:','CmaFree:'))]
print(json.dumps(result,ensure_ascii=False,indent=2))
print('Requires a KV260 with the official Kria-PYNQ environment; no FPGA programmed by this check.')
identity=((result['model'] or '')+' '+(result['compatible'] or '')).lower()
if (result['machine']!='aarch64' or not any(x in identity for x in ('kv260','k26')) or not result['g++'] or
    not all(result['overlay_files'].values()) or any(v.startswith('ERROR:') for v in result['modules'].values())):sys.exit(1)
if not result['root']:print('If PL access is denied, run using sudo and the absolute Kria-PYNQ Python path.')
