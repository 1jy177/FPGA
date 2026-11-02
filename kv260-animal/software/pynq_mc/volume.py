"""VXL4 / VXL4Z: uint16 little-endian voxels, physical geometry, streaming CRC.

VXL4（原始，未压缩）
--------------------
  0..127   128 字节头：magic/version/size/w/h/d/dtype，几何，payload 长度，CRC32
  128..    payload = w*h*d*2 字节的 uint16 小端

VXL4Z（压缩，本项目新增）
-------------------------
  0..127   与 VXL4 完全相同的 128 字节头（payload 长度和 CRC32 都指**解压后**的数据）
  128..135 uint64 压缩流字节数
  136..    zlib（deflate）流，解压后得到与 VXL4 逐字节相同的 payload

为什么加 VXL4Z
--------------
KV260 端到端耗时实测显示 input（读取+校验体数据）占 69~93%，而 FPGA 扫描只占
5~40%。医学体数据高度冗余：body 有 98.8%、skeleton 有 99.8% 的体素是同一个值，
zlib level 1 即可达到 14~17 倍压缩。因此把「读 120 MB」变成「读 8 MB + 解压」。

校验语义不变：header 里的 CRC32 作用于解压后的 payload，expected_sha256 仍然
是对 header+payload 的 SHA256，所以 dataset.json 里的 sha256 一个字节都不用改。

用法
----
    header, voxels = volume.load('data/digimouse/body.vxl4.bin')
    # 若同目录存在 body.vxl4z，会自动优先使用它，无需改任何调用方。
"""
import struct
import zlib
import hashlib
import mmap
import os
from pathlib import Path
import numpy as np

MAGIC = 0x344c5856          # "VXL4"
VERSION = 1
HEADER_SIZE = 128
DTYPE_UINT16 = 2
VXLZ_EXT = ".vxl4z"


def release_read_pages(voxels):
    """Keep file mappings, but bound resident input pages on a 4 GB board.

    This does not remove the file's OS page-cache data or modify any voxels.
    All PL transfers have copied their input before this advisory call.
    """
    mapping=getattr(voxels,'_mmap',None)
    if mapping is not None and hasattr(mapping,'madvise') and hasattr(mmap,'MADV_DONTNEED'):
        mapping.madvise(mmap.MADV_DONTNEED)


def _parse_header(header):
    """校验 128 字节头，返回 (shape(d,h,w), payload_bytes, crc32)。"""
    if len(header) != HEADER_SIZE:
        raise ValueError("truncated VXL4 header")
    magic, version, size, w, h, d, dtype = struct.unpack_from("<IHHIIIH", header)
    if (magic, version, size, dtype) != (MAGIC, VERSION, HEADER_SIZE, DTYPE_UINT16):
        raise ValueError("unsupported VXL4 uint16 format (old VXL3 is not compatible)")
    if not all(2 <= n <= 4096 for n in (w, h, d)):
        raise ValueError("KV260 dimensions must each be 2..4096")
    payload, crc = struct.unpack_from("<QI", header, 88)
    if payload != 2 * w * h * d:
        raise ValueError("VXL4 length mismatch")
    geometry = np.frombuffer(header, dtype="<f4", count=15, offset=24)
    if not np.isfinite(geometry).all() or np.any(geometry[:3] <= 0):
        raise ValueError("invalid spacing/origin/direction")
    if abs(np.linalg.det(geometry[6:].reshape(3, 3))) < 1e-6:
        raise ValueError("singular direction matrix")
    return (d, h, w), payload, crc


def compressed_sibling(path):
    """body.vxl4.bin -> body.vxl4z（不存在则返回 None）。"""
    path = Path(path)
    if not path.name.endswith(".vxl4.bin"):
        return None
    candidate = path.with_suffix("").with_suffix(VXLZ_EXT)
    return candidate if candidate.is_file() else None


