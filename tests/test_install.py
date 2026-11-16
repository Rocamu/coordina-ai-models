import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("coordina_installer", Path(__file__).resolve().parents[1]/"install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def test_refresh_installs_harness_skill_with_references(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/"source/plugin/skills/coordina";source.mkdir(parents=True)
            (source/"SKILL.md").write_text("contract 2.3.9")
            (source/"references").mkdir();(source/"references/transportes.md").write_text("transport docs")
            user=root/"user";(user/".dsh/skills").mkdir(parents=True)
            with patch.object(installer,"USER",user),patch.object(installer,"ROOT",root/"source"),patch.object(installer,"BACKUP",root/"backup"):
                installer.refresh_skills()
                self.assertEqual((user/".dsh/skills/coordina/SKILL.md").read_text(),"contract 2.3.9")
                self.assertEqual((user/".dsh/skills/coordina/references/transportes.md").read_text(),"transport docs")
    def test_client_interpreter_is_preserved_without_changing_other_clients(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);python=root/"authorized-python";python.touch()
            (root/"config.json").write_text(json.dumps({"client_interpreters":{"desktop":str(python)}}))
            with patch.object(installer,"STATE",root):
                self.assertEqual(installer.mcp_wire("desktop")["command"],str(python))
                self.assertEqual(installer.mcp_wire("codex")["command"],installer.PYTHON)
                python.unlink()
                with self.assertRaises(ValueError): installer.mcp_wire("desktop")

    def test_release_marker_preserves_transport_and_permissions(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);p=root/".claude.json"
            original={"mcpServers":{"coordina":{"command":"keep","args":["keep"],"env":{"OTHER":"keep"}},"other":{"command":"unrelated"}},"permission":"unchanged"}
            p.write_text(json.dumps(original))
            config=root/".codex/config.toml";config.parent.mkdir()
            config.write_text('approval_policy = "on-request"\n[mcp_servers.coordina.env]\nOTHER = "keep"\n\n[mcp_servers.other]\ncommand = "keep"\n')
            with patch.object(installer,"USER",root),patch.object(installer,"ROOT",root/"source"),patch.object(installer,"BACKUP",root/"backup"):
                installer.refresh_connection_versions()
                value=json.loads(p.read_text())
                self.assertEqual(value['mcpServers']['coordina']['env'].pop('COORDINA_RELEASE'),installer.VERSION)
                self.assertEqual(value,original)
                first=config.read_text();installer.refresh_connection_versions()
                self.assertEqual(first,config.read_text())
                self.assertIn('approval_policy = "on-request"',first)
                self.assertIn('[mcp_servers.other]\ncommand = "keep"',first)

    def test_skill_update_copies_required_references_even_when_entrypoint_matches(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);src=root/"plugin/skills/coordina";src.mkdir(parents=True)
            (src/"SKILL.md").write_text("entrypoint")
            (src/"references").mkdir();(src/"references/transportes.md").write_text("cloud and local")
            dest=root/"installed";dest.mkdir();(dest/"SKILL.md").write_text("entrypoint")
            with patch.object(installer,"ROOT",root),patch.object(installer,"BACKUP",root/"backup"):
                installer.skill_at(dest)
                self.assertEqual((dest/"references/transportes.md").read_text(),"cloud and local")

    def test_scoped_approval_survives_reinstall_without_changing_other_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root/".codex/config.toml"
            path.parent.mkdir()
            unrelated = 'approval_policy = "on-request"\n\n[mcp_servers.other]\ncommand = "keep"\ndefault_tools_approval_mode = "prompt"\n\n'
            path.write_text(unrelated+'[mcp_servers.coordina]\ncommand = "old"\nargs = ["old"]\n\n[plugins."coordina@personal"]\nenabled = true\n')
            with patch.object(installer,"USER",root),patch.object(installer,"BACKUP",root/"backup"):
                installer.configure_codex(auto_approve=True)
                first = path.read_text()
                installer.configure_codex(auto_approve=True)
                self.assertEqual(first,path.read_text())
                self.assertIn(unrelated,first)
                self.assertEqual(first.count('default_tools_approval_mode = "approve"'),2)
                # Re-running without the opt-in keeps an earlier opt-in untouched.
                installer.configure_codex()
                self.assertEqual(first,path.read_text())
                self.assertTrue((root/"backup"/str(path).lstrip("/")).is_file())

    def test_deployment_uses_real_files_and_preserves_previous_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);source=root/"Desktop/source";source.mkdir(parents=True)
            runtime=root/"Library/Application Support/Coordina/runtime";runtime.mkdir(parents=True)
            for name in installer.SOURCE_MODULES:
                (source/name).write_text("new "+name)
                (runtime/name).write_text("previous "+name)
            with patch.object(installer,"ROOT",source),patch.object(installer,"RUNTIME",runtime),patch.object(installer,"BACKUP",root/"backup"):
                installer.deploy_runtime()
                for name in installer.SOURCE_MODULES:
                    self.assertFalse((runtime/name).is_symlink())
                    self.assertEqual((runtime/name).read_text(),"new "+name)
                    self.assertEqual((root/"backup"/str(runtime/name).lstrip("/")).read_text(),"previous "+name)


if __name__ == "__main__":
    unittest.main()
