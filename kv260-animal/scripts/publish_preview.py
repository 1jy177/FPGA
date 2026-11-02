#!/usr/bin/env python3
"""Publish the verified Digimouse reference without removing legacy datasets."""
import argparse
import json
import shutil
from pathlib import Path
from utils import digest

ROOT=Path(__file__).resolve().parents[1]


def publish(preview):
    scene=json.loads((preview/'digimouse/scene.json').read_text())
    if scene['profile']!='digimouse' or len(scene['layers'])!=16 or scene['board_tested']:
        raise ValueError('expected 16-layer CPU reference, not an unverified result')
    for layer in scene['layers']:
        for model in (layer['full'],layer['display']):
            if Path(model['file']).name!=model['file']:
                raise ValueError('invalid model filename')
            if digest(preview/'digimouse'/model['file'])!=model['sha256']:
                raise ValueError('preview model SHA256 mismatch')
    # Never discard the earlier landing page or earlier scene HTML.
    for path in (preview/'index.html',preview/'quick/index.html',preview/'native/index.html'):
        backup=path.with_name('embryo-legacy.html')
        if path.is_file() and not backup.exists():shutil.copy2(path,backup)
    shutil.copyfile(ROOT/'software/pynq_mc/index.html',preview/'index.html')
    shutil.copyfile(ROOT/'software/pynq_mc/scene.html',preview/'digimouse/index.html')
    for profile,preset in [('quick','combined'),('native','exterior')]:
        directory=preview/profile;directory.mkdir(exist_ok=True)
        target='../digimouse/?view='+preset
        (directory/'index.html').write_text(
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta http-equiv="refresh" content="0;url='+target+'">'
            '<title>Digimouse 场景已更新</title><a href="'+target+'">打开新版小鼠解剖图谱</a></html>\n',
            encoding='utf-8')
    print('PASS Digimouse landing and compatibility URLs published; legacy datasets retained.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--preview',type=Path,required=True)
    publish(parser.parse_args().preview)
