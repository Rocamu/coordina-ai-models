"""Safe defaults: no tool auto-approval without opt-in; transactional installer."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(os.environ.get('COORDINA_TEST_ROOT', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import coordina as b

spec = importlib.util.spec_from_file_location("coordina_installer_safety", ROOT/"install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class AutoApproveOptInTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='coordina-safety-')
        self.root = Path(self.tmp.name)
        env = {k: v for k, v in os.environ.items() if k != 'COORDINA_AUTO_APPROVE'}
        env['COORDINA_HOME'] = str(self.root/'state')
        self.env = patch.dict(os.environ, env, clear=True); self.env.start()
        (self.root/'state').mkdir()
        self.work = self.root/'project'; self.work.mkdir()
        self.foro = self.root/'foro/mcp.py'; self.foro.parent.mkdir(); self.foro.write_text('# forum')
        self.foro_patch = patch.object(b, 'FORO_MCP', self.foro); self.foro_patch.start()

    def tearDown(self):
        self.foro_patch.stop(); self.env.stop(); self.tmp.cleanup()

    def fake(self, name):
        f = self.root/name; f.write_text('#!/bin/sh\n'); f.chmod(0o700); return str(f)

    def spec(self, mode='consult'):
        return {'mode': mode, 'workspace': str(self.work), 'timeout': 300}

    def cfg(self, **extra):
        cfg = {'binaries': {n: self.fake(n) for n in ('codex', 'claude', 'grok')}, 'models': {'grok': 'm'}}
        cfg.update(extra); return cfg

    def test_default_is_off_and_env_overrides_config(self):
        self.assertFalse(b.auto_approve_enabled({}))
        self.assertTrue(b.auto_approve_enabled({'auto_approve': True}))
        self.assertFalse(b.auto_approve_enabled({'auto_approve': 'yes'}))
        with patch.dict(os.environ, {'COORDINA_AUTO_APPROVE': '1'}):
            self.assertTrue(b.auto_approve_enabled({}))
        with patch.dict(os.environ, {'COORDINA_AUTO_APPROVE': '0'}):
            self.assertFalse(b.auto_approve_enabled({'auto_approve': True}))

    def test_grok_refuses_without_opt_in_and_approves_only_with_it(self):
        out = self.root/'1.answer.txt'
        sel = {'sent_to_cli': 'm'}
        with self.assertRaises(ValueError) as ctx:
            b.command_for('grok', self.spec(), out, 'p', cfg=self.cfg(), selection=sel)
        self.assertIn('COORDINA_AUTO_APPROVE', str(ctx.exception))
        cmd, _ = b.command_for('grok', self.spec(), out, 'p', cfg=self.cfg(auto_approve=True), selection=sel)
        self.assertIn('--always-approve', cmd)
        with patch.dict(os.environ, {'COORDINA_AUTO_APPROVE': '1'}):
            cmd, _ = b.command_for('grok', self.spec(), out, 'p', cfg=self.cfg(), selection=sel)
            self.assertIn('--always-approve', cmd)

    def test_forum_mcp_is_not_injected_or_preapproved_by_default(self):
        out = self.root/'1.answer.txt'; sel = {'sent_to_cli': None}
        cmd, _ = b.command_for('codex', self.spec(), out, 'p', cfg=self.cfg(), selection=sel)
        self.assertFalse(any('foro-agentes' in x for x in cmd))
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', cmd)
        self.assertEqual(cmd[cmd.index('--sandbox')+1], 'read-only')
        cmd, _ = b.command_for('claude', self.spec(), out, 'p', cfg=self.cfg(), selection=sel)
        self.assertFalse(any('foro' in x for x in cmd))
        self.assertNotIn('--dangerously-skip-permissions', cmd)
        cmd, _ = b.command_for('codex', self.spec(), out, 'p', cfg=self.cfg(auto_approve=True), selection=sel)
        self.assertTrue(any('foro-agentes.default_tools_approval_mode' in x for x in cmd))

    def test_codex_config_has_no_auto_approval_by_default(self):
        user = self.root/'user'; user.mkdir()
        with patch.object(installer, 'USER', user), patch.object(installer, 'BACKUP', self.root/'backup'), \
             patch.object(installer, 'STATE', self.root/'state'):
            installer.reset_journal()
            installer.configure_codex()
            text = (user/'.codex/config.toml').read_text()
        self.assertIn('[mcp_servers.coordina]', text)
        self.assertNotIn('default_tools_approval_mode', text)


class TransactionalInstallerTests(unittest.TestCase):
    """End-to-end runs of install.main() in a throwaway home with fake CLIs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='coordina-install-')
        t = Path(self.tmp.name)
        self.src = t/'src'; self.src.mkdir()
        for name in installer.SOURCE_MODULES:
            shutil.copy2(ROOT/name, self.src/name)
        shutil.copytree(ROOT/'plugin', self.src/'plugin',
                        ignore=shutil.ignore_patterns('.mcp.json', '.claude-plugin', '.codex-plugin'))
        self.user = t/'home'; self.user.mkdir()
        self.bin = t/'bin'; self.bin.mkdir()
        for n in ('codex', 'claude'):
            f = self.bin/n; f.write_text('#!/bin/sh\nexit 0\n'); f.chmod(0o700)
        self.existing = self.user/'.claude.json'
        self.existing.write_text(json.dumps({'mcpServers': {'other': {'command': 'keep'}}}))
        self.codex_cfg = self.user/'.codex/config.toml'; self.codex_cfg.parent.mkdir()
        self.codex_cfg.write_text('model = "keep"\n')
        self.patches = [
            patch.object(installer, 'ROOT', self.src), patch.object(installer, 'USER', self.user),
            patch.object(installer, 'STATE', self.user/'.local/share/coordina'),
            patch.object(installer, 'BACKUP', self.user/'.local/share/coordina/backups/test'),
            patch.object(installer, 'RUNTIME', self.user/'Library/Application Support/Coordina/runtime'),
            patch.object(installer.sys, 'platform', 'darwin'),
            patch.dict(os.environ, {'PATH': str(self.bin)+os.pathsep+'/usr/bin:/bin'}),
        ]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def snapshot(self):
        return {str(p.relative_to(self.user)): (p.read_bytes() if p.is_file() else None)
                for p in sorted(self.user.rglob('*'))}

    def fake_run(self, fail_on=None):
        def run(cmd, check=False, **kw):
            if fail_on and fail_on in cmd:
                raise subprocess.CalledProcessError(1, cmd)
            if cmd[1:3] == ['mcp', 'add'] and 'claude' in cmd[0]:
                d = json.loads(self.existing.read_text()); d['mcpServers']['coordina'] = {'command': 'x'}
                self.existing.write_text(json.dumps(d))
            if cmd[1:3] == ['mcp', 'add'] and 'codex' in cmd[0]:
                self.codex_cfg.write_text(self.codex_cfg.read_text()+'\n[mcp_servers.coordina]\ncommand = "x"\n')
            return subprocess.CompletedProcess(cmd, 0)
        return run

    def test_preflight_failure_writes_nothing(self):
        before = self.snapshot()
        (self.src/'coordina.py').unlink()
        with patch.object(installer.subprocess, 'run', self.fake_run()):
            with self.assertRaises(SystemExit) as ctx:
                installer.main([])
        self.assertIn('coordina.py', str(ctx.exception))
        self.assertIn('No se instaló nada', str(ctx.exception))
        self.assertEqual(before, self.snapshot())

    def test_invalid_claude_json_is_caught_before_any_write(self):
        self.existing.write_text('{broken')
        before = self.snapshot()
        with self.assertRaises(SystemExit):
            installer.main([])
        self.assertEqual(before, self.snapshot())

    def test_failure_midway_rolls_back_every_change(self):
        before = self.snapshot()
        with patch.object(installer, 'configure_codex', side_effect=RuntimeError('configuration failed')):
            with self.assertRaises(SystemExit) as ctx:
                installer.main([])
        self.assertIn('vuelve a ejecutar', str(ctx.exception))
        after = self.snapshot()
        # Only the state folder with its backups may remain; everything else is as before.
        leftovers = {k: v for k, v in after.items() if not k.startswith('.local/share/coordina')}
        expected = {k: v for k, v in before.items()}
        self.assertEqual(expected, leftovers)
        self.assertFalse((self.user/'.local/bin/coordina').exists())

    def test_interruption_rolls_back(self):
        before = self.snapshot()
        with patch.object(installer, 'build_zip', side_effect=KeyboardInterrupt):
            with patch.object(installer.subprocess, 'run', self.fake_run()):
                with self.assertRaises(SystemExit):
                    installer.main([])
        leftovers = {k: v for k, v in self.snapshot().items() if not k.startswith('.local/share/coordina')}
        self.assertEqual(before, leftovers)

    def test_success_is_idempotent_and_safe_by_default(self):
        with patch.object(installer.subprocess, 'run', self.fake_run()):
            installer.main([])
            installer.main([])  # client CLIs reformat their files on first registration
            first = self.snapshot()
            installer.main([])
        second = self.snapshot()
        strip = lambda s: {k: v for k, v in s.items() if 'backups' not in k and not k.endswith('last-install.json')}
        self.assertEqual(strip(first), strip(second))
        self.assertTrue((self.user/'.local/bin/coordina').is_file())
        self.assertNotIn('default_tools_approval_mode', self.codex_cfg.read_text())
        cfg = json.loads((self.user/'.local/share/coordina/config.json').read_text())
        self.assertNotIn('auto_approve', cfg)
        self.assertIn('model = "keep"', self.codex_cfg.read_text())
        self.assertEqual(json.loads(self.existing.read_text())['mcpServers']['other'], {'command': 'keep'})

    def test_auto_approve_flag_is_explicit(self):
        with patch.object(installer.subprocess, 'run', self.fake_run()):
            installer.main(['--auto-approve'])
        self.assertIn('default_tools_approval_mode = "approve"', self.codex_cfg.read_text())
        cfg = json.loads((self.user/'.local/share/coordina/config.json').read_text())
        self.assertIs(cfg['auto_approve'], True)


if __name__ == '__main__':
    unittest.main()
