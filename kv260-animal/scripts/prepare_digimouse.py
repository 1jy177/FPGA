#!/usr/bin/env python3
"""USC labelled anatomy -> compact, physically aligned VXL4 fields for PS/PL.

All surfaces are reconstructed by the same uint16 Marching Cubes implementation.
No imported mesh, synthetic anatomy, upsampling or new organ segmentation.
"""
import argparse
import json
import re
import shutil
import struct
import sys
import zipfile
from pathlib import Path
import numpy as np
from scipy import ndimage as ndi
from download_digimouse import SOURCE, verify
from utils import digest, write_json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'software'))
from pynq_mc.volume import write

# Source indices are retained in every layer manifest, not inferred by CT threshold.
LAYERS = [
    ('body', '完整体表', None, '#cfced2', 'body', 0.70),
    ('skeleton', '全身骨骼', [2], '#ede4cf', 'skeleton', 0.40),
    ('heart', '心脏', [9], '#d55969', 'organ', 0.65),
    ('lungs', '双肺', [21], '#e7a9b7', 'organ', 0.65),
    ('liver', '肝脏', [18], '#a86057', 'organ', 0.65),
    ('kidneys', '双肾', [19], '#d68b56', 'organ', 0.60),
    ('stomach', '胃', [15], '#e6c089', 'organ', 0.65),
    ('spleen', '脾脏', [16], '#9e6d9f', 'organ', 0.60),
    ('pancreas', '胰腺', [17], '#e9ce83', 'organ', 0.55),
    ('bladder', '膀胱', [13], '#a3cacc', 'organ', 0.60),
    ('adrenals', '肾上腺', [20], '#e5b76c', 'organ', 0.45),
    ('testes', '睾丸', [14], '#d7b5a2', 'organ', 0.60),
    ('brain', '全脑', [4, 5, 6, 7, 8, 10], '#a4bdcd', 'head', 0.65),
    ('eyes', '双眼', [3], '#657e9c', 'head', 0.55),
    ('masseter', '咬肌', [11], '#bd787a', 'head', 0.60),
    ('lacrimal', '泪腺', [12], '#abc3a1', 'head', 0.50),
]


def read_analyze(header, payload):
    """Read the pinned uint8 Analyze 7.5 layout, preserving its storage axes."""
    if len(header) != 348 or struct.unpack_from('<i', header)[0] != 348:
        raise ValueError('expected little-endian Analyze 7.5 header')
    dims = struct.unpack_from('<8h', header, 40)
    if dims[0] not in (3, 4) or min(dims[1:4]) < 2 or any(n not in (0, 1) for n in dims[4:]):
        raise ValueError('unsupported Analyze geometry')
    datatype, bits = struct.unpack_from('<2h', header, 70)
    if (datatype, bits) != (2, 8):
        raise ValueError('expected uint8 Analyze data')
    spacing = np.array(struct.unpack_from('<8f', header, 76)[1:4], dtype=float)
    if not np.isfinite(spacing).all() or np.any(spacing <= 0):
        raise ValueError('invalid Analyze spacing')
    shape = tuple(reversed(dims[1:4]))
    if len(payload) != int(np.prod(shape)):
        raise ValueError('Analyze payload length mismatch')
    return np.frombuffer(payload, dtype=np.uint8).reshape(shape), spacing


def volume_from_zip(path, prefix):
    with zipfile.ZipFile(path) as archive:
        stem = prefix + '_380x992x208'
        return read_analyze(archive.read(stem + '.hdr'), archive.read(stem + '.img'))


def cropped_mask(labels, indices, padding=5):
    mask = labels > 0 if indices is None else np.isin(labels, indices)
    if not mask.any():
        raise ValueError('source anatomical label is empty: ' + str(indices))
    slices = ndi.find_objects(mask, max_label=1)[0]
    start = np.array([s.start for s in slices])
    end = np.array([s.stop for s in slices])
    boundary = any(lo == 0 or hi == size for lo, hi, size in zip(start, end, labels.shape))
    return np.pad(mask[slices], padding), start - padding, int(mask.sum()), boundary


