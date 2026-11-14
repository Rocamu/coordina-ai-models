import json
import os
from pathlib import Path
import socket
import sqlite3
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import coordina as b
import live


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="coordina-native-")
        self.root=Path(self.tmp.name);self.work=self.root/"project";self.work.mkdir()
        self.env=patch.dict(os.environ,{"COORDINA_HOME":str(self.root/"state")});self.env.start()
        self.source=patch.object(live,"source_context",return_value={"family":"claude","session_id":"origin"});self.source.start()
        self.endpoint=dict(family="codex",session_id="destination",title="Trabajo del proyecto",workspace=str(self.work),roots=[str(self.work)],status="open",live=True)

    def tearDown(self):self.source.stop();self.env.stop();self.tmp.cleanup()

    def coordinate(self,**kw):
        return live.coordinate("gpt","Revisa el proyecto",str(self.work),launch=False,**kw)

    def test_proposal_never_starts_a_cli(self):
        with patch.object(live,"discover",return_value=[self.endpoint]),patch.object(b,"start_job") as spawn:
            d=self.coordinate();self.assertEqual(d["state"],"choose_session");spawn.assert_not_called()
            self.assertFalse(d["registration_required"])

    def test_selection_is_remembered_for_this_origin_only(self):
        with patch.object(live,"discover",return_value=[self.endpoint]):
            first=self.coordinate(session_id="destination")
            again=self.coordinate()
            self.assertEqual(first["spec"]["session_id"],again["spec"]["session_id"])
            with patch.object(live,"source_context",return_value={"family":"claude","session_id":"other-origin"}):
                self.assertEqual(self.coordinate()["state"],"choose_session")

    def test_auto_creates_new_only_after_successful_empty_discovery(self):
        other=dict(self.endpoint,workspace=str(self.root/"other"),roots=[str(self.root/"other")],title="Finanzas")
        with patch.object(live,"discover",return_value=[other]),patch.object(b,"start_job",return_value={"id":"new"}) as spawn:
            d=self.coordinate()
            self.assertEqual(d["id"],"new");spawn.assert_called_once()
        with patch.object(live,"discover",side_effect=TimeoutError("owner timeout")),patch.object(b,"start_job") as spawn:
            self.assertEqual(self.coordinate()["state"],"discovery_unavailable");spawn.assert_not_called()

    def test_stale_explicit_destination_does_not_create_another_session(self):
        with patch.object(live,"discover",return_value=[]),patch.object(b,"start_job") as spawn:
            self.assertEqual(self.coordinate(session_id="closed")["state"],"session_unavailable");spawn.assert_not_called()

    def test_request_id_does_not_replay_delivery(self):
        with patch.object(live,"discover",return_value=[self.endpoint]) as discover:
            first=self.coordinate(session_id="destination",request_id="once")
            second=self.coordinate(session_id="destination",request_id="once")
            self.assertEqual(first["id"],second["id"]);self.assertEqual(discover.call_count,1)

    def test_heavy_profile_is_preserved_and_cannot_reuse_light_request(self):
        with patch.object(live,"discover",return_value=[self.endpoint]):
            first=self.coordinate(session_id="destination",request_id="profile",workload="heavy")
            self.assertEqual(first["spec"]["workload"],"heavy")
            with self.assertRaises(ValueError):self.coordinate(session_id="destination",request_id="profile")

    def test_memory_policy_blocks_native_delivery_without_touching_app(self):
        for level,profile in ((2,"heavy"),(3,"light"),(4,"light"),(5,"heavy")):
            with patch.object(live,"discover",return_value=[self.endpoint]):
                job=self.coordinate(session_id="destination",workload=profile)
            with patch.object(b,"memory_snapshot",return_value={"pressure":level}),patch.object(live,"send_codex") as send:
                live.worker(job["id"])
                send.assert_not_called()
            self.assertEqual(b.job_status(job["id"])["state"],"blocked_memory")

    def test_existing_route_without_match_does_not_fork(self):
        with patch.object(live,"discover",return_value=[]),patch.object(b,"start_job") as spawn:
            self.assertEqual(self.coordinate(route="existing")["state"],"no_suitable_session");spawn.assert_not_called()

    def test_named_destination_can_connect_without_extra_selection(self):
        with patch.object(live,"discover",return_value=[self.endpoint]):
            d=self.coordinate(session_hint="Trabajo del proyecto")
            self.assertEqual(d["spec"]["route"],"existing")

    def test_source_conversation_is_excluded(self):
        with patch.object(live,"source_context",return_value={"family":"codex","session_id":"destination"}),patch.object(live,"discover",return_value=[self.endpoint]):
            d=live.propose("codex",str(self.work),"proyecto")
            self.assertEqual(d["candidates"],[])

    def test_old_open_or_named_chat_survives_recent_history_limit(self):
        db=sqlite3.connect(str(self.root/"state_5.sqlite"))
        db.execute("CREATE TABLE threads(id TEXT,title TEXT,name TEXT,cwd TEXT,archived INTEGER,updated_at INTEGER)")
        db.executemany("INSERT INTO threads VALUES(?,?,?,?,?,?)",[(str(i),"Original","Otro "+str(i),str(self.work),0,i+1) for i in range(300)])
        db.execute("INSERT INTO threads VALUES(?,?,?,?,?,?)",("old-open","Original","Álbum antiguo",str(self.work),0,0));db.commit();db.close()
        (self.root/".codex-global-state.json").write_text(json.dumps({"electron-persisted-atom-state":{"thread-user-activity-times-v1":{json.dumps(["local","old-open"]):1}}}))
        with patch.object(live,"codex_home",return_value=self.root):
            for query in ({},{"session_id":"old-open"},{"session_hint":"álbum antiguo"}):
                self.assertEqual([r["session_id"] for r in live.codex_metadata(**query)],["old-open"])

    def test_private_socket_rejects_symlinks_and_other_user_writes(self):
        p=self.root/"private";p.write_text("{}");p.chmod(0o600)
        link=self.root/"link";link.symlink_to(p)
        with self.assertRaises(ValueError):live.private_path(link,"file")
        p.chmod(0o666)
        with self.assertRaises(ValueError):live.private_path(p,"file",True)

    def test_busy_receipt_does_not_reclassify_as_new_session(self):
        class IPC:
            def __init__(self,**kw):pass
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def owner(self,sid):return {"client_id":"owner","untrusted_input":True}
            def request(self,method,params,version,target):
                self_test.assertEqual(version,2)
                self_test.assertEqual(params["turnStart"]["request"]["input"],[])
                self_test.assertEqual(params["turnStart"]["context"]["responseItems"][1]["type"],"function_call_output")
                return {"resultType":"error","error":"App context must wait until the current turn finishes"}
        self_test=self
        with patch.object(live,"NativeIPC",IPC):
            d=live.send_codex(self.endpoint,"peer content","job")
            self.assertEqual(d,{"state":"busy","submitted":False})

    def test_native_reply_is_correlated_and_never_starts_another_turn(self):
        with patch.object(live,"discover",return_value=[self.endpoint]):d=self.coordinate(session_id="destination")
        key=live.exchange(d["id"])["reply_key"]
        with self.assertRaises(ValueError):live.reply(d["id"],"wrong","respuesta")
        result=live.reply(d["id"],key,"respuesta")
        self.assertFalse(result["new_turn_started"])
        self.assertEqual(b.job_status(d["id"])["result"],"respuesta")
        with self.assertRaises(ValueError):live.reply(d["id"],key,"otra respuesta")

    def test_completed_native_job_is_never_delivered_twice(self):
        with patch.object(live,"discover",return_value=[self.endpoint]):d=self.coordinate(session_id="destination")
        b.update_job(d["id"],state="completed",result="done")
        with patch.object(live,"send_codex") as send:
            live.worker(d["id"]);send.assert_not_called()

    def test_claude_transcript_only_returns_new_correlated_final_answer(self):
        sid="12345678-1234-1234-1234-123456789abc"
        p=self.root/"projects/project"/(sid+".jsonl");p.parent.mkdir(parents=True);p.write_text("")
        with patch.object(live,"claude_home",return_value=self.root):reader=live.ClaudeTranscript({"session_id":sid})
        def append(text,reason="end_turn"):
            with p.open("a") as f:f.write(json.dumps({"type":"assistant","message":{"stop_reason":reason,"content":[{"type":"text","text":text}]}})+"\n")
        append("unrelated")
        self.assertIsNone(reader.response("job"))
        append("COORDINA[job] partial","tool_use")
        self.assertIsNone(reader.response("job"))
        append("COORDINA[job] final")
        self.assertEqual(reader.response("job"),"COORDINA[job] final")

    def test_real_native_socket_framing_and_owner_discovery(self):
        directory=self.root/"ipc";directory.mkdir(mode=0o700);path=directory/"ipc.sock"
        server=socket.socket(socket.AF_UNIX);server.bind(str(path));path.chmod(0o600);server.listen(1)
        errors=[]
        def serve():
            try:
                conn,_=server.accept();conn.settimeout(3)
                with conn:
                    def nbytes(n):
                        data=b""
                        while len(data)<n:data+=conn.recv(n-len(data))
                        return data
                    for i in range(2):
                        length=struct.unpack("<I",nbytes(4))[0];p=json.loads(nbytes(length))
                        result={"clientId":"coordina-test"} if i==0 else {"supportsUntrustedAppInput":True}
                        packet={"type":"response","requestId":p["requestId"],"resultType":"success","result":result,"handledByClientId":"owner"}
                        raw=json.dumps(packet).encode();conn.sendall(struct.pack("<I",len(raw))+raw)
            except BaseException as e:errors.append(e)
            finally:server.close()
        t=threading.Thread(target=serve);t.start()
        with live.NativeIPC(path) as client:self.assertTrue(client.owner("some-session")["untrusted_input"])
        t.join(timeout=5);self.assertFalse(t.is_alive());self.assertEqual(errors,[])


class AntigravityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="coordina-agy-")
        self.root=Path(self.tmp.name);self.work=self.root/"project";self.work.mkdir()
        self.env=patch.dict(os.environ,{"COORDINA_HOME":str(self.root/"state")});self.env.start()
        self.source=patch.object(live,"source_context",return_value={"family":"claude","session_id":"origin"});self.source.start()
        self.server=dict(pid=1,procStart="x",exe="/Applications/Antigravity.app/Contents/Resources/bin/language_server",csrf="token",address="127.0.0.1:1",
                         summaries={"conv":self.summary()})

    def tearDown(self):self.source.stop();self.env.stop();self.tmp.cleanup()

    def summary(self,status="CASCADE_RUN_STATUS_IDLE",**kw):
        uri="file://"+str(self.work).replace(" ","%20")
        return dict(dict(summary="Resumen",annotations={"title":"Trabajo del proyecto"},status=status,stepCount=4,lastModifiedTime="2026-10-17T15:41:05.879642Z",
                         workspaces=[{"workspaceFolderAbsoluteUri":uri}],trajectoryMetadata={"projectId":"p1"}),**kw)

    def step(self,kind,text="",status="CORTEX_STEP_STATUS_DONE"):
        if kind=="user":return {"type":"CORTEX_STEP_TYPE_USER_INPUT","status":status,"userInput":{"items":[{"text":text}]}}
        if kind=="agent":return {"type":"CORTEX_STEP_TYPE_SYSTEM_MESSAGE","status":status,"systemMessage":{"agentMessage":{"content":text}}}
        if kind=="tool":return {"type":"CORTEX_STEP_TYPE_RUN_COMMAND","status":status}
        return {"type":"CORTEX_STEP_TYPE_PLANNER_RESPONSE","status":status,"plannerResponse":{"response":text}}

    def test_aliases_select_antigravity(self):
        for name in ("antigravity","Gemini","agy"):self.assertEqual(b.backend(name),"antigravity")
        with self.assertRaises(ValueError):b.backend("bard")

    def test_sessions_map_workspace_title_and_skip_subagents(self):
        servers=[dict(self.server,summaries={"conv":self.summary(),"child":self.summary(trajectoryMetadata={"parentConversationId":"conv"})})]
        rows=live.antigravity_sessions(servers)
        self.assertEqual([r["session_id"] for r in rows],["conv"])
        self.assertEqual(rows[0]["workspace"],str(self.work));self.assertEqual(rows[0]["title"],"Trabajo del proyecto")
        self.assertGreater(rows[0]["updated_at"],0)

    def test_closed_app_is_a_discovery_failure_not_a_reason_for_new_session(self):
        with patch.object(live,"antigravity_servers",return_value=[]),patch.object(b,"start_job") as spawn:
            self.assertEqual(live.propose("antigravity",str(self.work))["state"],"discovery_unavailable")
            d=live.coordinate("gemini","Revisa el proyecto",str(self.work),launch=False)
            self.assertEqual(d["state"],"discovery_unavailable");self.assertFalse(d["new_session_allowed"]);spawn.assert_not_called()

    def test_existing_conversation_is_proposed_before_any_new_one(self):
        with patch.object(live,"antigravity_servers",return_value=[self.server]),patch.object(b,"start_job") as spawn:
            d=live.coordinate("antigravity","Revisa el proyecto",str(self.work),launch=False)
            self.assertEqual(d["state"],"choose_session");self.assertEqual(d["recommended_session_id"],"conv");spawn.assert_not_called()
            d=live.coordinate("antigravity","Revisa el proyecto",str(self.work),launch=False,session_id="conv")
            self.assertEqual(d["spec"]["route"],"existing");self.assertEqual(live.exchange(d["id"])["endpoint"]["family"],"antigravity")

    def test_busy_conversation_waits_and_agentapi_is_not_called(self):
        busy=dict(self.server,summaries={"conv":self.summary("CASCADE_RUN_STATUS_RUNNING")})
        with patch.object(live,"antigravity_servers",return_value=[busy]),patch.object(live,"agentapi") as cli:
            self.assertEqual(live.send_antigravity({"session_id":"conv"},"COORDINA[j] x","j"),{"state":"busy","submitted":False})
            cli.assert_not_called()
        with patch.object(live,"antigravity_servers",return_value=[]):
            self.assertEqual(live.send_antigravity({"session_id":"conv"},"x","j")["state"],"offline")

    def test_delivery_uses_agent_message_cli_and_unconfirmed_is_not_retried(self):
        ok={"response":{"sendMessage":{"recipientId":"conv"}}}
        with patch.object(live,"antigravity_servers",return_value=[self.server]),patch.object(live,"agentapi",return_value=ok) as cli:
            d=live.send_antigravity({"session_id":"conv"},"-COORDINA[j] texto","j")
            self.assertEqual(d,{"state":"accepted","submitted":True,"step_offset":4})
            self.assertEqual(cli.call_args[0][1],["send-message","--title=Coordina","conv","-COORDINA[j] texto"])
        with patch.object(live,"antigravity_servers",return_value=[self.server]),patch.object(live,"agentapi",return_value={"error":"rpc"}):
            self.assertEqual(live.send_antigravity({"session_id":"conv"},"x","j")["state"],"delivery_unknown")

    def test_response_is_correlated_and_bounded_by_next_input(self):
        steps=[self.step("agent","otro"),self.step("plan","respuesta ajena"),self.step("agent","COORDINA[j] encargo"),
               self.step("plan","pensando"),self.step("tool"),self.step("plan","COORDINA[j] final"),
               self.step("user","nuevo turno humano"),self.step("plan","COORDINA[j] no es la respuesta")]
        with patch.object(live,"antigravity_servers",return_value=[self.server]),patch.object(live,"agy_rpc",return_value={"steps":steps}) as rpc:
            r=live.antigravity_response({"session_id":"conv"},{"step_offset":4},"j")
            self.assertEqual(r,{"state":"completed","text":"COORDINA[j] final","marker":True})
            self.assertEqual(rpc.call_args[0][2],{"cascadeId":"conv","stepOffset":4})
            self.assertIsNone(live.antigravity_response({"session_id":"conv"},{},"otro-id"))

    def test_running_turn_is_not_read_early_and_permission_wait_is_reported(self):
        running=dict(self.server,summaries={"conv":self.summary("CASCADE_RUN_STATUS_RUNNING")})
        steps=[self.step("agent","COORDINA[j] encargo"),self.step("plan","COORDINA[j] parcial")]
        with patch.object(live,"antigravity_servers",return_value=[running]),patch.object(live,"agy_rpc",return_value={"steps":steps}):
            self.assertIsNone(live.antigravity_response({"session_id":"conv"},{},"j"))
        with patch.object(live,"antigravity_servers",return_value=[running]),patch.object(live,"agy_rpc",return_value={"steps":steps+[self.step("tool",status="CORTEX_STEP_STATUS_WAITING")]}):
            self.assertEqual(live.antigravity_response({"session_id":"conv"},{},"j"),{"state":"waiting_permission"})

    def test_new_route_opens_one_app_conversation_not_a_hidden_executor(self):
        with patch.object(live,"antigravity_servers",return_value=[self.server]),patch.object(b,"enqueue_job",wraps=b.enqueue_job) as enqueue:
            d=live.coordinate("antigravity","Revisa el proyecto",str(self.work),launch=False,route="new")
            self.assertEqual(enqueue.call_args[1]["launch"],False)
            endpoint=live.exchange(d["id"])["endpoint"];self.assertTrue(endpoint["new_conversation"])
            self.assertEqual(endpoint["project_id"],"outside-of-project")
            with self.assertRaises(ValueError):live.coordinate("antigravity","x",str(self.work),launch=False,route="new",rounds=2)
        created={"response":{"newConversation":{"conversationId":"nueva"}}}
        with patch.object(live,"antigravity_servers",return_value=[self.server]),patch.object(live,"agentapi",return_value=created) as cli:
            r=live.send_antigravity(endpoint,"COORDINA[j] encargo","j")
            self.assertEqual(r["session_id"],"nueva");self.assertEqual(cli.call_args[0][1][-2:],["--","COORDINA[j] encargo"])

    def test_antigravity_message_asks_for_chat_reply_without_tool_approval(self):
        row={"reply_key":"secret-key","endpoint":{"family":"antigravity"}}
        text=live.message("j",{"mode":"consult","task":"Revisa"},row)
        self.assertIn("comenzando exactamente por COORDINA[j]",text);self.assertNotIn("secret-key",text)
        self.assertIn("secret-key",live.message("j",{"mode":"consult","task":"Revisa"},dict(row,endpoint={"family":"claude"})))

    def test_rpc_sends_csrf_to_loopback_and_server_scan_checks_owner(self):
        import http.server
        seen={}
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                seen.update(path=self.path,token=self.headers.get("x-codeium-csrf-token"),body=self.rfile.read(int(self.headers["Content-Length"])))
                raw=json.dumps({"trajectorySummaries":{}}).encode();self.send_response(200);self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw)
            def log_message(self,*a):pass
        httpd=http.server.HTTPServer(("127.0.0.1",0),Handler);t=threading.Thread(target=httpd.handle_request);t.start()
        server=dict(self.server,address="127.0.0.1:"+str(httpd.server_port))
        self.assertEqual(live.agy_rpc(server,"GetAllCascadeTrajectories",{}),{"trajectorySummaries":{}})
        t.join(5);httpd.server_close()
        self.assertEqual(seen["path"],"/exa.language_server_pb.LanguageServerService/GetAllCascadeTrajectories");self.assertEqual(seen["token"],"token")
        exe=self.root/"Antigravity.app/Contents/Resources/bin/language_server";exe.parent.mkdir(parents=True);exe.write_text("");exe.chmod(0o755)
        ps="  10 "+str(os.getuid())+" Fri Oct 17 11:55:12 2026 "+str(exe)+" --standalone --csrf_token abcdef0123456789abcdef\n"
        ps+="  11 "+str(os.getuid()+1)+" Fri Oct 17 11:55:12 2026 "+str(exe)+" --csrf_token abcdef0123456789abcdef\n"
        def run(args,**kw):
            out=ps if args[0]=="/bin/ps" else "p10\nn127.0.0.1:5\nn*:6\n"
            return type("R",(),{"stdout":out,"returncode":0})()
        with patch.object(live.subprocess,"run",side_effect=run),patch.object(live,"agy_rpc",return_value={"trajectorySummaries":{"c":{}}}) as rpc:
            found=live.antigravity_servers()
        self.assertEqual([(s["pid"],s["address"],s["csrf"]) for s in found],[(10,"127.0.0.1:5","abcdef0123456789abcdef")]);self.assertEqual(rpc.call_count,1)


if __name__=="__main__":unittest.main()
