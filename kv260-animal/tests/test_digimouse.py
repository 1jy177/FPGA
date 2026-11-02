import struct
import sys
import unittest
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from prepare_digimouse import read_analyze, cropped_mask, continuous_field, LAYERS


class DigimouseTests(unittest.TestCase):
    def test_analyze_storage_and_validation(self):
        header=bytearray(348)
        struct.pack_into('<i',header,0,348)
        struct.pack_into('<8h',header,40,4,5,7,9,1,0,0,0)
        struct.pack_into('<2h',header,70,2,8)
        struct.pack_into('<8f',header,76,0,.1,.1,.1,0,0,0,0)
        payload=np.arange(315,dtype=np.uint16).astype('u1').tobytes()
        volume,spacing=read_analyze(header,payload)
        self.assertEqual(volume.shape,(9,7,5))
        self.assertEqual(int(volume[1,2,3]),48)
        np.testing.assert_allclose(spacing,[.1]*3)
        with self.assertRaisesRegex(ValueError,'length'):
            read_analyze(header,payload[:-1])
        struct.pack_into('<h',header,70,4)
        with self.assertRaisesRegex(ValueError,'uint8'):
            read_analyze(header,payload)

    def test_crop_preserves_physical_position(self):
        labels=np.zeros((20,30,40),dtype='u1');labels[5:9,10:17,20:25]=9
        mask,start,count,boundary=cropped_mask(labels,[9],padding=3)
        self.assertEqual(count,4*7*5)
        self.assertFalse(boundary)
        np.testing.assert_array_equal(start,[2,7,17])
        self.assertEqual(mask.shape,(10,13,11))
        self.assertTrue(mask[3,3,3])
        field=continuous_field(mask,.65)
        self.assertEqual(field.dtype,np.dtype('<u2'))
        self.assertGreater(int(field[5,6,5]),32768)
        self.assertLess(int(field[0].max()),32768)
        labels[0,0,0]=9
        self.assertTrue(cropped_mask(labels,[9])[3])

    def test_every_anatomical_class_has_exactly_one_layer(self):
        classes=[index for layer in LAYERS for index in (layer[2] or [])]
        self.assertEqual(sorted(classes),list(range(2,22)))
        self.assertEqual(len(LAYERS),16)
        self.assertEqual(len({layer[0] for layer in LAYERS}),16)
        self.assertFalse(any('intestin' in layer[0] for layer in LAYERS))


if __name__=='__main__':unittest.main()
