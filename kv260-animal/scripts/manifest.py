#!/usr/bin/env python3
"""Validate HWH ABI/addressing, then bind the bitstream and handoff by hashes."""
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
root=Path(__file__).resolve().parents[1]
tree=ET.parse(root/'overlays/mc_kv260.hwh')
modules={m.get('INSTANCE'):m for m in tree.findall('.//MODULE')}
for name in ('mc_kv260_0','mc_kv260_1'):
    m=modules[name]
    params={p.get('NAME'):p.get('VALUE') for p in m.findall('./PARAMETERS/PARAMETER')}
    for bus in ('INPUT_MEM','OUTPUT_MEM'):
        assert params['C_M_AXI_'+bus+'_ADDR_WIDTH']=='64'
    assert params['C_M_AXI_INPUT_MEM_DATA_WIDTH']=='128'
    assert params['C_M_AXI_OUTPUT_MEM_DATA_WIDTH']=='256'
    regs={r.get('NAME'):r.find("./PROPERTY[@NAME='ADDRESS_OFFSET']").get('VALUE') for r in m.findall('.//REGISTER')}
    # HWH represents offsets as decimal or hex, depending on the exporter.
    for name,offset in [('volume_1',0x10),('volume_2',0x14),('packets_1',0x1c),('packets_2',0x20),
                        ('width',0x28),('height',0x30),('depth',0x38),('row_words',0x40),
                        ('z_offset',0x48),('iso_value',0x50),('capacity',0x58),('count',0x60),
                        ('status',0x70),('processed',0x80),('abi',0x90)]:
        assert int(regs[name],0)==offset,(name,regs)
    assert any('DDR_HIGH' in r.get('ADDRESSBLOCK','') for r in m.findall('.//MEMRANGE'))
reset={p.get('NAME'):p.get('VALUE') for p in modules['reset'].findall('./PARAMETERS/PARAMETER')}
assert reset['C_EXT_RESET_HIGH']=='0'
files=[root/'overlays'/name for name in ('mc_kv260.bit','mc_kv260.hwh')]
result={'abi':0x4b560002,'part':'xck26-sfvc784-2LV-c','clock_mhz':200,'engines':2,
    'scan_ii':1,'input_axi_bits':128,'output_axi_bits':256,'address_bits':64,
    'tools':'Vivado/Vitis HLS 2022.2.2','board_tested':False,
    'voxel_bits':16,'volume_format':'VXL4','packet_version':3,
    'edge_fraction':'UNORM16 round-to-nearest; decode q/65535',
    'max_dimension':4096,
    'kernel_max_slab_depth':65,
    'sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
(root/'overlays/manifest.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
