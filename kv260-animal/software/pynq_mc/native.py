import ctypes as C
import os
import tempfile
from pathlib import Path
import numpy as np

def library():
    path = Path(__file__).with_name("libmc_mesh.so")
    if not path.exists():
        raise RuntimeError("native library missing; run bash scripts/install.sh with this Python")
    lib = C.CDLL(str(path))
    ptr, size = C.c_void_p, C.c_size_t
    lib.mc_error.restype = C.c_char_p
    lib.mc_create.argtypes = [ptr,size,size,C.c_char_p]
    lib.mc_create.restype = ptr
    lib.mc_destroy.argtypes = [ptr]
    lib.mc_append.argtypes = [ptr,ptr,size]
    for name in ("mc_vertices", "mc_faces", "mc_cache_peak"):
        fn = getattr(lib,name)
        fn.argtypes = [ptr]
        fn.restype = size
    lib.mc_copy.argtypes = [ptr,ptr,ptr]
    lib.mc_write.argtypes = [ptr,C.c_char_p]
    lib.mc_reference.argtypes = [ptr,ptr,C.c_uint,ptr,size,C.POINTER(size)]
    return lib

def checked(lib, status):
    if status != 0:
        raise RuntimeError(lib.mc_error().decode())

class Builder:
    def __init__(self, lib, header, vertices, faces, mesh_path=None):
        self.lib = lib
        self.mesh_path=Path(mesh_path) if mesh_path is not None else None
        if self.mesh_path is not None:self.mesh_path.parent.mkdir(parents=True,exist_ok=True)
        prefix=os.fsencode(self.mesh_path) if self.mesh_path is not None else None
        self.handle = lib.mc_create(C.c_char_p(header), vertices, faces,prefix)
        if not self.handle:
            raise RuntimeError(lib.mc_error().decode())

    def append(self, packets):
        checked(self.lib, self.lib.mc_append(self.handle, packets.ctypes.data, len(packets)))

    def finish(self):
        points = np.empty((self.lib.mc_vertices(self.handle),3), dtype="<f4")
        faces = np.empty((self.lib.mc_faces(self.handle),3), dtype="<u4")
        checked(self.lib, self.lib.mc_copy(self.handle,points.ctypes.data,faces.ctypes.data))
        return points, faces

    def cache_peak(self):
        return self.lib.mc_cache_peak(self.handle)

    def export(self, path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        vertices=self.lib.mc_vertices(self.handle);faces=self.lib.mc_faces(self.handle)
        fd,temporary=tempfile.mkstemp(prefix=path.name+'.partial-',dir=path.parent)
        os.close(fd)
        try:
            checked(self.lib,self.lib.mc_write(self.handle,os.fsencode(temporary)))
            os.replace(temporary,path)
        finally:
            if os.path.exists(temporary):os.unlink(temporary)
        points=np.memmap(str(path),mode='r',dtype='<f4',shape=(vertices,3)) if vertices else np.empty((0,3),dtype='<f4')
        triangles=np.memmap(str(path),mode='r',dtype='<u4',offset=vertices*12,shape=(faces,3)) if faces else np.empty((0,3),dtype='<u4')
        return points,triangles

    def close(self):
        if self.handle:
            self.lib.mc_destroy(self.handle)
            self.handle = None