def continuous_field(mask, sigma):
    # Smooth the signed-distance field instead of a binary mesh. This is offline
    # preprocessing; PL still computes all crossing edges from the uint16 field.
    inside = ndi.distance_transform_edt(mask).astype('f4')
    outside = ndi.distance_transform_edt(~mask).astype('f4')
    inside -= outside
    del outside
    ndi.gaussian_filter(inside, sigma, output=inside, mode='nearest')
    inside *= 8192
    inside += 32768
    np.clip(inside, 0, 65535, out=inside)
    return np.rint(inside).astype('<u2')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    verify(args.original)
    labels, spacing = volume_from_zip(args.original / 'atlas.zip', 'atlas/atlas')
    ct, ct_spacing = volume_from_zip(args.original / 'ct_data.zip', 'ct_data/ct')
    if labels.shape != tuple(SOURCE['shape_zyx']) or not np.allclose(spacing, 0.1):
        raise ValueError('upstream atlas geometry changed')
    if ct.shape != labels.shape or not np.array_equal(ct_spacing, spacing):
        raise ValueError('registered CT and atlas are not on the same grid')
    if set(np.unique(labels)) != set(range(22)):
        raise ValueError('unexpected anatomical class indices')
    with zipfile.ZipFile(args.original / 'atlas.zip') as archive:
        mapping = archive.read('atlas/atlas_380x992x208.txt').decode()
    if not re.search(r'9\s*-->\s*heart', mapping) or not re.search(r'21\s*-->\s*lungs', mapping):
        raise ValueError('upstream anatomical label mapping changed')
    args.output.mkdir(parents=True, exist_ok=True)
    source = json.loads((args.original / 'source.json').read_text())
    layers = []
    records = []
    for number, (identifier, label, indices, color, group, sigma) in enumerate(LAYERS, 1):
        mask, start, count, boundary = cropped_mask(labels, indices)
        field = continuous_field(mask, sigma)
        if any(np.any(face >= 32768) for face in
               (field[0], field[-1], field[:, 0], field[:, -1], field[:, :, 0], field[:, :, -1])):
            raise ValueError('open field boundary: ' + identifier)
        origin = (start[::-1] * spacing).tolist()
        path = args.output / (identifier + '.vxl4.bin')
        write(path, field, 32768, spacing=spacing, origin=origin, tissue_id=number)
        description = ('原图谱所有非背景区域的体表。' if indices is None else
                       'Digimouse 解剖标注：' + ', '.join(map(str, indices)) + '。')
        meta = dict(id=identifier, label=label, input=path.name, sha256=digest(path),
                    color=color, opacity=1, group=group, iso=32768,
                    source_indices=indices, shape_zyx=list(field.shape),
                    spacing_mm=spacing.tolist(), origin_mm=origin,
                    description=description, source_voxels=count, smooth_sigma_voxels=sigma,
                    source_touches_boundary=boundary)
        layers.append(meta)
        records.append(dict(id=identifier, voxels=count, shape_zyx=list(field.shape),
                            source_touches_boundary=boundary))
        if identifier == 'heart':
            # A small real anatomical-label crossing for future board CPU/PL tests.
            crossing = (field[:, :, 1:] >= 32768) & (field[:, :, :-1] < 32768)
            z, y, x = np.argwhere(crossing)[len(np.argwhere(crossing)) // 2]
            z = max(2, min(int(z), field.shape[0] - 3))
            y = max(4, min(int(y), field.shape[1] - 5))
            x = max(16, min(int(x) + 1, field.shape[2] - 17))
            roi = np.ascontiguousarray(field[z-2:z+3, y-4:y+5, x-16:x+17])
            write(args.output / 'board_roi.vxl4.bin', roi, 32768, spacing=spacing,
                  origin=np.asarray(origin) + np.array([x-16, y-4, z-2]) * spacing)
        print('prepared', number, identifier, field.shape, 'source voxels', count, flush=True)
        del mask, field
    preparation = dict(source=source, records=records, spacing_mm=spacing.tolist(),
        source_label_mapping=mapping, coordinate_system='Analyze storage axes in mm; not a patient RAS/LPS claim',
        changes=['crop each label with 5-voxel background padding; preserve physical origin',
                 'signed distance inside minus outside in source voxels',
                 'Gaussian signed-distance filtering; per-layer sigma in manifest',
                 'uint16=round(clip(32768+8192*distance,0,65535)); iso=32768',
                 'no source upsampling, no anatomical label generation, no mesh smoothing'],
        limitations=['exact specimen age in weeks not published',
                     'no independent intestine label; source label accuracy limits anatomical detail',
                     'registered uint8 CT is not calibrated raw 16-bit CT or a clinical scan'])
    dataset = dict(version=2, profile='digimouse', title='Digimouse · 全身解剖', source=source,
        profile_label='0.1 mm 图谱网格 · 16 个解剖图层',
        note='28 g 正常雄性裸鼠，非胚胎。真实 CT 与冷冻切片配准图谱；具体周龄未公开。',
        default_preset='combined', camera=dict(yaw=0.60, pitch=0.03),
        layers=layers, preparation=preparation)
    write_json(args.output / 'dataset.json', dataset)
    write_json(args.output / 'source.json', source)
    write_json(args.output / 'preparation.json', preparation)
    shutil.copyfile(args.original / 'LICENSE-Digimouse.txt', args.output / 'LICENSE-DATA.txt')
    (args.output / 'ATTRIBUTION.txt').write_text(
        SOURCE['title'] + '\n' + SOURCE['author'] + '\nDOI: ' + SOURCE['doi'] +
        '\n' + SOURCE['url'] + '\nCC BY-SA 4.0: ' + SOURCE['license_url'] +
        '\nDerived fields and meshes: cropped labels, signed-distance filtering, uint16 encoding, '
        'Marching Cubes, independent display LOD and anatomical colors.\n'
        'Data and derived models remain CC BY-SA 4.0; no upstream endorsement.\n', encoding='utf-8')
    # Real registered CT projections are diagnostic evidence, not a substitute mesh.
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 3, figsize=(15, 8))
    for ax, axis in zip(axes, range(3)):
        ax.imshow(ct.max(axis=axis), cmap='gray'); ax.axis('off')
        ax.set_title('Registered CT maximum projection, axis ' + str(axis))
    fig.tight_layout(); fig.savefig(args.output / 'source_mip.png', dpi=150); plt.close(fig)
    print('PASS 16 aligned anatomical fields, original 0.1 mm grid', flush=True)


if __name__ == '__main__':
    main()
