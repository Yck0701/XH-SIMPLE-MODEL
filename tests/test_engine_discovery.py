from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'Launcher'))
import core
import engine_discovery as discovery


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        self.project=self.root/'workspace/game/project.uproject';core.write_json(self.project,{'FileVersion':3})
        self.info=core.project_info(self.project)

    def engine(self,path,version=(5,8,3)):
        exe=self.root/path/'Engine/Binaries/Win64/UnrealEditor.exe';exe.parent.mkdir(parents=True);exe.write_bytes(b'editor')
        core.write_json(exe.parents[2]/'Build/Build.version',dict(zip(('MajorVersion','MinorVersion','PatchVersion'),version)))
        return exe

    def find(self,**kwargs):
        return discovery.discover(self.info,**dict({'roots':[],'records':[],'running':[]},**kwargs))

    def test_sibling_source_tree_and_relative_association(self):
        engine=self.engine(Path('workspace/UE_company'))
        self.assertEqual(self.find()['selected'],str(engine))
        self.info['association']='../UE_company'
        self.assertEqual(self.find()['selected'],str(engine))
        self.assertIn('工程中的路径关联',self.find()['candidates'][0]['sources'])

    def test_deep_source_tree_finds_ancestor_engine(self):
        engine=self.engine(Path('source'))
        project=self.root/'source/Projects/Team/Effects/Samples/Test.uproject';core.write_json(project,{})
        self.info=core.project_info(project)
        self.assertEqual(self.find()['selected'],str(engine))

    def test_registered_guid_ignores_braces_case_and_stale_location(self):
        engine=self.engine(Path('registered'))
        self.info['association']='{ABCD-1234}'
        result=self.find(records=[('abcd-1234',engine.parents[3],'registry'),('abcd-1234',self.root/'missing','stale')])
        self.assertEqual(result['selected'],str(engine));self.assertEqual(len(result['candidates']),1)

    def test_numeric_association_never_uses_neighbouring_wrong_version(self):
        wrong=self.engine(Path('workspace/UE_old'),(5,7,0));right=self.engine(Path('install/UE_5.8'))
        self.info['association']='5.8'
        result=self.find(records=[('5.7',wrong.parents[3],'registry')],roots=[right.parents[3]])
        self.assertEqual(result['selected'],str(right))

    def test_duplicate_records_stay_one_engine(self):
        engine=self.engine(Path('installed'));self.info['association']='5.8'
        result=self.find(records=[('5.8',engine.parents[3],'registry64'),('5.8',engine.parents[3],'Epic')],known=[str(engine)])
        self.assertEqual(len(result['candidates']),1);self.assertEqual(result['selected'],str(engine))
        self.assertEqual(len(result['candidates'][0]['sources']),3)

    def test_multiple_same_version_requires_choice(self):
        a=self.engine(Path('installed-a'));b=self.engine(Path('installed-b'));self.info['association']='5.8'
        result=self.find(records=[('5.8',a.parents[3],'registry'),('5.8',b.parents[3],'Epic')])
        self.assertIsNone(result['selected']);self.assertEqual(len(result['candidates']),2)
        self.assertEqual(self.find(records=[('5.8',a.parents[3],'registry')],remembered=str(b))['selected'],str(b))

    def test_project_local_precedes_generic_official_and_running_precedes_memory(self):
        local=self.engine(Path('workspace/game/UE'));official=self.engine(Path('official'));self.info['association']='5.8'
        records=[('5.8',official.parents[3],'Epic')]
        self.assertEqual(self.find(records=records)['selected'],str(local))
        self.assertEqual(self.find(remembered=str(official),running=[str(local)])['selected'],str(local))

    def test_no_association_does_not_guess_single_unrelated_engine(self):
        engine=self.engine(Path('unrelated'))
        result=self.find(roots=[engine.parents[3]])
        self.assertIsNone(result['selected']);self.assertEqual(len(result['candidates']),1)

    def test_bounded_search_does_not_scan_assets_or_change_files(self):
        self.engine(Path('workspace/game/Content/UE_hidden'))
        before=core.inventory(self.root)
        self.assertEqual(self.find()['candidates'],[])
        self.assertEqual(before,core.inventory(self.root))

    def test_launcher_manifest_and_malformed_manifest(self):
        file=self.root/'Epic/UnrealEngineLauncher/LauncherInstalled.dat'
        core.write_json(file,{'InstallationList':[{'AppName':'UE_5.8','InstallLocation':'D:/Engines/UE_5.8'},{'AppName':'OtherGame','InstallLocation':'D:/Other'}]})
        with patch.dict(discovery.os.environ,{'PROGRAMDATA':str(self.root)}):
            self.assertEqual(discovery.launcher_engines(),[('5.8','D:/Engines/UE_5.8','Epic 安装记录')])
            file.write_text('broken');self.assertEqual(discovery.launcher_engines(),[])

    def test_running_project_matches_complete_path_not_same_filename(self):
        payload=[{'ExecutablePath':'D:/Correct/UnrealEditor.exe','CommandLine':'"editor" "'+str(self.project)+'"'},
                 {'ExecutablePath':'D:/Wrong/UnrealEditor.exe','CommandLine':'"editor" "D:/Other/project.uproject"'}]
        with patch.object(discovery.subprocess,'run',return_value=type('Result',(),{'returncode':0,'stdout':json.dumps(payload)})()):
            self.assertEqual(discovery.running_engine(self.project),['D:/Correct/UnrealEditor.exe'])

    def test_missing_remembered_engine_falls_back_to_current_project(self):
        current=self.engine(Path('workspace/game/EngineCopy/UE'))
        self.info['association']='EngineCopy/UE'
        self.assertEqual(self.find(remembered='Z:/unavailable/UnrealEditor.exe')['selected'],str(current))


if __name__=='__main__':unittest.main()
