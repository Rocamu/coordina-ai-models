import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import coordina as b
import live
import opencode_backend as oc


class DeepSeekTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="coordina-deepseek-")
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.env = patch.dict(os.environ, {"COORDINA_HOME": str(self.root / "state")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def request(self, **kw):
        return live.coordinate("deepseek", "Lee README.md", str(self.workspace), launch=False, **kw)

    def test_cli_route_has_no_desktop_discovery_and_is_idempotent(self):
        with patch.object(live, "discover", side_effect=AssertionError("no desktop lookup")):
            first = self.request(request_id="one")
            second = self.request(request_id="one")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["spec"]["route"], "new")
        self.assertEqual(first["spec"]["target"], "deepseek")

    def test_no_existing_chat_or_paid_alternation(self):
        for kw in ({"route": "existing"}, {"session_id": "desktop"}, {"rounds": 2}):
            with self.assertRaises(ValueError):
                self.request(**kw)
        self.assertEqual(b.backend("opencode"), "joker")
        self.assertEqual(b.backend("opencode-deepseek"), "deepseek")

    @patch.object(oc, "BINARY", sys.executable)
    def test_additional_models_are_fixed_and_keep_one_round(self):
        for target, model in oc.MODELS.items():
            spec = {"target": target, "workspace": str(self.workspace), "mode": "consult"}
            job = live.coordinate(target, "Lee README.md", str(self.workspace), launch=False)
            self.assertEqual(job["spec"]["target"], target)
            self.assertIn(model, oc.command(spec))
            cfg = json.loads(oc.environment(spec)["OPENCODE_CONFIG_CONTENT"])
            allowed = [p["resource"] for p in cfg["policies"] if p["action"] == "provider.use" and p["effect"] == "allow"]
            self.assertEqual(allowed, [model.split("/", 1)[0]])
            self.assertEqual(oc.parse('', spec)[1]["model"], model)
            with self.assertRaises(ValueError):
                live.coordinate(target, "read", str(self.workspace), rounds=2, launch=False)
            with self.assertRaises(ValueError):
                live.coordinate(target, "read", str(self.workspace), route="existing", launch=False)

    def test_command_requires_installed_cli(self):
        with patch.object(oc, "BINARY", str(self.root / "missing-opencode-cli")):
            with self.assertRaisesRegex(ValueError, "falta su CLI empaquetada"):
                oc.command({"target": "deepseek"})

    def test_policy_restricts_tools_and_caller_pwd(self):
        env = oc.environment({"workspace": str(self.workspace), "mode": "consult"})
        self.assertEqual(env["PWD"], str(self.workspace.resolve()))
        cfg = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        allows = [p for p in cfg["policies"] if p["effect"] == "allow"]
        self.assertFalse(any(p["resource"].startswith(("shell:", "edit:", "subagent:")) for p in allows))
        self.assertIn({"action": "tool.use", "resource": "external_directory:*", "effect": "deny"}, cfg["policies"])
        self.assertIn({"action": "mcp.use", "resource": "*", "effect": "deny"}, cfg["policies"])

    def test_edit_only_adds_workspace_file_permission(self):
        cfg = json.loads(oc.environment({"workspace": str(self.workspace), "mode": "edit"})["OPENCODE_CONFIG_CONTENT"])
        edits = [x for x in cfg["policies"] if x["effect"] == "allow" and x["resource"].startswith("edit:")]
        self.assertEqual(edits, [{"action": "tool.use", "resource": "edit:*", "effect": "allow"}])

    def test_output_is_exact_exclusive_and_rejects_escape(self):
        import hashlib
        root = self.root / "outputs"
        root.mkdir()
        with patch.object(oc, "OUTPUT_ROOT", root):
            dest = root / "answer.json"
            answer = '["á", 1]\n'
            receipt = oc.write_response(str(dest), answer)
            self.assertEqual(dest.read_bytes(), answer.encode())
            self.assertEqual(receipt["sha256"], hashlib.sha256(answer.encode()).hexdigest())
            with self.assertRaises(FileExistsError):
                oc.write_response(str(dest), "overwrite")
            for bad in (str(self.root / "outside.txt"), str(root / "../escape.txt"), "relative.txt"):
                with self.assertRaises(ValueError):
                    oc.write_response(bad, "bad")
            (root / "link").symlink_to(self.workspace, target_is_directory=True)
            with self.assertRaises(OSError):
                oc.write_response(str(root / "link/out.txt"), "bad")
            self.assertFalse((self.workspace / "out.txt").exists())
            self.assertFalse(list(root.glob(".coordina-output-*")))

    def test_worker_output_path_returns_receipt_without_answer_text(self):
        fake = self.root / "opencode-cli"
        fake.write_text("#!"+sys.executable+"\nimport sys,json\nsys.stdin.read()\nprint(json.dumps({'type':'text','part':{'text':'EXACT_OUTPUT_7319'}}))\n")
        fake.chmod(0o700)
        root = self.root / "outputs"
        root.mkdir()
        with patch.object(oc, "OUTPUT_ROOT", root), patch.object(oc, "BINARY", str(fake)), patch.object(b, "memory_snapshot", return_value={"pressure": 1}):
            job = self.request(output_path=str(root / "out.txt"))
            b.worker(job["id"])
            result = b.job_status(job["id"])
        self.assertEqual(result["state"], "completed", result["error"])
        self.assertEqual((root / "out.txt").read_text(), "EXACT_OUTPUT_7319")
        self.assertNotIn("EXACT_OUTPUT_7319", json.dumps(result))
        self.assertEqual(json.loads(result["result"])["size_bytes"], 17)

    def test_stream_error_fails_and_denials_remain_visible(self):
        with self.assertRaises(RuntimeError):
            oc.parse(json.dumps({"type": "error", "error": {"message": "quota"}}))
        raw = "\n".join(json.dumps(e) for e in [
            {"type": "text", "sessionID": "test", "part": {"text": "OK"}},
            {"type": "tool_use", "part": {"tool": "shell", "state": {"status": "error", "error": "denied"}}}])
        answer, meta = oc.parse(raw)
        self.assertEqual(answer, "OK")
        self.assertEqual(meta["tool_errors"][0]["error"], "denied")

    def test_worker_returns_deepseek_result_without_another_provider(self):
        fake = self.root / "opencode-cli"
        fake.write_text("#!"+sys.executable+"\nimport sys,json,os\nassert '--auto' not in sys.argv\nassert os.getcwd()==os.environ['PWD']\nsys.stdin.read()\nprint(json.dumps({'type':'text','part':{'text':'piloto OK'}}))\n")
        fake.chmod(0o700)
        job = self.request()
        with patch.object(oc, "BINARY", str(fake)), patch.object(b, "memory_snapshot", return_value={"pressure": 1}):
            b.worker(job["id"])
        result = b.job_status(job["id"])
        self.assertEqual(result["state"], "completed", result["error"])
        self.assertEqual(result["result"], "piloto OK")
        self.assertEqual(result["transcript"][0]["metadata"]["model"], oc.MODEL)

    def test_watchdog_covers_unread_stdin_and_releases_queue(self):
        fake = self.root / "never-reads-stdin"
        fake.write_text("#!"+sys.executable+"\nimport time,subprocess,sys\nsubprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\ntime.sleep(60)\n")
        fake.chmod(0o700)
        job = self.request()
        spec = job["spec"]
        spec.update(timeout=1.0, task="x" * 59000)
        b.update_job(job["id"], spec=b.dumps(spec))
        started = time.monotonic()
        with patch.object(oc, "BINARY", str(fake)), patch.object(b, "memory_snapshot", return_value={"pressure": 1}):
            b.worker(job["id"])
        got = b.job_status(job["id"])
        self.assertEqual(got["state"], "failed", got)
        self.assertIn("TimeoutError", got["error"])
        self.assertLess(time.monotonic() - started, 5)
        self.assertIsNone(got["pid"])
        # A subsequent job must acquire the same serial queue without blocking.
        fake.write_text("#!"+sys.executable+"\nimport json\nprint(json.dumps({'type':'text','part':{'text':'NEXT_OK'}}))\n")
        second = self.request()
        with patch.object(oc, "BINARY", str(fake)), patch.object(b, "memory_snapshot", return_value={"pressure": 1}):
            b.worker(second["id"])
        self.assertEqual(b.job_status(second["id"])["result"], "NEXT_OK")

    def test_preflight_rejects_inaccessible_output_before_queue(self):
        with patch.object(oc, "OUTPUT_ROOT", self.root), patch.object(oc, "check_output_access", side_effect=PermissionError("Documents")):
            with self.assertRaises(PermissionError):
                self.request(output_path=str(self.root / "blocked.txt"))
        with b.database() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)

    def test_emitted_quota_error_stops_waiting_before_cli_exits(self):
        fake = self.root / "quota-error"
        fake.write_text("#!"+sys.executable+"\nimport json,time\nprint(json.dumps({'type':'error','error':{'statusCode':429,'retryAfter':63565000}}),flush=True)\ntime.sleep(60)\n")
        fake.chmod(0o700)
        job=self.request();started=time.monotonic()
        with patch.object(oc, "BINARY", str(fake)), patch.object(b, "memory_snapshot", return_value={"pressure": 1}):
            b.worker(job["id"])
        got=b.job_status(job["id"])
        self.assertEqual(got["state"],"failed")
        self.assertIn('429',got['error'])
        self.assertIn('63565000',got['error'])
        self.assertLess(time.monotonic()-started,5)

    def test_output_preflight_timeout_leaves_status_available(self):
        job=self.request();deadline=b.turn_deadline
        with patch.object(b,'turn_deadline',side_effect=lambda seconds,message:deadline(.05,message)), patch.object(oc,'check_output_access',side_effect=lambda value:time.sleep(3)):
            with self.assertRaisesRegex(TimeoutError,'acceso a la carpeta'):
                b.dispatch('output_check',{'output_path':str(self.root/'probe')},'test','desktop')
        result=b.dispatch('job_status',{'job_id':job['id'],'compact':True},'test','desktop')
        self.assertEqual(result['id'],job['id'])
        self.assertNotIn('task',result['spec'])

    def test_failed_delivery_retains_full_answer_and_exports_without_model(self):
        fake = self.root / "large-answer"
        fake.write_text("#!"+sys.executable+"\nimport json\nprint(json.dumps({'type':'text','part':{'text':'á'*70000}}))\n")
        fake.chmod(0o700)
        with patch.object(oc, "OUTPUT_ROOT", self.root):
            job = self.request(output_path=str(self.root / "out.txt"), request_id="delivery")
            with patch.object(oc, "BINARY", str(fake)), patch.object(b, "memory_snapshot", return_value={"pressure": 1}), patch.object(oc, "write_response", side_effect=PermissionError("revoked")):
                b.worker(job["id"])
            got = b.job_status(job["id"], compact=True)
            self.assertEqual(got["state"], "failed")
            self.assertNotIn("task", got["spec"])
            self.assertEqual(json.loads(got["result"])["size_bytes"], 140000)
            with patch.object(b, "run_turn", side_effect=AssertionError("must not call model")):
                receipt = b.export_job(job["id"])
            self.assertEqual((self.root / "out.txt").read_text(), "á"*70000)
            self.assertFalse(receipt["model_called"])
            # Recovering the existing request is idempotent even once output exists.
            again = self.request(output_path=str(self.root / "out.txt"), request_id="delivery")
            self.assertEqual(again["id"], job["id"])


if __name__ == "__main__":
    unittest.main()
