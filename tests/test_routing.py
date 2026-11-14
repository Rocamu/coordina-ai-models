import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import coordina as b
import live
import routing

ORIGIN = {"family":"codex","session_id":"01a18054-5e8e-7d21-884e-79a909a9907f"}
CHAT = "1ac566e6-f06f-4327-a1ec-a20d9c37d911"
URL = "https://claude.ai/chat/" + CHAT
OTHER = "https://claude.ai/chat/22222222-2222-4222-8222-222222222222"


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="coordina-routing-")
        self.root = Path(self.tmp.name)
        self.work = self.root / "project"
        self.work.mkdir()
        self.env = patch.dict(os.environ, {"COORDINA_HOME":str(self.root/"state")})
        self.env.start()
        self.origin = patch.object(live, "source_context", return_value=ORIGIN.copy())
        self.origin.start()

    def tearDown(self):
        self.origin.stop()
        self.env.stop()
        self.tmp.cleanup()

    def ui(self, **kw):
        args = dict(target="cowork", task="Devuelve la palabra listo.", workspace=str(self.work),
                    session_id=URL, session_hint="DEMO PROJECT CODE", request_id="round-trip", launch=False)
        args.update(kw)
        return live.coordinate(**args)

    def native(self, **kw):
        return live.coordinate("claude", "Consulta", str(self.work), launch=False, **kw)

    def endpoint(self, sid="dest", title="DEMO PROJECT CODE"):
        return dict(family="claude",session_id=sid,title=title,workspace=str(self.work),
                    roots=[str(self.work)],status="idle",live=True)

    def test_cowork_never_maps_to_claude_code(self):
        for name in ("cowork","claude-cowork","claude-web"):
            self.assertEqual(b.backend(name), "cowork")
            with self.assertRaises(ValueError):
                b.start_job(name,"Consulta",str(self.work),launch=False)
        with patch.object(live,"discover") as discover, patch.object(b,"start_job") as spawn:
            got = live.propose("cowork",str(self.work),session_hint="DEMO PROJECT CODE")
            self.assertEqual(got["state"],"ui_required")
            self.assertFalse(got["submitted"])
            discover.assert_not_called()
            self.ui()
            spawn.assert_not_called()

    def test_url_validation_and_artifact_canonicalization(self):
        self.assertEqual(routing.claude_chat(URL+"?artifact=abc#panel"),(CHAT,URL))
        for value in ("https://claude.ai/project/"+CHAT, "http://claude.ai/chat/"+CHAT,
                      "https://claude.ai.evil/chat/"+CHAT, "https://user@claude.ai/chat/"+CHAT,
                      "https://claude.ai:443/chat/"+CHAT,"DEMO PROJECT CODE"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                routing.claude_chat(value)

    def test_unknown_origin_can_deliver_and_return_by_job_id(self):
        with patch.object(live,"source_context",return_value={"family":"unknown","session_id":None}), \
             patch.object(live,"discover",return_value=[self.endpoint()]):
            job=self.native(session_id="dest",request_id="unknown-native")
            self.assertEqual(job["spec"]["route"],"existing")
            reply=live.exchange(job["id"])
            live.reply(job["id"],reply["reply_key"],"Respuesta al origen sin ID")
            self.assertEqual(b.job_status(job["id"])["result"],"Respuesta al origen sin ID")
            ui=self.ui(request_id=None)
            routing.ui_receipt(ui["id"],"submitted",URL,ui["message"])
            routing.ui_receipt(ui["id"],"completed",URL,"COORDINA["+ui["id"]+"] listo")
            with b.database() as c:
                self.assertEqual(c.execute("SELECT count(*) FROM session_bindings").fetchone()[0],0)

    def test_cloud_origin_is_provenance_not_a_local_receiver_requirement(self):
        with patch.object(live,"codex_metadata",side_effect=AssertionError("must not inspect host metadata")), \
             patch.object(live,"discover",side_effect=AssertionError("must not discover the origin")):
            for family in ("codex-cloud","claude-cloud","cowork"):
                s=routing.source(family,"cloud-session-only")
                self.assertEqual(s["session_id"],"cloud-session-only")
                self.assertEqual(s["identity_evidence"],"caller_declared")
                self.assertNotIn("permission_class",s)
        self.assertEqual(routing.source("codex",None)["session_id"],None)

    def test_origin_probe_failure_does_not_block_delivery(self):
        with patch.object(live,"source_context",side_effect=TimeoutError("origin offline")), \
             patch.object(live,"discover",return_value=[self.endpoint()]):
            self.assertEqual(self.native(session_id="dest")["spec"]["route"],"existing")

    def test_cowork_origin_has_no_permission_grant(self):
        with patch.object(live,"source_context",return_value={"family":"unknown","session_id":None}):
            s = routing.source("cowork",URL)
            self.assertEqual(s["session_id"],CHAT)
            self.assertNotIn("permission_class",s)
            with self.assertRaises(ValueError):
                self.ui(source_family="cowork",source_session_id=URL)

    def test_round_trip_is_durable_and_correlated(self):
        job = self.ui()
        self.assertEqual(job["state"],"ui_required")
        self.assertIsNone(job["pid"])
        self.assertEqual(job["spec"]["source"],ORIGIN)
        self.assertEqual(job["spec"]["destination_url"],URL)
        self.assertIn(ORIGIN["session_id"],job["message"])
        # Preparing is not sending and does not bind the destination.
        with b.database() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM session_bindings").fetchone()[0],0)
        submitted = routing.ui_receipt(job["id"],"submitted",URL+"?artifact=any",job["message"])
        self.assertEqual(submitted["state"],"response_pending")
        reply = "COORDINA["+job["id"]+"] listo."
        done = routing.ui_receipt(job["id"],"completed",URL,reply)
        self.assertEqual(done["state"],"completed")
        self.assertEqual(done["result"],reply)
        self.assertEqual(len(done["transcript"]),2)
        again = self.ui(session_id=None,session_hint=None)
        self.assertEqual(again["id"],job["id"])
        self.assertEqual(again["state"],"completed")
        self.assertEqual(routing.ui_receipt(job["id"],"completed",URL,reply)["result"],reply)
        with b.database() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0],1)
        # Resume from a separate process, not Python's in-memory state.
        result = subprocess.run([sys.executable,"-B",b.SCRIPT,"show",job["id"]],
                                capture_output=True,text=True,check=True,env=os.environ.copy())
        self.assertEqual(json.loads(result.stdout)["result"],reply)
        reused = self.ui(request_id="next",session_id=None,session_hint=None)
        self.assertEqual(reused["spec"]["session_id"],CHAT)

    def test_wrong_chat_wrong_marker_and_sent_message_cannot_be_a_reply(self):
        job=self.ui()
        for url,text in ((OTHER,job["message"]),(URL,"COORDINA[wrong] listo")):
            with self.assertRaises(ValueError):
                routing.ui_receipt(job["id"],"submitted",url,text)
        with self.assertRaises(ValueError):
            routing.ui_receipt(job["id"],"completed",URL,"COORDINA["+job["id"]+"] listo")
        routing.ui_receipt(job["id"],"submitted",URL,job["message"])
        with self.assertRaises(ValueError):
            routing.ui_receipt(job["id"],"completed",URL,job["message"])
        with self.assertRaises(ValueError):
            routing.ui_receipt(job["id"],"completed",OTHER,"COORDINA["+job["id"]+"] listo")

    def test_ui_rendering_can_flatten_whitespace_but_not_change_words(self):
        job=self.ui()
        flattened=" ".join(job["message"].split())
        with self.assertRaises(ValueError):
            routing.ui_receipt(job["id"],"submitted",URL,flattened.replace("listo", "otro"))
        got=routing.ui_receipt(job["id"],"submitted",URL,flattened)
        self.assertEqual(got["state"],"response_pending")
        with self.assertRaises(ValueError):
            routing.ui_receipt(job["id"],"completed",URL,flattened)

    def test_uncertain_delivery_cannot_be_reset_to_prepared(self):
        job=self.ui()
        routing.ui_receipt(job["id"],"delivery_unknown",URL,"La interfaz no confirmó el envío.")
        again=self.ui()
        self.assertEqual(again["id"],job["id"])
        self.assertEqual(again["state"],"delivery_unknown")
        done=routing.ui_receipt(job["id"],"completed",URL,"COORDINA["+job["id"]+"] listo")
        self.assertEqual(done["state"],"completed")

    def test_cancelled_ui_job_cannot_be_submitted_or_reset(self):
        job=self.ui()
        cancelled=b.dispatch("job_cancel",{"job_id":job["id"]},"cli","cli")
        self.assertEqual(cancelled["state"],"cancelled")
        self.assertEqual(self.ui()["state"],"cancelled")
        with self.assertRaises(ValueError):
            routing.ui_receipt(job["id"],"submitted",URL,job["message"])

    def test_request_id_conflict_is_rejected_without_sending(self):
        self.ui()
        for args in ({"session_id":OTHER},{"task":"otra tarea"}):
            with self.assertRaises(ValueError):
                self.ui(**args)
        with patch.object(live,"source_context",return_value=dict(ORIGIN,session_id="other-origin")):
            with self.assertRaises(ValueError):
                self.ui()

    def test_stale_binding_cannot_switch_to_another_similar_thread(self):
        with patch.object(live,"discover",return_value=[self.endpoint()]):
            self.native(session_id="dest")
        with patch.object(live,"discover",return_value=[self.endpoint("other")]),patch.object(b,"start_job") as spawn:
            got=self.native()
            self.assertEqual(got["state"],"session_unavailable")
            self.assertEqual(got["session_id"],"dest")
            spawn.assert_not_called()

    def test_exact_name_wins_over_partial_name_but_duplicate_names_need_choice(self):
        with patch.object(live,"discover",return_value=[self.endpoint(),self.endpoint("other","DEMO PROJECT CODE antiguo")]):
            self.assertEqual(self.native(session_hint="DEMO PROJECT CODE")["spec"]["session_id"],"dest")
        with patch.object(live,"discover",return_value=[self.endpoint(),self.endpoint("other")]):
            self.assertEqual(self.native(session_hint="DEMO PROJECT CODE")["state"],"choose_session")

    def test_auto_can_create_without_replacing_a_named_thread(self):
        with patch.object(live,"discover",return_value=[]),patch.object(b,"start_job",return_value={"id":"new"}) as spawn:
            self.assertEqual(self.native()["id"],"new")
            spawn.assert_called_once()
            spawn.reset_mock()
            self.assertEqual(self.native(route="existing")["state"],"no_suitable_session")
            self.assertEqual(self.native(session_hint="DEMO PROJECT CODE")["state"],"no_suitable_session")
            spawn.assert_not_called()
            self.native(route="new")
            self.assertEqual(spawn.call_args.kwargs["source"],ORIGIN)
        with self.assertRaises(ValueError):
            self.native(route="new",session_hint="DEMO PROJECT CODE")

    def test_mcp_accepts_per_call_origin_and_ui_receipt(self):
        schemas={n:s for n,d,s in b.TOOLS}
        self.assertIn("source_session_id",schemas["coordinate"]["properties"])
        with patch.object(live,"source_context",return_value={"family":"unknown","session_id":None}), patch.object(live,"discover",return_value=[]):
            got=b.dispatch("coordinate",dict(target="claude",task="Consulta",workspace=str(self.work),route="existing"),"mcp-id","desktop")
            self.assertEqual(got["state"],"no_suitable_session")
        job=self.ui()
        got=b.dispatch("ui_receipt",dict(job_id=job["id"],state="submitted",observed_url=URL,
                                      observed_text=job["message"]),"mcp-id","desktop")
        self.assertEqual(got["state"],"response_pending")


if __name__ == "__main__":
    unittest.main()

