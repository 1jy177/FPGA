#!/usr/bin/env python3
"""Download pinned USC Digimouse volumes and retain their upstream license."""
import argparse
import subprocess
import zipfile
from pathlib import Path
from utils import digest, write_json

BASE = 'https://neuroimage.usc.edu/mouse_atlas/'
FILES = {
    'atlas.zip': (907411, '6f7cbd8a7236a78c47a14c3259d387439c0dc0284b2a83fa955f8c49efe11761'),
    'ct_data.zip': (19876037, '5ca9e99a3a4adeb7879bb4d6c8f45e407b7a49bbc599b3936fa46503cf7acb65'),
}
SOURCE = {
    'title': 'Digimouse: a 3D whole body mouse atlas from CT and cryosection data',
    'author': 'B. Dogdas, D. Stout, A. F. Chatziioannou, R. M. Leahy; USC Biomedical Imaging Group',
    'doi': '10.1088/0031-9155/52/3/003',
    'url': 'https://neuroimage.usc.edu/neuro/Digimouse',
    'license': 'CC BY-SA 4.0',
    'license_url': 'https://creativecommons.org/licenses/by-sa/4.0/',
    'license_source': 'https://neuroimage.usc.edu/neuro/Digimouse_license',
    'specimen': '28 g normal nude male mouse; exact age in weeks is not stated by the source',
    'source_dtype': 'uint8 registered CT and uint8 expert-assisted anatomical labels',
    'spacing_mm': [0.1, 0.1, 0.1],
    'shape_zyx': [208, 992, 380],
    'label_provenance': 'Organs delineated on registered cryosections with an anatomist; skeleton and skin from CT.',
}


def verify(directory):
    for name, (size, sha) in FILES.items():
        path = directory / name
        if not path.is_file() or path.stat().st_size != size or digest(path) != sha:
            raise ValueError('Digimouse archive size/SHA256 mismatch: ' + name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name, (size, sha) in FILES.items():
        path = args.output / name
        if not (path.is_file() and path.stat().st_size == size and digest(path) == sha):
            # A fresh partial file never replaces a previously verified archive.
            partial = path.with_suffix(path.suffix + '.partial')
            subprocess.run(['curl', '--fail', '-L', '--retry', '5', '--retry-all-errors',
                            '--connect-timeout', '20', '--max-time', '600',
                            '-o', str(partial), BASE + name], check=True)
            if partial.stat().st_size != size or digest(partial) != sha:
                raise ValueError('upstream archive changed: ' + name)
            partial.replace(path)
        print('PASS', name, sha, flush=True)
    verify(args.output)
    with zipfile.ZipFile(args.output / 'atlas.zip') as archive:
        license_text = archive.read('atlas/LICENSE.txt')
        if b'Attribution-ShareAlike 4.0 International' not in license_text:
            raise ValueError('unexpected upstream data license')
        (args.output / 'LICENSE-Digimouse.txt').write_bytes(license_text)
    source = dict(SOURCE, archives={name: {'url': BASE + name, 'bytes': size, 'sha256': sha}
                                  for name, (size, sha) in FILES.items()})
    write_json(args.output / 'source.json', source)
    print('PASS pinned CT, anatomical labels and CC BY-SA 4.0 license', flush=True)


if __name__ == '__main__':
    main()
