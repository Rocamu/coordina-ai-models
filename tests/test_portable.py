"""Phase 1 startup checks; no model, network or real user configuration used."""
import contextlib
import io
import json
import os
import sys
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import patch

import portable
from test_install import installer


class StartupTests(unittest.TestCase):
    def test_doctor_distinguishes_missing_broken_and_responsive(self):
        def probe(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1 if argv[0].endswith('claude') else 0)
        with patch('portable.shutil.which', side_effect=lambda n: '/bin/'+n if n in ('codex', 'claude') else None), patch('portable.subprocess.run', side_effect=probe):
            result = portable.doctor()
        self.assertEqual(result['available'], ['codex'])
        self.assertEqual(result['mode'], 'direct')
        self.assertTrue(result['models'][1]['found'])
        self.assertFalse(result['models'][1]['responds'])
        self.assertTrue(all(row['install'].startswith('https://') for row in result['models']))
        self.assertEqual(result['models'][0]['authentication'], 'not checked')

    def test_one_model_direct_and_two_models_share_context(self):
        for installed in (['claude'], ['codex', 'claude']):
            with self.subTest(installed=installed):
                calls = []
                def execute(argv, **kwargs):
                    calls.append(argv)
                    return subprocess.CompletedProcess(argv, 0, stdout='answer', stderr='')
                with patch('portable.shutil.which', side_effect=lambda n: '/bin/'+n if n in installed else None), patch('portable.subprocess.run', side_effect=execute):
                    result = portable.run('question', '.', targets=installed)
                self.assertEqual(result['mode'], 'direct' if len(installed) == 1 else 'coordinate')
                self.assertEqual(list(result['answers']), installed)
                if len(installed) > 1:
                    self.assertIn('answer', calls[-1][-1])

    def test_no_models_returns_actionable_error(self):
        with patch('portable.shutil.which', return_value=None):
            self.assertEqual(portable.doctor()['mode'], 'unavailable')
            with self.assertRaisesRegex(RuntimeError, 'coordina doctor'):
                portable.run('question', '.')

    def test_cli_with_simulated_executables(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            env = dict(os.environ, PATH=folder, COORDINA_HOME=str(root/'state'))
            cli = Path(__file__).resolve().parents[1]/'coordina.py'
            for name in ('claude', 'codex'):
                executable = root/name
                executable.write_text('#!/bin/sh\nprintf "simulated answer\\n"\n')
                executable.chmod(0o700)
                completed = subprocess.run([sys.executable, '-B', str(cli), 'run', 'question', '--workspace', folder], env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual(result['mode'], 'direct' if name == 'claude' else 'coordinate')
            diagnosis = subprocess.run([sys.executable, '-B', str(cli), 'doctor'], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(diagnosis.returncode, 0, diagnosis.stderr)
            self.assertEqual(json.loads(diagnosis.stdout)['available'], ['codex', 'claude'])

    def test_linux_preflight_does_not_require_any_cli(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(installer.sys, 'platform', 'linux'), patch.object(installer, 'USER', Path(folder)), patch.object(installer, 'STATE', Path(folder)/'state'), patch.object(installer, 'RUNTIME', Path(folder)/'runtime'), patch.object(installer.shutil, 'which', return_value=None):
            installer.preflight([])

    def test_version_timeout_is_bounded(self):
        with patch('portable.shutil.which', return_value='/bin/model'), patch('portable.subprocess.run', side_effect=subprocess.TimeoutExpired('model', 5)) as probe:
            self.assertFalse(portable.CommandAdapter('codex', 'codex', 'url').health()['responds'])
            self.assertEqual(probe.call_args.kwargs['timeout'], 5)

    def test_installer_accepts_optional_clients_and_preserves_other_config(self):
        for installed in ([], ['codex'], ['claude'], ['gemini'], ['opencode'], ['codex', 'claude']):
            with self.subTest(installed=installed), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                user = root/'user'; user.mkdir()
                state = root/'state'
                source = root/'source'; source.mkdir()
                shutil.copytree(installer.ROOT/'plugin/skills', source/'plugin/skills')
                for module in installer.SOURCE_MODULES:
                    (source/module).touch()
                with contextlib.ExitStack() as stack:
                    for name, value in [('ROOT', source), ('USER', user), ('STATE', state), ('RUNTIME', root/'runtime'), ('BACKUP', root/'backup')]:
                        stack.enter_context(patch.object(installer, name, value))
                    stack.enter_context(patch.object(installer.shutil, 'which', side_effect=lambda n: '/bin/'+n if n in installed else None))
                    stack.enter_context(patch.object(installer, 'deploy_runtime'))
                    stack.enter_context(patch.object(installer, 'build_zip', return_value=root/'plugin.zip'))
                    stack.enter_context(patch.object(installer, 'install_antigravity', return_value=None))
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    installer.reset_journal()
                    installer.preflight([])
                    installer.install([])
                self.assertEqual((user/'.codex/config.toml').exists(), 'codex' in installed)
                self.assertEqual((user/'.claude.json').exists(), 'claude' in installed)
                self.assertEqual(json.loads((state/'last-install.json').read_text())['detected_models'], installed)
                self.assertTrue((user/'.local/bin/coordina').is_file())


if __name__ == '__main__':
    unittest.main()
