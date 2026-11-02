import json
import sys
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from reconstruct_scene import verified_display
from utils import digest

class DisplayBindingTests(unittest.TestCase):
    def test_exact_provenance_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            reference=Path(tmp)/'reference';reference.mkdir()
            output=Path(tmp)/'output';output.mkdir()
            binary=reference/'body.display.mesh';binary.write_bytes(b'display result')
            display={'file':binary.name,'sha256':digest(binary),'vertices':1,'triangles':1,'bounds':[[0]*3,[1]*3]}
            scene={'layers':[{'id':'body','input_sha256':'input hash',
                     'full':{'sha256':'full mesh hash'},'display':display}]}
            (reference/'scene.json').write_text(json.dumps(scene))
            meta={'id':'body','sha256':'input hash'};full={'sha256':'full mesh hash'}
            actual=verified_display(reference,meta,full,output)
            self.assertEqual(actual,display)
            self.assertEqual((output/binary.name).read_bytes(),binary.read_bytes())
            with self.assertRaisesRegex(ValueError,'differs'):
                verified_display(reference,meta,{'sha256':'hardware mismatch'},output)
            with self.assertRaisesRegex(ValueError,'differs'):
                verified_display(reference,dict(meta,sha256='wrong input'),full,output)
            binary.write_bytes(b'corrupted display')
            with self.assertRaisesRegex(ValueError,'hash'):
                verified_display(reference,meta,full,output)
if __name__=='__main__':unittest.main()
