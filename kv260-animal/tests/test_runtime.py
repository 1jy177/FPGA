import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"software"))
from pynq_mc import Reconstructor
from pynq_mc.volume import make_sphere, load
from pynq_mc.runtime import Mesh

class RuntimeTests(unittest.TestCase):
    def test_slabs_and_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=make_sphere(Path(tmp)/"sphere.bin",17)
            engine=Reconstructor(backend="cpu")
            a=engine.reconstruct(path,32657,slab_cells=1)
            b=engine.reconstruct(path,32657,slab_cells=16)
            np.testing.assert_array_equal(a.points,b.points)
            np.testing.assert_array_equal(a.faces,b.faces)
            c=engine.reconstruct(path,32657,slab_cells=2,mesh_path=Path(tmp)/'sphere.mesh')
            np.testing.assert_array_equal(a.points,c.points)
            np.testing.assert_array_equal(a.faces,c.faces)
            self.assertTrue(c.timing['mesh_file_backed'])
            self.assertTrue(c.timing['mesh_streamed'])
            self.assertLess(c.timing['mesh_cache_peak_vertices'],len(c.points))
            self.assertGreater(len(a.faces),100)
            self.assertEqual(len(engine.reconstruct(path,0).faces),0)
            self.assertEqual(len(engine.reconstruct(path,65535).faces),0)
            html=a.show(Path(tmp)/"view.html").read_text()
            self.assertNotIn('/*MODEL_DATA*/null',html)
            self.assertIn('onpointermove',html)
            a.save(Path(tmp)/"view.obj")
            self.assertTrue((Path(tmp)/"view.obj").stat().st_size>100)
            # A failed new reconstruction must preserve an existing result.
            output=Path(tmp)/'preserved.mesh';output.write_bytes(b'existing result')
            with self.assertRaisesRegex(RuntimeError,'budget'):
                Reconstructor(backend='cpu',max_faces=1).reconstruct(path,mesh_path=output)
            self.assertEqual(output.read_bytes(),b'existing result')
            self.assertEqual(list(Path(tmp).glob('*.partial-*')),[])
            empty=engine.reconstruct(path,0,mesh_path=Path(tmp)/'empty.mesh')
            self.assertEqual(len(empty.faces),0)
            self.assertEqual((Path(tmp)/'empty.mesh').stat().st_size,0)
    def test_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=make_sphere(Path(tmp)/"sphere.bin",9)
            with self.assertRaises(ValueError):Reconstructor(backend="cpu").reconstruct(path,-1)
            with self.assertRaises(ValueError):Reconstructor(backend="cpu").reconstruct(path,1.5)
            with self.assertRaises(RuntimeError):Reconstructor(backend="cpu",max_vertices=2).reconstruct(path)
            data=bytearray(path.read_bytes());data[-1]^=1;path.write_bytes(data)
            with self.assertRaisesRegex(ValueError,"CRC32"):load(path)
    def test_wrong_board_does_not_program_overlay(self):
        with patch('pynq_mc.runtime.platform.machine',return_value='x86_64'):
            with self.assertRaisesRegex(RuntimeError,'identification required'):
                Reconstructor(backend='pynq')
        class LargeFaces:
            def __len__(self):return 6000001
        with self.assertRaisesRegex(ValueError,'display LOD'):
            Mesh(None,LargeFaces(),{}).show()

if __name__=="__main__":unittest.main()