def load(path, expected_sha256=None, prefer_compressed=True):
    """读取体数据。

    prefer_compressed=True 时，若存在同名 .vxl4z 就优先用它；否则按原 VXL4 路径。
    返回 (header_bytes, voxels)，voxels 形状 (d, h, w)，dtype uint16 小端。
    """
    path = Path(path)
    # A/B 基准开关：MC_VOLUME_NO_COMPRESS=1 时强制走未压缩的 .vxl4.bin，
    # 用于在同一块板子、同一批数据上做「压缩前 vs 压缩后」的严格对比。
    if prefer_compressed and os.environ.get("MC_VOLUME_NO_COMPRESS") == "1":
        prefer_compressed = False
    if prefer_compressed:
        alt = compressed_sibling(path)
        if alt is not None:
            path = alt
    is_vxlz = path.suffix == VXLZ_EXT

    with path.open("rb") as f:
        header = f.read(HEADER_SIZE)
        shape, payload, crc = _parse_header(header)

        if is_vxlz:
            raw_len = struct.unpack("<Q", f.read(8))[0]
            blob = f.read(raw_len)
            if len(blob) != raw_len:
                raise ValueError("truncated VXL4Z payload")
            data = zlib.decompress(blob)
            if len(data) != payload:
                raise ValueError("VXL4Z decompressed length mismatch")
            if zlib.crc32(data) != crc:
                raise ValueError("VXL4 CRC32 mismatch")
            sha = hashlib.sha256(header)
            sha.update(data)
            if expected_sha256 is not None and sha.hexdigest() != expected_sha256:
                raise ValueError("VXL4 SHA256 mismatch")
            voxels = np.frombuffer(data, dtype="<u2").reshape(shape)
            return header, voxels

        # 原始未压缩路径
        if path.stat().st_size != HEADER_SIZE + payload:
            raise ValueError("VXL4 length mismatch")
        actual = 0
        sha = hashlib.sha256(header) if expected_sha256 is not None else None
        while True:
            block = f.read(1024 * 1024)
            if not block:
                break
            actual = zlib.crc32(block, actual)
            if sha is not None:
                sha.update(block)
        if actual != crc:
            raise ValueError("VXL4 CRC32 mismatch")
        if sha is not None and sha.hexdigest() != expected_sha256:
            raise ValueError("VXL4 SHA256 mismatch")
    return header, np.memmap(str(path), mode="r", dtype="<u2",
                             offset=HEADER_SIZE, shape=shape)


def load_payload(path):
    """只返回原始 payload 字节（打包工具用，不做逐块循环）。"""
    path = Path(path)
    with path.open("rb") as f:
        header = f.read(HEADER_SIZE)
        _shape, payload, _crc = _parse_header(header)
        data = f.read(payload)
    if len(data) != payload:
        raise ValueError("truncated VXL4 payload")
    return header, data


def write_compressed(path, header, payload, level=1):
    """写出 VXL4Z：复用原始 128 字节头 + 压缩流长度 + zlib 流。"""
    if len(header) != HEADER_SIZE:
        raise ValueError("header must be 128 bytes")
    _shape, expected, crc = _parse_header(header)
    if len(payload) != expected:
        raise ValueError("payload length mismatch")
    if zlib.crc32(payload) != crc:
        raise ValueError("payload CRC32 does not match header")
    blob = zlib.compress(payload, level)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(header)
        f.write(struct.pack("<Q", len(blob)))
        f.write(blob)
    return len(blob)


def write(path, voxels, iso, spacing=(1,1,1), origin=(0,0,0),
          direction=(1,0,0,0,1,0,0,0,1), tissue_id=0):
    """Write in bounded chunks; does not create an extra full-volume copy."""
    if voxels.ndim != 3 or not all(2 <= n <= 4096 for n in voxels.shape):
        raise ValueError("volume dimensions must each be 2..4096")
    if not 0 <= int(iso) <= 65535 or int(iso) != iso:
        raise ValueError("iso must be an integer 0..65535")
    if voxels.dtype != np.dtype('<u2'):
        raise ValueError("voxels must be uint16 little-endian")
    d,h,w = voxels.shape
    header = bytearray(128)
    struct.pack_into('<IHHIIIHH', header, 0, MAGIC,1,128,w,h,d,DTYPE_UINT16,tissue_id)
    struct.pack_into('<15f', header, 24, *spacing,*origin,*direction)
    path = Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    crc = 0
    with path.open('wb') as f:
        f.write(header)
        for plane in voxels:
            block = np.ascontiguousarray(plane).tobytes()
            f.write(block); crc = zlib.crc32(block,crc)
        struct.pack_into('<HHQI', header, 84, int(iso),0,w*h*d*2,crc)
        f.seek(0); f.write(header)
    return path


def make_sphere(path, size=40):
    """Generate a demo sphere volume."""
    if not 4 <= size <= 128:
        raise ValueError("demo size must be 4..128")
    z,y,x = np.ogrid[:size,:size,:size]
    radius = np.sqrt((x-(size-1)/2)**2+(y-(size-1)/2)**2+(z-(size-1)/2)**2)
    voxels = np.clip(32768+(size*0.30-radius)*2048, 0, 65535).round().astype('<u2')
    return write(path,voxels,32768)
