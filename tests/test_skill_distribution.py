"""Distribution preserves local files and records detectable managed-file drift."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('skill_installer',Path(__file__).resolve().parents[1]/'install.py')
i=importlib.util.module_from_spec(spec);spec.loader.exec_module(i)

class SkillDistributionTests(unittest.TestCase):
    def test_manifest_detects_drift_and_preserves_client_extension(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source/plugin/skills/coordina';source.mkdir(parents=True)
            (source/'SKILL.md').write_text('canonical')
            (source/'references').mkdir();(source/'references/local-harness.md').write_text('P-005')
            installed=root/'installed';installed.mkdir();(installed/'private-note.txt').write_text('keep')
            with patch.object(i,'ROOT',root/'source'),patch.object(i,'STATE',root/'state'),patch.object(i,'BACKUP',root/'backups'):
                i.skill_at(installed)
                data=i.write_skill_manifest([installed])
                self.assertTrue(data['installed'][0]['matches_canonical'])
                self.assertIn('private-note.txt',data['installed'][0]['unmanaged_files_preserved'])
                self.assertIn('harness',data['extensions']['references/local-harness.md']['applies_to'])
                (installed/'SKILL.md').write_text('drift')
                data=i.write_skill_manifest([installed]);self.assertFalse(data['installed'][0]['matches_canonical'])
                i.skill_at(installed)
                self.assertEqual((installed/'private-note.txt').read_text(),'keep')
                self.assertEqual((root/'backups'/str(installed/'SKILL.md').lstrip('/')).read_text(),'drift')

    def test_skills_only_does_not_deploy_or_reconfigure_runtime(self):
        with patch.object(i.sys,'argv',['install.py','--update-skills']),patch.object(i,'refresh_skills',return_value=[]) as refresh,patch.object(i,'write_skill_manifest') as manifest,patch.object(i,'build_zip',return_value='archive'),patch.object(i,'deploy_runtime') as deploy,patch.object(i,'refresh_connection_versions') as configure:
            i.main();refresh.assert_called_once();manifest.assert_called_once_with([])
            deploy.assert_not_called();configure.assert_not_called()

if __name__=='__main__':unittest.main()
