"""Two independent KV260 engines, reusable buffers, deterministic slab assembly."""
import ctypes as C
import hashlib
import json
import platform
import struct
import threading
import time
from pathlib import Path
import numpy as np
from . import native, volume

ABI=0x4b560002
_INFLIGHT_BUFFERS=[]

class Mesh:
    def __init__(self,points,faces,timing):
        self.points,self.faces,self.timing=points,faces,timing
    def save(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('w') as f:
            f.write('# KV260 uint16 Marching Cubes V3\n')
            for first in range(0,len(self.points),65536):
                np.savetxt(f,self.points[first:first+65536],fmt='v %.9g %.9g %.9g')
            for first in range(0,len(self.faces),65536):
                np.savetxt(f,self.faces[first:first+65536].astype(np.uint64)+1,fmt='f %d %d %d')
    def show(self,path='output/model.html'):
        if len(self.faces)>6000000:
            raise ValueError('full mesh is too large for an embedded viewer; use reconstruct_scene.py with a verified display LOD')
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        template=Path(__file__).with_name('viewer.html').read_text()
        before,after=template.split('/*MODEL_DATA*/null')
        with path.open('w') as f:
            f.write(before);f.write('{"points":[')
            for name,values in [('points',self.points),('faces',self.faces)]:
                if name=='faces':f.write('],"faces":[')
                for i in range(0,len(values),2048):
                    if i:f.write(',')
                    f.write(json.dumps(values[i:i+2048].tolist(),allow_nan=False,separators=(',',':'))[1:-1])
            f.write('],"timing":');json.dump(self.timing,f,allow_nan=False);f.write('}');f.write(after)
        return path

class Reconstructor:
    def __init__(self,bit='overlays/mc_kv260.bit',backend='pynq',buffer_mb=64,
                 max_vertices=4000000,max_faces=8000000,timeout=120,engines=2):
        if backend not in ('pynq','cpu'):raise ValueError('backend must be pynq or cpu')
        if not 1<=buffer_mb<=256 or not 0<timeout<=3600:raise ValueError('invalid buffer/timeout')
        if not 1<=max_vertices<=128000000 or not 1<=max_faces<=256000000:raise ValueError('invalid mesh budget')
        if engines not in (1,2):raise ValueError('engines must be 1 or 2')
        self.lib=native.library();self.backend=backend;self.budget=int(buffer_mb*1024*1024)
        self.max_vertices,self.max_faces=max_vertices,max_faces
        self.timeout,self.engines=timeout,engines
        self.lock=threading.Lock();self.poisoned=False;self.retained=[]
        if backend=='pynq':
            if _INFLIGHT_BUFFERS:raise RuntimeError('previous hardware timeout; reboot board')
            identification=' '.join(p.read_bytes().decode(errors='replace').lower()
                for p in (Path('/proc/device-tree/model'),Path('/proc/device-tree/compatible')) if p.exists())
            if platform.machine()!='aarch64' or not any(x in identification for x in ('kv260','k26')):
                raise RuntimeError('KV260/K26 board identification required; overlay was not programmed')
            from pynq import Overlay,allocate
            bit=Path(bit).resolve();manifest=json.loads(bit.with_name('manifest.json').read_text())
            if manifest.get('abi')!=ABI or manifest.get('part')!='xck26-sfvc784-2LV-c' or manifest.get('engines')!=2:
                raise ValueError('incompatible KV260 overlay')
            for p in (bit,bit.with_suffix('.hwh')):
                if hashlib.sha256(p.read_bytes()).hexdigest()!=manifest['sha256'][p.name]:raise ValueError('overlay hash mismatch')
            self.overlay=Overlay(str(bit))
            self.ips=[getattr(self.overlay,'mc_kv260_'+str(i)) for i in range(engines)]
            self.allocate=allocate

    def _cpu(self,header,block,z,iso,capacity,timing):
        payload=np.ascontiguousarray(block);local=bytearray(header)
        struct.pack_into('<I',local,16,len(block));struct.pack_into('<H',local,84,iso)
        out=np.empty((capacity,4),dtype='<u8');n=C.c_size_t();t=time.perf_counter()
        native.checked(self.lib,self.lib.mc_reference(C.c_char_p(bytes(local)),payload.ctypes.data,
            z,out.ctypes.data,capacity,C.byref(n)))
        timing['reference_s']+=time.perf_counter()-t
        return out[:n.value]

    def _slot(self,ip,shape,capacity):
        inp=self.allocate(shape=shape,dtype=np.uint16)
        try:out=self.allocate(shape=(capacity,4),dtype=np.uint64)
        except BaseException:inp.freebuffer();raise
        return {'ip':ip,'inp':inp,'out':out,'running':False}

    def _start(self,slot,block,z,q,capacity,timing):
        t=time.perf_counter();d,h,w=block.shape;rw=(w+7)//8
        inp,out,ip=slot['inp'],slot['out'],slot['ip']
        inp[:d,:,:w]=block
        if rw*8>w:inp[:d,:,w:]=0
        inp.flush();out.flush()
        # K26 DDR allocations can be above 4 GiB. Both halves are mandatory.
        for addr,buf in ((0x10,inp),(0x1c,out)):
            ip.write(addr,int(buf.physical_address)&0xffffffff)
            ip.write(addr+4,int(buf.physical_address)>>32)
        for addr,value in ((0x28,w),(0x30,h),(0x38,d),(0x40,rw),(0x48,z),(0x50,q),(0x58,capacity)):
            ip.write(addr,int(value))
        slot['capacity']=capacity;slot['cells']=(w-1)*(h-1)*(d-1)
        timing['prepare_s']+=time.perf_counter()-t
        slot['started']=time.perf_counter();slot['running']=True
        ip.write(0,1)

    def _finish(self,slot,timing):
        t=time.perf_counter();ip=slot['ip']
        while not ip.read(0)&2:
            if time.perf_counter()-slot['started']>self.timeout:
                raise TimeoutError('KV260 IP timeout; buffers retained, reboot board before reuse')
            time.sleep(0.0001)
        slot['running']=False
        timing['wait_s']+=time.perf_counter()-t
        # Sum of overlapping launch-to-observation durations; not wall time.
        timing['engine_observed_s_sum']+=time.perf_counter()-slot['started']
        if ip.read(0x90)!=ABI:raise RuntimeError('IP ABI mismatch')
        status,count=ip.read(0x70),ip.read(0x60)
        if status or count>slot['capacity']:raise RuntimeError('IP error status=%d count=%d'%(status,count))
        if ip.read(0x80)!=slot['cells']:raise RuntimeError('IP cell count mismatch')
        t=time.perf_counter();slot['out'].invalidate()
        result=np.array(slot['out'][:count],copy=True)
        timing['readback_s']+=time.perf_counter()-t
        return result

    def _release(self,slots):
        for slot in slots:
            buffers=[slot['inp'],slot['out']]
            if slot['running']:
                self.poisoned=True;self.retained.extend(buffers);_INFLIGHT_BUFFERS.extend(buffers)
            else:
                for b in reversed(buffers):b.freebuffer()

    def reconstruct(self,path,iso=None,slab_cells=None,expected_sha256=None,mesh_path=None):
        with self.lock:
            if self.poisoned:raise RuntimeError('session unusable; reboot board')
            begin=time.perf_counter();header,voxels=volume.load(path,expected_sha256)
            value=struct.unpack_from('<H',header,84)[0] if iso is None else float(iso)
            if not np.isfinite(value) or not 0<=value<=65535 or int(value)!=value:
                raise ValueError('iso must be an integer 0..65535 in uint16 input units')
            q=int(value)
            d,h,w=voxels.shape;per_layer=(w-1)*(h-1);rw=(w+7)//8
            step=min(self.budget//(32*per_layer),self.budget//(rw*16*h)-1,64)
            if step<1:raise ValueError('one XY cell layer exceeds buffer budget; increase buffer_mb explicitly')
            if slab_cells is not None:
                if not isinstance(slab_cells,int) or slab_cells<1:raise ValueError('invalid slab_cells')
                step=min(step,slab_cells)
            jobs=[(z,min(d-1,z+step)) for z in range(0,d-1,step)]
            timing={k:0.0 for k in ('prepare_s','wait_s','engine_observed_s_sum','readback_s','reference_s','mesh_s')}
            timing.update(backend=self.backend,engines=self.engines if self.backend=='pynq' else 0,
                          input_s=time.perf_counter()-begin,packets=0,slabs=len(jobs),cells=per_layer*(d-1))
            builder=native.Builder(self.lib,header,self.max_vertices,self.max_faces,mesh_path);slots=[]
            try:
                if self.backend=='pynq':
                    t=time.perf_counter()
                    for ip in self.ips[:min(len(jobs),self.engines)]:
                        slots.append(self._slot(ip,(step+1,h,rw*8),per_layer*step))
                    timing['allocate_s']=time.perf_counter()-t
                    for i,slot in enumerate(slots):
                        z,end=jobs[i];self._start(slot,voxels[z:end+1],z,q,per_layer*(end-z),timing)
                for i,(z,end) in enumerate(jobs):
                    if self.backend=='cpu':packets=self._cpu(header,voxels[z:end+1],z,q,per_layer*(end-z),timing)
                    else:
                        slot=slots[i%len(slots)];packets=self._finish(slot,timing)
                        following=i+len(slots)
                        if following<len(jobs):
                            z2,end2=jobs[following]
                            self._start(slot,voxels[z2:end2+1],z2,q,per_layer*(end2-z2),timing)
                    # Launch the next hardware slab before ARM mesh assembly.
                    t=time.perf_counter();builder.append(packets);timing['mesh_s']+=time.perf_counter()-t
                    timing['packets']+=len(packets)
                    volume.release_read_pages(voxels)
                t=time.perf_counter()
                points,faces=builder.finish() if mesh_path is None else builder.export(mesh_path)
                timing['mesh_s']+=time.perf_counter()-t
                timing['mesh_cache_peak_vertices']=builder.cache_peak()
                timing['mesh_file_backed']=mesh_path is not None
                timing['mesh_streamed']=mesh_path is not None
            finally:
                self._release(slots);builder.close()
            timing['total_s']=time.perf_counter()-begin
            return Mesh(points,faces,timing)
