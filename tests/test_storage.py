"""Exercise write boundaries without starting an editor or touching user assets."""
import importlib.util
from pathlib import Path
import sys,tempfile,types,unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'Plugin/VFXMeshGenerator/Content/Python/vfx_mesh/storage.py'

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.load(self.root)

    def load(self,root):
        fake=types.SimpleNamespace(Paths=types.SimpleNamespace(project_dir=lambda:str(root)))
        spec=importlib.util.spec_from_file_location('vfx_storage_under_test',SOURCE)
        self.module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,unreal=fake):spec.loader.exec_module(self.module)

    def test_asset_and_object_paths_accept_project_assets(self):
        for name in ('/Game/MI_中文','/Game/Sub/SM_Mesh.SM_Mesh'):
            self.assertEqual(self.module.content_path(name),name.split('.')[0])

    def test_foreign_mounts_and_traversal_rejected_without_writes(self):
        for name in ('/Engine/SM_Mesh','/OtherPlugin/Mesh','/Game/../Other/Mesh',
                     '/Game/A/../../B','/Game/A/SM.Bad/../../B','/Game/A:stream','/Game//A','/Game/A\\B'):
            with self.subTest(name=name),self.assertRaises(ValueError):self.module.content_path(name)
        with self.assertRaises(ValueError):self.module.project_path(self.root.parent/'elsewhere')
        self.assertEqual(list(self.root.iterdir()),[])

    def test_real_linked_content_and_saved_are_blocked(self):
        fixture=Path('H:/GPT/XH/tests/vfx152-path-check/current')
        if not (fixture/'Content/Linked').exists():self.skipTest('Requires isolated Windows junction fixture')
        self.load(fixture)
        with self.assertRaisesRegex(ValueError,'链接|重解析'):self.module.content_path('/Game/Linked/Keep')
        with self.assertRaisesRegex(ValueError,'链接|重解析'):self.module.project_path(fixture/'Saved/VFXMeshGenerator/editor.json')

if __name__=='__main__':unittest.main()
