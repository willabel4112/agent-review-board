import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import board_lib as lib


class SnapshotBoundary(unittest.TestCase):
    def test_only_hash_revisions_and_safe_docids_with_unicode_preserved(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(lib,'SNAPDIR',str(Path(temp)/'snapshots')):
            hidden = Path(temp)/'hidden.md'
            hidden.write_text('private synthetic sentinel',encoding='utf-8')
            sha = lib.snapshot_put('中文-id','safe synthetic document')
            self.assertEqual(lib.snapshot_get('中文-id',sha),'safe synthetic document')
            for attack in (str(hidden)[:-3],'../../hidden',r'C:\outside\hidden','invalid',None):
                self.assertIsNone(lib.snapshot_get('中文-id',attack))
                self.assertIsNone(lib.snapshot_blocks('中文-id',attack))
            for docid in ('../escaped',r'..\escaped','C:drive','name:ads','CON','..','name.'):
                with self.assertRaises(ValueError):
                    lib.snapshot_put(docid,'unsafe')
            self.assertEqual(hidden.read_text(encoding='utf-8'),'private synthetic sentinel')

    @unittest.skipIf(os.name=='nt','unprivileged Windows symlink creation is unavailable')
    def test_symlink_cannot_redirect_snapshot_outside_cache(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(lib,'SNAPDIR',str(Path(temp)/'snapshots')):
            root=Path(lib.SNAPDIR); root.mkdir()
            outside=Path(temp)/'outside'; outside.mkdir()
            (root/'linked').symlink_to(outside,target_is_directory=True)
            with self.assertRaises(ValueError): lib.snapshot_put('linked','private')
            self.assertIsNone(lib.snapshot_get('linked','a'*12))
            self.assertEqual(list(outside.iterdir()),[])


if __name__=='__main__': unittest.main()
