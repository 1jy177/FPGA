"""Exercise dual-core scheduling, >4 GiB addresses, cache and fault handling."""
import struct
import sys
import tempfile
import unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'software'))
from pynq_mc import Reconstructor
from pynq_mc import volume,runtime

class Buffer(np.ndarray):
    def __new__(cls,shape,dtype,address):
        b=np.zeros(shape,dtype=dtype).view(cls);b.physical_address=address
        b.cleaned=False;b.invalidated=False;b.freed=False
        return b
    def flush(self):self.cleaned=True
    def invalidate(self):self.invalidated=True
    def freebuffer(self):self.freed=True

class IP:
    def __init__(self,owner,index):self.owner=owner;self.index=index;self.regs={};self.running=False
    def write(self,addr,value):
        self.regs[addr]=value
        if addr!=0:return
        o=self.owner;r=self.regs
        inp=o.buffers[r[0x10]+(r[0x14]<<32)];out=o.buffers[r[0x1c]+(r[0x20]<<32)]
        assert inp.cleaned and out.cleaned
        w,h,d=r[0x28],r[0x30],r[0x38]
        data=o.engine._cpu(o.header,inp[:d,:h,:w],r[0x48],r[0x50],r[0x58],{'reference_s':0.0})
        out[:len(data)]=data
        r.update({0x60:len(data),0x70:o.status,0x80:(w-1)*(h-1)*(d-1),0x90:runtime.ABI})
        self.running=True;o.events.append(('start',self.index,r[0x48]))
    def read(self,addr):
        if addr==0:
            if self.owner.timeout:return 0
            self.owner.events.append(('done',self.index,self.regs[0x48]));self.running=False
            return 2
        return self.regs[addr]

class DriverTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=volume.make_sphere(Path(self.temp.name)/'sphere.bin',17)
        self.header,_=volume.load(self.path);self.buffers={};self.events=[];self.status=0;self.timeout=False
        self.engine=Reconstructor(backend='cpu',timeout=.002)
        self.expected=self.engine.reconstruct(self.path,32657,slab_cells=1)
        self.engine.backend='pynq';self.engine.ips=[IP(self,0),IP(self,1)]
        def allocate(shape,dtype):
            address=0x100000000+len(self.buffers)*0x100000
            b=Buffer(shape,dtype,address);self.buffers[address]=b;return b
        self.engine.allocate=allocate
    def tearDown(self):
        runtime._INFLIGHT_BUFFERS.clear();self.temp.cleanup()
    def test_reuse_dual_launch_addresses_and_geometry(self):
        actual=self.engine.reconstruct(self.path,32657,slab_cells=3)
        np.testing.assert_array_equal(actual.points,self.expected.points)
        np.testing.assert_array_equal(actual.faces,self.expected.faces)
        self.assertEqual(len(self.buffers),4)
        self.assertEqual(self.events[:2],[('start',0,0),('start',1,3)])
        self.assertEqual([e[2] for e in self.events if e[0]=='done'],[0,3,6,9,12,15])
        self.assertTrue(all(b.freed for b in self.buffers.values()))
        self.assertTrue(all(b.invalidated for b in list(self.buffers.values())[1::2]))
        # A second reconstruction must also be valid.
        again=self.engine.reconstruct(self.path,32657,slab_cells=7)
        np.testing.assert_array_equal(again.faces,self.expected.faces)
    def test_timeout_retains_both_active_engines(self):
        self.timeout=True
        with self.assertRaises(TimeoutError):self.engine.reconstruct(self.path,slab_cells=2)
        self.assertTrue(self.engine.poisoned)
        self.assertEqual(len(runtime._INFLIGHT_BUFFERS),4)
        self.assertTrue(all(not b.freed for b in self.buffers.values()))
        with self.assertRaises(RuntimeError):self.engine.reconstruct(self.path)
    def test_completed_error_retains_other_running_engine(self):
        self.status=3
        with self.assertRaisesRegex(RuntimeError,'IP error'):self.engine.reconstruct(self.path,slab_cells=2)
        self.assertEqual(sum(b.freed for b in self.buffers.values()),2)
        self.assertEqual(len(runtime._INFLIGHT_BUFFERS),2)
    def test_single_engine(self):
        self.engine.engines=1;self.engine.ips=self.engine.ips[:1]
        actual=self.engine.reconstruct(self.path,32657,slab_cells=4)
        np.testing.assert_array_equal(actual.faces,self.expected.faces)
        self.assertEqual(len(self.buffers),2)
    def test_allocation_failure_releases_previous_slot(self):
        allocate=self.engine.allocate
        def fail(shape,dtype):
            if len(self.buffers)==3:raise MemoryError('CMA exhausted')
            return allocate(shape,dtype)
        self.engine.allocate=fail
        with self.assertRaises(MemoryError):self.engine.reconstruct(self.path,slab_cells=2)
        self.assertTrue(all(b.freed for b in self.buffers.values()))

if __name__=='__main__':unittest.main()
